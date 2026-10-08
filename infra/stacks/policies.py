"""IAM documents of every FinanceAgent role (tasks 3.7, 5.1, 5.6, 7.1; FA-POL-01, FA-PRV-08).

Every document is a plain dict built from ``partition``, ``region`` and ``account``: the stacks pass
CloudFormation tokens (``Aws.PARTITION`` ...), so no account literal is ever written to a file; the
unit tests pass placeholders and evaluate requests offline with :func:`finplan_contracts.iam.evaluate`
together with the environment permission boundary (policy simulation).

==========================  ==========  =================================================================
role                        boundary    may
==========================  ==========  =================================================================
runtime (AgentCore Runtime)  env        pull its image; write its own runtime log group; X-Ray; metrics in
                                        ``bedrock-agentcore`` and ``FinPlan/FinanceAgent``; read metrics;
                                        workload access token for the caller's JWT (never ForUserId); its
                                        environment's Memory events; read its own explanation config, the
                                        tool catalog and the shared budget parameters; Bedrock
                                        ``InvokeModel`` / ``InvokeModelWithResponseStream`` ONLY on the
                                        resolved inference profile and its routed foundation models (none
                                        while the provider is ``fixture``). Explicitly denied: any Lambda
                                        invoke and any execute-api call (single tool path, FA-POL-01)
gateway-service              env        invoke this environment's FinanceLambdasTool tool Lambdas (any
                                        alias); evaluate this environment's policy engine
deploy exec (CloudFormation) env        this environment's Cognito pool, secret, AgentCore resources,
                                        runtime log groups and ``finplan-<env>-financeagent-*`` roles (with
                                        the environment boundary only); never another environment's
stage (CodeBuild)            env        publish own SSM references, read env + shared SSM, read own stack
                                        outputs, release ledger, approval record, read-only Bedrock profile
                                        lookup, the ci_test client secret, read-only AgentCore lookups
build (CodeBuild)            shared     push the Runtime image, write the release ledger
==========================  ==========  =================================================================
"""

from __future__ import annotations

from typing import Any

from finplan_contracts import iam as contract_iam

from . import naming as n

__all__ = [
    "tool_catalog_object_arn",
    "BEDROCK_INVOKE_ACTIONS",
    "METRICS_NAMESPACE",
    "build_role_statements",
    "deploy_execution_statements",
    "gateway_role_policy",
    "gateway_trust_conditions",
    "runtime_role_policy",
    "runtime_trust_conditions",
    "stage_role_statements",
]

PARTITION = contract_iam.PARTITION
REGION = contract_iam.REGION
ACCOUNT = contract_iam.ACCOUNT
BEDROCK_INVOKE_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
METRICS_NAMESPACE = "FinPlan/FinanceAgent"
FLT = "financelambdastool"
#: AgentCore payment/wallet actions (contracts live-financial deny list), denied wherever bedrock-agentcore:* is allowed.
LIVE_AGENTCORE_ACTIONS = ("bedrock-agentcore:*Payment*", "bedrock-agentcore:*Wallet*", "bedrock-agentcore:*Funds*")


def _arn(service: str, resource: str, *, partition: str, region: str = "", account: str = "") -> str:
    return f"arn:{partition}:{service}:{region}:{account}:{resource}"


def _param(path: str, *, partition: str, region: str, account: str) -> str:
    return contract_iam.ssm_parameter_arn(path, partition=partition, region=region, account=account)


def tool_catalog_object_arn(env: str, *, partition: str, account: str) -> str:
    """The FinanceLambdasTool catalog objects of ``env`` that its ``tool-catalog`` pointer names (read-only)."""
    return f"arn:{partition}:s3:::finplan-shared-{FLT}-pipeline-store-{account}/releases/*/tool-catalog/{env}.json"


def _others(env: str) -> list[str]:
    return [e for e in n.ENVIRONMENTS if e != env]


def _agentcore_other_env_arns(env: str, *, partition: str, region: str, account: str) -> list[str]:
    out = []
    for o in _others(env):
        for res in (f"runtime/finplan_{o}_*", f"memory/finplan_{o}_*", f"policy-engine/finplan_{o}_*", f"gateway/finplan-{o}-*"):
            out.append(_arn("bedrock-agentcore", res, partition=partition, region=region, account=account))
    return out


# ===================================================================== runtime role
def runtime_trust_conditions(*, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> dict[str, Any]:
    return {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": _arn("bedrock-agentcore", "*", partition=partition, region=region, account=account)}}


def runtime_role_policy(env: str, *, memory_arn: str, bedrock_resources: Any = None, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> dict[str, Any]:
    """Identity policy of ``finplan-<env>-financeagent-runtime-role``.

    ``bedrock_resources`` is the list of resolved ARNs (inference profile plus routed foundation
    models) or a CloudFormation list token; ``None``/empty means no Bedrock statement at all
    (``fixture`` provider: least privilege, FA-PRV-14).
    """
    a = lambda svc, res, reg=True, acct=True: _arn(svc, res, partition=partition, region=region if reg else "", account=account if acct else "")  # noqa: E731
    group = n.runtime_log_group_prefix(env)
    repo = a("ecr", f"repository/{n.ecr_repository_name()}")
    params = [
        n.own_ssm(env, "config", "explanation-provider"),
        n.own_ssm(env, "config", "explanation-model-id"),
        n.own_ssm(env, "config", "explanation-guards"),
        f"/finplan/{env}/{FLT}/contract/tool-catalog",
        "/finplan/shared/financialplanning/config/budget-allocation",
        "/finplan/shared/financialplanning/config/budget-state",
        "/finplan/shared/financialplanning/config/cost-ceiling-usd",
    ]
    st: list[dict[str, Any]] = [
        {"Sid": "PullOwnImage", "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], "Resource": [repo]},
        {"Sid": "EcrToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": ["*"]},
        {"Sid": "OwnRuntimeLogStreams", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [a("logs", f"log-group:{group}*:log-stream:*")]},
        {"Sid": "OwnRuntimeLogGroups", "Effect": "Allow", "Action": ["logs:DescribeLogStreams"], "Resource": [a("logs", f"log-group:{group}*")]},
        {"Sid": "DescribeLogGroups", "Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": [a("logs", "log-group:*")]},
        {"Sid": "Traces", "Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": ["*"]},
        {"Sid": "PutMetrics", "Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": ["*"], "Condition": {"StringEquals": {"cloudwatch:namespace": ["bedrock-agentcore", METRICS_NAMESPACE]}}},
        {"Sid": "ReadUsageMetrics", "Effect": "Allow", "Action": ["cloudwatch:GetMetricData"], "Resource": ["*"]},
        {
            "Sid": "WorkloadTokenForCallerJwt",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT"],
            "Resource": [a("bedrock-agentcore", "workload-identity-directory/default"), a("bedrock-agentcore", f"workload-identity-directory/default/workload-identity/{n.runtime_name(env)}-*")],
        },
        {
            "Sid": "SessionCheckpoints",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:GetEvent", "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:ListSessions"],
            "Resource": [memory_arn],
        },
        {"Sid": "ReadOwnConfiguration", "Effect": "Allow", "Action": ["ssm:GetParameter", "ssm:GetParameters"], "Resource": [_param(p, partition=partition, region=region, account=account) for p in params]},
        {"Sid": "ReadToolCatalogObject", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [tool_catalog_object_arn(env, partition=partition, account=account)]},
        # ---- explicit denies (hold even if an allow above were widened)
        {"Sid": "DenyWorkloadTokenWithoutJwt", "Effect": "Deny", "Action": ["bedrock-agentcore:GetWorkloadAccessTokenForUserId"], "Resource": ["*"]},
        {"Sid": "DenyDirectToolInvoke", "Effect": "Deny", "Action": ["lambda:InvokeFunction", "lambda:InvokeFunctionUrl", "execute-api:Invoke"], "Resource": ["*"]},
        {"Sid": "DenyOtherEnvironmentAgentCore", "Effect": "Deny", "Action": ["bedrock-agentcore:*"], "Resource": _agentcore_other_env_arns(env, partition=partition, region=region, account=account)},
    ]
    if bedrock_resources:
        st.insert(len(st) - 3, {"Sid": "InvokeConfiguredExplanationModel", "Effect": "Allow", "Action": list(BEDROCK_INVOKE_ACTIONS), "Resource": bedrock_resources})
    return {"Version": "2012-10-17", "Statement": st}


# ===================================================================== gateway service role
def gateway_trust_conditions(env: str, *, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> dict[str, Any]:
    return {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": _arn("bedrock-agentcore", f"gateway/{n.gateway_name(env)}-*", partition=partition, region=region, account=account)}}


def gateway_role_policy(env: str, *, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> dict[str, Any]:
    """``finplan-<env>-financeagent-gateway-service-role``: invoke this environment's tool Lambdas only
    (published at ``/finplan/<env>/financeagent/agent/gateway-principal-ref`` for the FinanceLambdasTool
    invoke grant) and evaluate this environment's policy engine."""
    a = lambda svc, res: _arn(svc, res, partition=partition, region=region, account=account)  # noqa: E731
    engine = a("bedrock-agentcore", f"policy-engine/{n.policy_engine_name(env)}-*")
    gateway = a("bedrock-agentcore", f"gateway/{n.gateway_name(env)}-*")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "InvokeSameEnvironmentToolLambdas", "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": [a("lambda", f"function:finplan-{env}-{FLT}-*")]},
            {"Sid": "PolicyEngineConfiguration", "Effect": "Allow", "Action": ["bedrock-agentcore:GetPolicyEngine"], "Resource": [engine]},
            {"Sid": "PolicyEngineAuthorization", "Effect": "Allow", "Action": ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"], "Resource": [engine, gateway]},
            {"Sid": "DenyOtherEnvironmentToolLambdas", "Effect": "Deny", "Action": ["lambda:InvokeFunction"], "Resource": [a("lambda", f"function:finplan-{o}-*") for o in _others(env)]},
        ],
    }


# ===================================================================== pipeline roles
def deploy_execution_statements(env: str, store_bucket_arn: str, *, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> list[dict[str, Any]]:
    """CloudFormation execution role of ``env``: only this environment's FinanceAgent resources; roles
    it creates must carry ``finplan-<env>-permission-boundary``."""
    prefix = f"finplan-{env}-{n.REPO}-"
    a = lambda svc, res, reg=True, acct=True: _arn(svc, res, partition=partition, region=region if reg else "", account=account if acct else "")  # noqa: E731
    role_res = a("iam", f"role/{prefix}*", reg=False)
    boundary = a("iam", f"policy/finplan-{env}-permission-boundary", reg=False)
    group = n.runtime_log_group_prefix(env)
    stmts: list[dict[str, Any]] = [
        {"Sid": "ReadStoreTemplates", "Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": [f"{store_bucket_arn}/*"]},
        {
            "Sid": "CreateEnvUserPool",
            "Effect": "Allow",
            "Action": ["cognito-idp:CreateUserPool", "cognito-idp:TagResource"],
            "Resource": ["*"],
            "Condition": {"StringEquals": {"aws:RequestTag/environment": env, "aws:RequestTag/owner-repo": n.REPO}},
        },
        {"Sid": "ManageEnvUserPool", "Effect": "Allow", "Action": ["cognito-idp:*"], "Resource": [a("cognito-idp", "userpool/*")]},
        {"Sid": "UserPoolDomains", "Effect": "Allow", "Action": ["cognito-idp:CreateUserPoolDomain", "cognito-idp:DeleteUserPoolDomain", "cognito-idp:DescribeUserPoolDomain", "cognito-idp:UpdateUserPoolDomain"], "Resource": ["*"]},
        {"Sid": "EnvSecrets", "Effect": "Allow", "Action": ["secretsmanager:*"], "Resource": [a("secretsmanager", f"secret:{n.secret_name(env, '*')}")]},
        {"Sid": "RandomPassword", "Effect": "Allow", "Action": ["secretsmanager:GetRandomPassword"], "Resource": ["*"]},
        {"Sid": "AgentCoreResources", "Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": [a("bedrock-agentcore", "*")]},
        {"Sid": "AgentCoreServiceLinkedRoles", "Effect": "Allow", "Action": ["iam:CreateServiceLinkedRole"], "Resource": ["*"], "Condition": {"StringLike": {"iam:AWSServiceName": "*bedrock-agentcore.amazonaws.com"}}},
        {"Sid": "ReadRuntimeImage", "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"], "Resource": [a("ecr", f"repository/{n.ecr_repository_name()}")]},
        {"Sid": "EcrToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": ["*"]},
        {"Sid": "EnvRolesCreateWithBoundary", "Effect": "Allow", "Action": ["iam:CreateRole", "iam:PutRolePermissionsBoundary"], "Resource": [role_res], "Condition": {"StringEquals": {"iam:PermissionsBoundary": boundary}}},
        {
            "Sid": "EnvRolesManage",
            "Effect": "Allow",
            "Action": [
                "iam:GetRole",
                "iam:GetRolePolicy",
                "iam:ListRolePolicies",
                "iam:ListAttachedRolePolicies",
                "iam:ListRoleTags",
                "iam:DeleteRole",
                "iam:PutRolePolicy",
                "iam:DeleteRolePolicy",
                "iam:UpdateRole",
                "iam:UpdateRoleDescription",
                "iam:UpdateAssumeRolePolicy",
                "iam:TagRole",
                "iam:UntagRole",
            ],
            "Resource": [role_res],
        },
        {"Sid": "PassEnvRolesToAgentCore", "Effect": "Allow", "Action": ["iam:PassRole"], "Resource": [role_res], "Condition": {"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}},
        {"Sid": "RuntimeLogGroups", "Effect": "Allow", "Action": ["logs:*"], "Resource": [a("logs", f"log-group:{group}*"), a("logs", f"log-group:{group}*:*")]},
        {"Sid": "LogGroupsDescribe", "Effect": "Allow", "Action": ["logs:DescribeLogGroups", "logs:ListTagsForResource"], "Resource": ["*"]},
        {"Sid": "DenyOtherEnvironmentAgentCore", "Effect": "Deny", "Action": ["bedrock-agentcore:*"], "Resource": _agentcore_other_env_arns(env, partition=partition, region=region, account=account)},
        {"Sid": "DenyLiveFinancialAgentCore", "Effect": "Deny", "Action": list(LIVE_AGENTCORE_ACTIONS), "Resource": ["*"]},
    ]
    stmts += list(contract_iam.ssm_access_policy(n.REPO, env, partition=partition, region=region, account=account)["Statement"])
    return stmts


def stage_role_statements(env: str, store_bucket_arn: str, *, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> list[dict[str, Any]]:
    """Promotion check, reference resolution, release publisher and deployed test runner of ``env``."""
    own = f"/finplan/{env}/{n.REPO}"
    p = lambda path: _param(path, partition=partition, region=region, account=account)  # noqa: E731
    a = lambda svc, res: _arn(svc, res, partition=partition, region=region, account=account)  # noqa: E731
    return [
        {
            "Sid": "PublishOwnReferences",
            "Effect": "Allow",
            "Action": ["ssm:PutParameter", "ssm:AddTagsToResource"],
            "Resource": [
                p(f"{own}/release/*"),
                p(f"{own}/agent/*"),
                p(f"{own}/secret-ref/ci-test-client"),
                p(f"{own}/config/explanation-provider"),
                p(f"{own}/config/explanation-model-id"),
                p(f"{own}/config/explanation-guards"),
                p(f"{own}/config/budget-enforced-role-names"),
            ],
        },
        {"Sid": "ReadEnvAndShared", "Effect": "Allow", "Action": list(contract_iam.SSM_READ_ACTIONS), "Resource": [p(f"/finplan/{env}"), p(f"/finplan/{env}/*"), p("/finplan/shared"), p("/finplan/shared/*")]},
        {"Sid": "ReadStackOutputs", "Effect": "Allow", "Action": ["cloudformation:DescribeStacks"], "Resource": [a("cloudformation", f"stack/finplan-{env}-{n.REPO}-*/*")]},
        {"Sid": "ReleaseLedger", "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": [f"{store_bucket_arn}/releases/*"]},
        {"Sid": "ReadToolCatalogObject", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [tool_catalog_object_arn(env, partition=partition, account=account)]},
        {"Sid": "ApprovalRecord", "Effect": "Allow", "Action": ["codepipeline:ListActionExecutions", "codepipeline:GetPipelineExecution"], "Resource": [a("codepipeline", n.PIPELINE_NAME)]},
        {"Sid": "ResolveExplanationModel", "Effect": "Allow", "Action": ["bedrock:GetInferenceProfile", "bedrock:GetFoundationModel", "bedrock:ListInferenceProfiles"], "Resource": ["*"]},
        {"Sid": "CiTestClientSecret", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], "Resource": [a("secretsmanager", f"secret:{n.secret_name(env, 'ci-test-client')}*")]},
        {
            "Sid": "ReadOwnAgentCore",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetGateway",
                "bedrock-agentcore:ListGatewayTargets",
                "bedrock-agentcore:GetGatewayTarget",
                "bedrock-agentcore:GetAgentRuntime",
                "bedrock-agentcore:GetPolicyEngine",
                "bedrock-agentcore:ListPolicies",
                "bedrock-agentcore:GetPolicy",
                "bedrock-agentcore:GetMemory",
            ],
            "Resource": [a("bedrock-agentcore", "*")],
        },
        {"Sid": "DenyOtherEnvironmentAgentCore", "Effect": "Deny", "Action": ["bedrock-agentcore:*"], "Resource": _agentcore_other_env_arns(env, partition=partition, region=region, account=account)},
        # Deployed suites reach tools only through the Gateway (single tool path, FA-POL-01).
        {"Sid": "DenyDirectToolInvoke", "Effect": "Deny", "Action": ["lambda:InvokeFunction"], "Resource": [a("lambda", f"function:finplan-*-{FLT}-*")]},
        {"Sid": "DenyBedrockInvoke", "Effect": "Deny", "Action": list(BEDROCK_INVOKE_ACTIONS) + ["bedrock:Converse", "bedrock:ConverseStream"], "Resource": ["*"]},
    ]


def build_role_statements(store_bucket_arn: str, *, partition: str = PARTITION, region: str = REGION, account: str = ACCOUNT) -> list[dict[str, Any]]:
    """Build stage: push the Runtime image (built once), write the release ledger. No Bedrock (FA-PRV-14)."""
    repo = _arn("ecr", f"repository/{n.ecr_repository_name()}", partition=partition, region=region, account=account)
    return [
        {"Sid": "ReleaseLedger", "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": [f"{store_bucket_arn}/releases/*"]},
        {"Sid": "ListStore", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": [store_bucket_arn], "Condition": {"StringLike": {"s3:prefix": ["releases/*"]}}},
        {
            "Sid": "PushImage",
            "Effect": "Allow",
            "Action": ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload", "ecr:DescribeImages", "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart"],
            "Resource": [repo],
        },
        {"Sid": "EcrToken", "Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": ["*"]},
        {"Sid": "DenyBedrockInvoke", "Effect": "Deny", "Action": list(BEDROCK_INVOKE_ACTIONS) + ["bedrock:Converse", "bedrock:ConverseStream"], "Resource": ["*"]},
    ]
