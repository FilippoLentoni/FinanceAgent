"""The FinanceAgent pipeline (task 7.1; spec agent-release-pipeline; contracts D6 pipeline standard;
design D7). Part of the account-level tooling stack, so only the authenticated bootstrap creates or
changes it; the pipeline never updates itself.

CodePipeline **V2** with CodeBuild, stages in the contract order:

1. **Source** - CodeConnections source of ``FilippoLentoni/FinanceAgent`` ``main``; the connection is
   the EXISTING, reused one, referenced through ``/finplan/shared/financeagent/config/codeconnection-ref``
   (written by the bootstrap; never an ARN in a file).
2. **Build** (ARM64 CodeBuild, privileged for docker) - ``scripts/build_stage.py``: pre-synth gates,
   ``cdk synth`` ONCE, post-synth gates (``scripts/infra_gates.py``), the Runtime image built ONCE for
   ``linux/arm64``, import-checked (lesson L3) and pushed by digest, a new ``release_id``, the artifact
   digest (assembly + image digest). Exports ``RELEASE_ID`` and ``IMAGE_URI`` (namespace
   ``BuildVariables``); its single artifact ``BuildOutput`` is the only input of every later action.
   ``rollback_to_release_id`` re-emits a recorded release instead (no rebuild).
3. **Beta / Gamma / Prod** (prod after the manual approval), each:
   ``Resolve`` (stage role): promotion check (gamma/prod refuse a 0.x pin), the FinanceLambdasTool
   compatibility gate and target resolution from THIS environment's release manifest, tool catalog
   and ``lambda/<tool>-arn`` references, and the Bedrock grant resolution (``GetInferenceProfile``,
   read-only) - exported as variables (namespace ``Resolve<Env>``), never as an artifact;
   ``DeployIdentity`` then ``DeployAgent`` (scoped deploy role + CloudFormation execution role; the
   agent stack gets ``ImageUri``/``ReleaseId`` from the Build variables and the targets/Bedrock ARNs
   from the Resolve variables); ``PublishRelease`` (SSM references, explanation configuration,
   budget-enforced role names, release manifest with the policy digest and target set);
   the deployed suite (``IntegrationBetaTests`` / ``GammaTests`` / ``SmokeTests``) sending REAL
   payloads to the deployed Runtime and Gateway as the stage role (lesson L5).

Until the bootstrap's source-stage dry run has passed, the transition into Build is disabled.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
from aws_cdk import Annotations, Aws, CfnCondition, CfnParameter, Duration, Fn
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_ssm as ssm
from finplan_contracts import ssm as contract_ssm

from . import naming as n
from .agent import AgentStack, bedrock_param, target_param, target_variable
from .common import tag_role
from .policies import build_role_statements, deploy_execution_statements, stage_role_statements
from .tooling import ToolingStack, add_log_group, get_tooling_stack

__all__ = ["BUILD_NAMESPACE", "CONNECTION_PARAMETER", "ENV_SUITES", "STAGE_NAMES", "add_pipeline", "add_to_app", "build_spec", "resolve_namespace", "stage_spec", "template_path"]

ROLLBACK_VARIABLE = "rollback_to_release_id"
NO_ROLLBACK = "none"
SOURCE_STAGE = "Source"
BUILD_STAGE = "Build"
STAGE_NAMES = (SOURCE_STAGE, BUILD_STAGE, "Beta", "Gamma", "Approval", "Prod")
ENV_SUITES = {"beta": "integration-beta", "gamma": "gamma", "prod": "smoke"}
CONNECTION_PARAMETER = contract_ssm.build(contract_ssm.SHARED, n.REPO, "config", "codeconnection-ref")
DEFAULT_GITHUB_REPOSITORY = "FilippoLentoni/FinanceAgent"
UV_VERSION = "0.12.23"
BUILD_NAMESPACE = "BuildVariables"
BUILD_VARIABLES = ("RELEASE_ID", "IMAGE_URI")
BEDROCK_VARIABLE = "BEDROCK_INVOKE_ARNS"


def resolve_namespace(env: str) -> str:
    return f"Resolve{env.capitalize()}"


def _stmts(docs: list[dict[str, Any]]) -> list[iam.PolicyStatement]:
    return [iam.PolicyStatement.from_json(d) for d in docs]


def _install() -> dict[str, Any]:
    return {"runtime-versions": {"python": "3.12", "nodejs": "22"}, "commands": [f'python3 -m pip install --quiet "uv=={UV_VERSION}"', "uv --version"]}


def build_spec() -> dict[str, Any]:
    return {
        "version": "0.2",
        "env": {"shell": "bash", "variables": {"UV_LINK_MODE": "copy", "CDK_DISABLE_VERSION_CHECK": "1", "FINPLAN_RELEASE_BUILD": "1"}, "exported-variables": list(BUILD_VARIABLES)},
        "phases": {
            "install": _install(),
            "build": {
                "commands": [
                    "uv sync --locked",
                    'uv run python scripts/build_stage.py --out build-output --source-commit "$SOURCE_COMMIT" --rollback-to "$ROLLBACK_TO_RELEASE_ID" --store "$FINPLAN_PIPELINE_STORE" --image-repository "$FINPLAN_IMAGE_REPOSITORY" --variables build.env',
                    "set -a; . ./build.env; set +a",
                    'test -n "$RELEASE_ID" && test -n "$IMAGE_URI"',
                ]
            },
        },
        "artifacts": {"base-directory": "build-output", "files": ["**/*"]},
        "cache": {"paths": ["/root/.cache/uv/**/*", "/root/.npm/**/*"]},
    }


def stage_spec(tools: list[str]) -> dict[str, Any]:
    """Post-deploy actions run from BuildOutput only (never the source checkout, never a synth)."""
    exported = [target_variable(t) for t in tools] + [BEDROCK_VARIABLE]
    return {
        "version": "0.2",
        "env": {"shell": "bash", "variables": {"UV_LINK_MODE": "copy"}, "exported-variables": exported},
        "phases": {
            "install": _install(),
            "build": {
                "commands": [
                    "uv sync --locked",
                    'uv run python scripts/stage_runner.py "$FINPLAN_STAGE_ACTION" --env "$FINPLAN_ENV" --release-info release-info.json --pipeline-execution-id "$PIPELINE_EXECUTION_ID" --store "$FINPLAN_PIPELINE_STORE" --variables resolved.env',
                    "if [ -f resolved.env ]; then set -a; . ./resolved.env; set +a; fi",
                ]
            },
        },
        "cache": {"paths": ["/root/.cache/uv/**/*"]},
    }


def template_path(stage: cdk.Stage, stack: cdk.Stack) -> str:
    return f"cdk.out/{stage.artifact_id}/{stack.template_file}"


class PipelineResources:
    def __init__(self, tooling: ToolingStack) -> None:
        self.tooling = tooling
        self.pipeline: codepipeline.Pipeline | None = None
        self.store: s3.IBucket | None = None
        self.roles: dict[str, iam.Role] = {}
        self.projects: dict[str, codebuild.PipelineProject] = {}


def _role(scope: ToolingStack, cid: str, name: str, logical: str, principal: iam.IPrincipal, description: str, statements: list[iam.PolicyStatement] | None = None) -> iam.Role:
    role = iam.Role(scope, cid, role_name=name, assumed_by=principal, description=description)
    for st in statements or []:
        role.add_to_principal_policy(st)
    tag_role(role, logical)
    scope.register_enforced_role(role)
    return role


def _logging(res: PipelineResources, cid: str, project_name: str) -> codebuild.LoggingOptions:
    group = add_log_group(res.tooling, cid, f"/aws/codebuild/{project_name}", "pipeline-build-project")
    return codebuild.LoggingOptions(cloud_watch=codebuild.CloudWatchLoggingOptions(log_group=group))


def add_pipeline(tooling: ToolingStack, stages: Mapping[str, Any], shared: Mapping[str, Any]) -> PipelineResources:
    res = PipelineResources(tooling)
    st = tooling
    source_cfg = shared.get("source") or {}
    github = str(source_cfg.get("repository") or DEFAULT_GITHUB_REPOSITORY)
    branch = str(source_cfg.get("branch") or "main")
    owner, repo_name = github.split("/", 1)
    p, r, a = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID

    dry_run_passed = CfnParameter(
        st, "SourceDryRunPassed", type="String", default="false", allowed_values=["false", "true"], description="true once the bootstrap's source-stage dry run fetched main; until then the transition into Build is disabled."
    )
    dry_run_cond = CfnCondition(st, "SourceDryRunPassedCondition", expression=Fn.condition_equals(dry_run_passed.value_as_string, "true"))
    store = s3.Bucket.from_bucket_name(st, "Store", n.pipeline_store_bucket_name(a))
    res.store = store

    pipeline_role = _role(st, "PipelineRole", n.shared_name("pipeline", "role"), "pipeline-role", iam.ServicePrincipal("codepipeline.amazonaws.com"), "CodePipeline service role of the FinanceAgent pipeline")
    build_role = _role(
        st,
        "BuildRole",
        n.shared_name("pipeline-build-project", "role"),
        "pipeline-role",
        iam.ServicePrincipal("codebuild.amazonaws.com"),
        "Build stage: gates, synth, ARM64 Runtime image build and push, release ledger",
        _stmts(build_role_statements(store.bucket_arn, partition=p, region=r, account=a)),
    )
    res.roles.update(pipeline=pipeline_role, build=build_role)

    env_common = {"FINPLAN_PIPELINE_STORE": codebuild.BuildEnvironmentVariable(value=store.bucket_name)}
    # ARM64 build host: the AgentCore Runtime requires an arm64 image (built natively, no emulation).
    build_env = codebuild.BuildEnvironment(build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0, compute_type=codebuild.ComputeType.SMALL, privileged=True)
    stage_env = codebuild.BuildEnvironment(build_image=codebuild.LinuxBuildImage.AMAZON_LINUX_2023_5, compute_type=codebuild.ComputeType.SMALL, privileged=False)
    build_project_name = n.shared_name("pipeline-build-project")
    build_project = codebuild.PipelineProject(
        st,
        "BuildProject",
        project_name=build_project_name,
        role=build_role,
        environment=build_env,
        environment_variables={**env_common, "FINPLAN_IMAGE_REPOSITORY": codebuild.BuildEnvironmentVariable(value=tooling.repository.repository_name)},
        build_spec=codebuild.BuildSpec.from_object(build_spec()),
        timeout=Duration.minutes(45),
        cache=codebuild.Cache.local(codebuild.LocalCacheMode.CUSTOM, codebuild.LocalCacheMode.DOCKER_LAYER),
        logging=_logging(res, "BuildProjectLogGroup", build_project_name),
        description="Build stage: gates, cdk synth, ARM64 Runtime image (built once, by digest), release_id",
    )
    tag_role(build_project, "pipeline-build-project")
    res.projects["build"] = build_project

    source_output = codepipeline.Artifact("SourceOutput")
    build_output = codepipeline.Artifact("BuildOutput")
    connection_arn = ssm.StringParameter.value_for_string_parameter(st, CONNECTION_PARAMETER)
    source = actions.CodeStarConnectionsSourceAction(action_name="Source", owner=owner, repo=repo_name, branch=branch, connection_arn=connection_arn, output=source_output, trigger_on_push=True, variables_namespace="SourceVariables")
    build = actions.CodeBuildAction(
        action_name="BuildAndTest",
        project=build_project,
        input=source_output,
        outputs=[build_output],
        type=actions.CodeBuildActionType.BUILD,
        variables_namespace=BUILD_NAMESPACE,
        environment_variables={"SOURCE_COMMIT": codebuild.BuildEnvironmentVariable(value=source.variables.commit_id), "ROLLBACK_TO_RELEASE_ID": codebuild.BuildEnvironmentVariable(value=f"#{{variables.{ROLLBACK_VARIABLE}}}")},
    )
    pipeline = codepipeline.Pipeline(
        st,
        "Pipeline",
        pipeline_name=n.PIPELINE_NAME,
        pipeline_type=codepipeline.PipelineType.V2,
        artifact_bucket=store,
        role=pipeline_role,
        cross_account_keys=False,
        restart_execution_on_update=False,
        use_pipeline_role_for_actions=True,
        variables=[codepipeline.Variable(variable_name=ROLLBACK_VARIABLE, default_value=NO_ROLLBACK, description="Set to a recorded release_id to redeploy its stored artifacts without rebuilding (contracts D6).")],
        stages=[codepipeline.StageProps(stage_name=SOURCE_STAGE, actions=[source]), codepipeline.StageProps(stage_name=BUILD_STAGE, actions=[build])],
    )
    tag_role(pipeline, "pipeline")
    res.pipeline = pipeline
    for env in n.ENVIRONMENTS:
        if env == "prod":
            pipeline.add_stage(
                stage_name="Approval",
                actions=[
                    actions.ManualApprovalAction(
                        action_name="ApproveProd", additional_information="Approve promotion of this FinanceAgent release to prod after the gamma tests passed. The approver and time are recorded in the prod release manifest."
                    )
                ],
            )
        _add_env_stage(res, env, stages[env], build_output, env_common, stage_env)
    cfn = pipeline.node.default_child
    assert isinstance(cfn, codepipeline.CfnPipeline)
    cfn.add_property_override("DisableInboundStageTransitions", Fn.condition_if(dry_run_cond.logical_id, Aws.NO_VALUE, [{"StageName": BUILD_STAGE, "Reason": "finplan bootstrap: the source-stage dry run has not passed yet"}]))
    cdk.CfnOutput(st, "PipelineName", value=n.PIPELINE_NAME)
    cdk.CfnOutput(st, "ImageRepositoryName", value=tooling.repository.repository_name)
    return res


def _add_env_stage(res: PipelineResources, env: str, ctx: Any, build_output: codepipeline.Artifact, env_common: dict[str, codebuild.BuildEnvironmentVariable], stage_env: codebuild.BuildEnvironment) -> None:
    st = res.tooling
    cap = env.capitalize()
    assert res.store is not None and res.pipeline is not None
    p, r, a = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID
    deploy_role = _role(st, f"DeployRole{cap}", n.deploy_role_name(env), "deploy-role", iam.ArnPrincipal(res.roles["pipeline"].role_arn), f"Scoped {env} deploy action role (CodePipeline assumes it)")
    exec_role = _role(
        st,
        f"DeployExecRole{cap}",
        n.exec_role_name(env),
        "deploy-role",
        iam.ServicePrincipal("cloudformation.amazonaws.com"),
        f"CloudFormation execution role for the {env} FinanceAgent stacks",
        _stmts(deploy_execution_statements(env, res.store.bucket_arn, partition=p, region=r, account=a)),
    )
    stage_role = _role(
        st,
        f"StageRole{cap}",
        n.stage_role_name(env),
        "pipeline-role",
        iam.ServicePrincipal("codebuild.amazonaws.com"),
        f"{env} resolver, release publisher and deployed test runner",
        _stmts(stage_role_statements(env, res.store.bucket_arn, partition=p, region=r, account=a)),
    )
    for role in (deploy_role, exec_role, stage_role):
        ToolingStack.scope_to_environment(role, env)
    res.roles.update({f"deploy-{env}": deploy_role, f"exec-{env}": exec_role, f"stage-{env}": stage_role})

    agent: AgentStack = ctx.stacks["agent"]
    identity = ctx.stacks["identity"]
    project_name = n.shared_name("pipeline-build-project", f"{env}-stage")
    project = codebuild.PipelineProject(
        st,
        f"StageProject{cap}",
        project_name=project_name,
        role=stage_role,
        environment=stage_env,
        environment_variables={**env_common, "FINPLAN_ENV": codebuild.BuildEnvironmentVariable(value=env)},
        build_spec=codebuild.BuildSpec.from_object(stage_spec(agent.tools)),
        timeout=Duration.minutes(30),
        cache=codebuild.Cache.local(codebuild.LocalCacheMode.CUSTOM),
        logging=_logging(res, f"StageProject{cap}LogGroup", project_name),
        description=f"{env}: promotion check and resolution, release publishing and the {ENV_SUITES[env]} suite",
    )
    tag_role(project, "pipeline-build-project")
    res.projects[env] = project

    stage = ctx.stage
    execution_id = codebuild.BuildEnvironmentVariable(value="#{codepipeline.PipelineExecutionId}")
    ns = resolve_namespace(env)

    def cb(name: str, action: str, order: int, kind: actions.CodeBuildActionType, namespace: str | None = None) -> actions.CodeBuildAction:
        return actions.CodeBuildAction(
            action_name=name,
            project=project,
            input=build_output,
            type=kind,
            run_order=order,
            variables_namespace=namespace,
            environment_variables={"FINPLAN_STAGE_ACTION": codebuild.BuildEnvironmentVariable(value=action), "PIPELINE_EXECUTION_ID": execution_id},
        )

    overrides = {"ImageUri": f"#{{{BUILD_NAMESPACE}.IMAGE_URI}}", "ReleaseId": f"#{{{BUILD_NAMESPACE}.RELEASE_ID}}", bedrock_param(): f"#{{{ns}.{BEDROCK_VARIABLE}}}"}
    for tool in agent.tools:
        overrides[target_param(tool)] = f"#{{{ns}.{target_variable(tool)}}}"

    def deploy(stack: cdk.Stack, order: int, params: dict[str, str] | None = None) -> actions.CloudFormationCreateUpdateStackAction:
        return actions.CloudFormationCreateUpdateStackAction(
            action_name=f"Deploy{stack.node.id}",
            stack_name=stack.stack_name,
            template_path=build_output.at_path(template_path(stage, stack)),
            admin_permissions=False,
            role=deploy_role,
            deployment_role=exec_role,
            cfn_capabilities=[cdk.CfnCapabilities.NAMED_IAM, cdk.CfnCapabilities.AUTO_EXPAND],
            replace_on_failure=False,
            parameter_overrides=params,
            run_order=order,
        )

    stage_actions: list[codepipeline.IAction] = [
        cb("Resolve", "resolve", 1, actions.CodeBuildActionType.BUILD, ns),
        deploy(identity, 2),
        deploy(agent, 3, overrides),
        cb("PublishRelease", "publish", 4, actions.CodeBuildActionType.BUILD),
    ]
    suite = ENV_SUITES[env]
    stage_actions.append(cb("".join(part.capitalize() for part in suite.split("-")) + "Tests", "tests", 5, actions.CodeBuildActionType.TEST))
    res.pipeline.add_stage(stage_name=cap, actions=stage_actions)


def add_to_app(app: cdk.App, shared: Mapping[str, Any], stages: Mapping[str, Any]) -> None:
    tooling = get_tooling_stack(app, shared)
    missing = [e for e in n.ENVIRONMENTS if e not in stages]
    if missing:
        Annotations.of(tooling).add_warning_v2("finplan:pipeline-skipped", f"pipeline not synthesized: environments {missing} are not selected (-c envs=...)")
        return
    add_pipeline(tooling, stages, shared)
