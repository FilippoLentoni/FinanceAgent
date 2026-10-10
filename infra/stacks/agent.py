"""Per-environment agent stack ``finplan-<env>-financeagent-agent`` (tasks 3.7, 4.4, 5.1-5.4, 5.6; design
D1-D4; matrix row ``agentcore-runtime-and-gateway``).

Resources (CloudFormation types verified against the AWS reference on 2026-10-08; all are L1
constructs of ``aws_cdk.aws_bedrockagentcore`` in aws-cdk-lib 2.270, so no custom resource is used):

* ``AWS::BedrockAgentCore::Memory`` - short-term session events only (no long-term strategy,
  FA-SS-04); event expiry from ``config/<env>.json`` ``session.memory_event_expiry_days``.
* ``AWS::BedrockAgentCore::PolicyEngine`` + one ``AWS::BedrockAgentCore::Policy`` per registered tool
  (``policy/tool-policy.yaml`` rendered to Cedar, :mod:`infra.stacks.tool_policy`).
* Two ``AWS::BedrockAgentCore::Gateway`` endpoints - primary/PPO and traditional portfolio analysis.
  Each has its own ``ENFORCE`` policy engine and catalog; both use ``CUSTOM_JWT`` from the SAME
  environment's Cognito pool and the explicitly trusted bootstrap-created Gateway service role.
* ``AWS::BedrockAgentCore::GatewayTarget`` - one per contract tool, each behind a condition: a tool is
  registered only when the stage's ``Resolve`` action found it in THIS environment's FinanceLambdasTool
  catalog (compatibility gate) and passed its alias-qualified Lambda ARN read from
  ``/finplan/<env>/financelambdastool/lambda/<tool>-arn`` (never a literal). The parameter pattern
  admits only ``finplan-<env>-financelambdastool-*`` functions of this environment.
* ``AWS::BedrockAgentCore::Runtime`` - the ARM64 image BY DIGEST (``ImageUri`` from the Build stage),
  ``PUBLIC`` network, HTTP protocol, ``customJWTAuthorizer`` from the same pool, request header
  allowlist ``Authorization`` (the agent forwards the caller's own token to the Gateway), lifecycle from
  config, environment ``FINPLAN_ENV`` / ``FINPLAN_RELEASE_ID`` / ``FINPLAN_MEMORY_ID`` /
  ``FINPLAN_GATEWAY_URL`` / ``FINPLAN_CLASSICAL_GATEWAY_URL``.
* the Runtime execution role ``finplan-<env>-financeagent-runtime-role`` (:func:`infra.stacks.policies.runtime_role_policy`);
  its Bedrock grant is a separate policy that exists only when ``BedrockInvokeArns`` is not ``none``
  (resolved at deploy time from the model-id configuration by ``GetInferenceProfile``; FA-PRV-08).
* the Runtime log group ``/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`` with 30-day retention
  (the role cannot create log groups, so the service never creates an unbounded one).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
from aws_cdk import Aws, CfnCondition, CfnParameter, Fn
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from . import naming as n
from .common import EnvStack, cfn_tags, tag_role
from .identity import IdentityStack
from .policies import runtime_role_policy, runtime_trust_conditions
from .tool_policy import CLASSICAL_POLICY_FILE, load_policy, policy_digest, render
from .tool_schemas import classical_tools, contract_tools, primary_tools, target_metadata, to_cfn, tool_definition

__all__ = ["AgentStack", "bedrock_param", "image_pattern", "target_param", "target_variable"]

NONE = "none"


def _camel(tool: str) -> str:
    return "".join(p.capitalize() for p in tool.split("_"))


def target_param(tool: str) -> str:
    return f"Target{_camel(tool)}Arn"


def target_variable(tool: str) -> str:
    """Exported variable of the stage's Resolve action carrying the tool's Lambda reference."""
    return f"TARGET_{tool.upper()}"


def bedrock_param() -> str:
    return "BedrockInvokeArns"


def image_pattern() -> str:
    return r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/" + n.ecr_repository_name() + r"@sha256:[0-9a-f]{64}$"


def lambda_pattern(env: str) -> str:
    return rf"^({NONE}|arn:aws:lambda:[a-z0-9-]+:[0-9]{{12}}:function:finplan-{env}-financelambdastool-[A-Za-z0-9_-]+(:[A-Za-z0-9_-]+)?)$"


def _policy_resource_name(env: str, tool: str, suffix: str) -> str:
    name = f"finplan_{env}_fa_{tool}_{suffix}"
    if len(name) <= 48:
        return name
    h = hashlib.sha256(name.encode()).hexdigest()[:6]
    return name[: 48 - 7] + "_" + h


class AgentStack(EnvStack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, cfg: Mapping[str, Any], identity: IdentityStack, **kwargs: Any) -> None:
        region = str(cfg.get("region", "us-east-2"))
        super().__init__(
            scope, construct_id, env_name=env_name, region=region, description=f"FinanceAgent {env_name}: AgentCore Runtime, Gateway with tool targets and policy engine, Memory", stack_name=n.agent_stack_name(env_name), **kwargs
        )
        env = env_name
        self.add_stack_dependency(identity)
        session = dict(cfg.get("session") or {})
        gw_cfg = dict(cfg.get("gateway") or {})
        p, r, a = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID

        # ------------------------------------------------------------- deploy-time parameters
        self.image_uri = CfnParameter(self, "ImageUri", type="String", allowed_pattern=image_pattern(), description="Runtime image BY DIGEST (Build stage output; never a tag)")
        self.release_id = CfnParameter(self, "ReleaseId", type="String", allowed_pattern=r"^rel_[0-7][0-9A-HJKMNP-TV-Z]{25}$", description="release_id of the deployed build")
        self.bedrock_arns = CfnParameter(
            self, bedrock_param(), type="CommaDelimitedList", default=NONE, description="Resolved inference-profile and routed foundation-model ARNs of the configured explanation model; 'none' while the provider is fixture"
        )
        has_bedrock = CfnCondition(self, "HasBedrockGrant", expression=Fn.condition_not(Fn.condition_equals(Fn.select(0, self.bedrock_arns.value_as_list), NONE)))

        # ------------------------------------------------------------- memory (short-term only)
        expiry = int(session.get("memory_event_expiry_days", 30))
        if session.get("long_term_memory_strategies"):
            raise ValueError("long-term memory strategies are not allowed in phase 1 (FA-SS-04)")
        self.memory = agentcore.CfnMemory(self, "Memory", name=n.memory_name(env), event_expiry_duration=expiry, description=f"FinanceAgent {env} session checkpoints (short-term events only)", tags=cfn_tags(env, "agent-memory"))

        # ------------------------------------------------------------- policy engine + gateway
        self.policy_engine = agentcore.CfnPolicyEngine(self, "ToolPolicyEngine", name=n.policy_engine_name(env), description=f"FinanceAgent {env} tool policy (policy/tool-policy.yaml)")
        tag_role(self.policy_engine, "gateway-policy-engine")
        gateway_role_arn = f"arn:{p}:iam::{a}:role/{n.gateway_role_name(env)}"
        self.gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name=n.gateway_name(env),
            description=f"FinanceAgent {env} MCP gateway to the FinanceLambdasTool tools",
            protocol_type="MCP",
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(supported_versions=[str(gw_cfg.get("mcp_protocol_version", "2025-11-25"))])),
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(discovery_url=identity.discovery_url, allowed_clients=identity.allowed_clients)
            ),
            role_arn=gateway_role_arn,
            policy_engine_configuration=agentcore.CfnGateway.GatewayPolicyEngineConfigurationProperty(arn=self.policy_engine.attr_policy_engine_arn, mode=str(gw_cfg.get("policy_mode", "ENFORCE"))),
            tags=cfn_tags(env, "agent-gateway"),
        )

        # Independent traditional catalog and policy engine. The original Gateway IDs,
        # target names and authorization document remain unchanged.
        self.classical_policy_engine = agentcore.CfnPolicyEngine(
            self, "ClassicalToolPolicyEngine", name=n.classical_policy_engine_name(env),
            description=f"FinanceAgent {env} traditional portfolio analysis policy",
        )
        tag_role(self.classical_policy_engine, "gateway-policy-engine")
        self.classical_gateway = agentcore.CfnGateway(
            self, "ClassicalGateway", name=n.classical_gateway_name(env),
            description=f"FinanceAgent {env} traditional portfolio optimization and explanation MCP",
            protocol_type="MCP",
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(supported_versions=[str(gw_cfg.get("mcp_protocol_version", "2025-11-25"))])
            ),
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(discovery_url=identity.discovery_url, allowed_clients=identity.allowed_clients)
            ),
            role_arn=gateway_role_arn,
            policy_engine_configuration=agentcore.CfnGateway.GatewayPolicyEngineConfigurationProperty(
                arn=self.classical_policy_engine.attr_policy_engine_arn, mode=str(gw_cfg.get("policy_mode", "ENFORCE"))
            ),
            tags=cfn_tags(env, "agent-gateway"),
        )

        # ------------------------------------------------------------- targets + policies
        self.tools = contract_tools()  # union exported by Resolve/CodePipeline exactly once
        self.primary_tools = primary_tools()
        self.classical_tools = classical_tools()
        self.definitions = {t: tool_definition(t) for t in self.tools}
        self.target_params: dict[str, CfnParameter] = {}
        conditions: dict[str, CfnCondition] = {}
        for tool in self.tools:
            param = CfnParameter(
                self, target_param(tool), type="String", default=NONE,
                allowed_pattern=lambda_pattern(env),
                description=f"{tool}: alias-qualified Lambda ARN from /finplan/{env}/financelambdastool/lambda/{tool.replace('_', '-')}-arn (Resolve action), or none",
            )
            self.target_params[tool] = param
            conditions[tool] = CfnCondition(self, f"Register{_camel(tool)}", expression=Fn.condition_not(Fn.condition_equals(param.value_as_string, NONE)))
        self.targets: dict[str, agentcore.CfnGatewayTarget] = {}
        self.classical_targets: dict[str, agentcore.CfnGatewayTarget] = {}
        for prefix, tools, gateway, engine, policy_doc, targets in (
            ("", self.primary_tools, self.gateway, self.policy_engine, load_policy(), self.targets),
            ("Classical", self.classical_tools, self.classical_gateway, self.classical_policy_engine, load_policy(CLASSICAL_POLICY_FILE), self.classical_targets),
        ):
            rendered = render(tools, {t: self.definitions[t].input_schema.get("properties", {}) for t in tools}, policy_doc)
            if prefix:
                self.classical_policy_digest = policy_digest(rendered)
            else:
                self.policy_digest = policy_digest(rendered)
            predecessor = NONE
            for tool in tools:
                d = self.definitions[tool]
                param, cond = self.target_params[tool], conditions[tool]
                target = agentcore.CfnGatewayTarget(
                    self, f"{prefix}Target{_camel(tool)}", gateway_identifier=gateway.attr_gateway_identifier,
                    # Gateway action-schema updates share a catalog. Serialize
                    # targets within each Gateway, while the two Gateways remain
                    # independent. Conditional references preserve sparse catalogs:
                    # DependsOn an omitted target would suppress later resources.
                    name=n.target_name(tool),
                    description=Fn.join("", [d.description[:140], " [catalog predecessor: ", predecessor, "]"]),
                    credential_provider_configurations=[agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(credential_provider_type="GATEWAY_IAM_ROLE")],
                    target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                        mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                            lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(lambda_arn=param.value_as_string, tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(inline_payload=[]))
                        )
                    ),
                )
                target.add_property_override("TargetConfiguration.Mcp.Lambda.ToolSchema.InlinePayload", [{"Name": tool, "Description": d.description, "InputSchema": to_cfn(d.input_schema), "OutputSchema": to_cfn(d.output_schema)}])
                metadata = target_metadata(env, tool, classical=bool(prefix))
                if metadata:
                    target.metadata_configuration = agentcore.CfnGatewayTarget.MetadataConfigurationProperty(allowed_request_headers=metadata["allowedRequestHeaders"])
                target.cfn_options.condition = cond
                target.add_metadata("logical-role", "gateway-target")
                targets[tool] = target
                predecessor = cdk.Token.as_string(Fn.condition_if(cond.logical_id, target.attr_target_id, predecessor))

            # Policy creation performs a schema check against the Gateway's
            # complete action catalog. Each policy therefore waits for every
            # enabled target, rather than just its own concurrently created target.
            # A harmless Cedar comment carries conditional resource references;
            # disabled optional targets do not become hard DependsOn dependencies.
            # The final enabled target transitively depends on its predecessors.
            # Reference that selector once per policy instead of repeating the
            # full catalog, keeping the deployable template comfortably bounded.
            catalog_ids = predecessor
            for tool in tools:
                target, cond = targets[tool], conditions[tool]
                for pol in [x for x in rendered if x.tool == tool]:
                    cp = agentcore.CfnPolicy(
                        self, f"{prefix}Policy{_camel(tool)}{_camel(pol.name)}",
                        name=_policy_resource_name(env, ("classical_" if prefix else "") + tool, pol.name),
                        policy_engine_id=engine.attr_policy_engine_id,
                        description=f"{pol.kind} {tool} ({env}; policy/{'classical-' if prefix else ''}tool-policy.yaml)",
                        definition=agentcore.CfnPolicy.PolicyDefinitionProperty(cedar=agentcore.CfnPolicy.CedarPolicyProperty(statement=Fn.sub(
                            pol.statement + "\n// Registered catalog readiness: ${CatalogTargetIds}\n",
                            {"GatewayArn": gateway.attr_gateway_arn, "CatalogTargetIds": catalog_ids},
                        ))),
                        enforcement_mode="ACTIVE", validation_mode="IGNORE_ALL_FINDINGS",
                    )
                    cp.cfn_options.condition = cond
                    cp.node.add_dependency(target)
                    cp.add_metadata("logical-role", "gateway-policy-engine")

        # ------------------------------------------------------------- runtime role
        memory_arn = self.memory.attr_memory_arn
        self.runtime_role = iam.Role(
            self,
            "RuntimeRole",
            role_name=n.runtime_role_name(env),
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", conditions=runtime_trust_conditions(partition=p, region=r, account=a)),
            description=f"FinanceAgent {env} AgentCore Runtime execution role (no tool or platform permissions)",
            inline_policies={"runtime": iam.PolicyDocument.from_json(runtime_role_policy(env, memory_arn=memory_arn, partition=p, region=r, account=a))},
        )
        tag_role(self.runtime_role, "agent-runtime")
        bedrock_policy = iam.CfnPolicy(
            self,
            "RuntimeBedrockInvoke",
            policy_name="explanation-model-invoke",
            roles=[self.runtime_role.role_name],
            policy_document={
                "Version": "2012-10-17",
                "Statement": [{"Sid": "InvokeConfiguredExplanationModel", "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": self.bedrock_arns.value_as_list}],
            },
        )
        bedrock_policy.cfn_options.condition = has_bedrock

        # ------------------------------------------------------------- runtime
        life = agentcore.CfnRuntime.LifecycleConfigurationProperty(idle_runtime_session_timeout=int(session.get("idle_runtime_session_timeout_seconds", 900)), max_lifetime=int(session.get("max_lifetime_seconds", 28800)))
        self.runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=n.runtime_name(env),
            description=f"FinanceAgent {env} LangGraph agent",
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(container_uri=self.image_uri.value_as_string)),
            role_arn=self.runtime_role.role_arn,
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
            protocol_configuration="HTTP",
            authorizer_configuration=agentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(discovery_url=identity.discovery_url, allowed_clients=identity.allowed_clients)
            ),
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(request_header_allowlist=["Authorization"]),
            lifecycle_configuration=life,
            environment_variables={
                "FINPLAN_ENV": env,
                "FINPLAN_RELEASE_ID": self.release_id.value_as_string,
                "FINPLAN_MEMORY_ID": self.memory.attr_memory_id,
                "FINPLAN_GATEWAY_URL": self.gateway.attr_gateway_url,
                "FINPLAN_CLASSICAL_GATEWAY_URL": self.classical_gateway.attr_gateway_url,
            },
            tags=cfn_tags(env, "agent-runtime"),
        )
        # the role (and its inline policy) must exist before the service validates the image pull
        self.runtime.node.add_dependency(self.runtime_role)
        self.log_group = logs.CfnLogGroup(self, "RuntimeLogGroup", log_group_name=Fn.join("", ["/aws/bedrock-agentcore/runtimes/", self.runtime.attr_agent_runtime_id, f"-{n.RUNTIME_ENDPOINT}"]), retention_in_days=n.LOG_RETENTION_DAYS)
        self.log_group.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        outputs = {
            "RuntimeArn": self.runtime.attr_agent_runtime_arn,
            "RuntimeId": self.runtime.attr_agent_runtime_id,
            "RuntimeRoleName": n.runtime_role_name(env),
            "GatewayUrl": self.gateway.attr_gateway_url,
            "GatewayId": self.gateway.attr_gateway_identifier,
            "GatewayArn": self.gateway.attr_gateway_arn,
            "MemoryId": self.memory.attr_memory_id,
            "PolicyEngineArn": self.policy_engine.attr_policy_engine_arn,
            "PolicyDigest": self.policy_digest,
            "ClassicalGatewayUrl": self.classical_gateway.attr_gateway_url,
            "ClassicalGatewayId": self.classical_gateway.attr_gateway_identifier,
            "ClassicalGatewayArn": self.classical_gateway.attr_gateway_arn,
            "ClassicalPolicyEngineArn": self.classical_policy_engine.attr_policy_engine_arn,
            "ClassicalPolicyDigest": self.classical_policy_digest,
        }
        for k, v in outputs.items():
            cdk.CfnOutput(self, k, value=v)
