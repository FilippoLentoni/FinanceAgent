"""Names of every FinanceAgent resource (contracts naming ``finplan-<env>-<repo>-<logical>``).

One place for the names that the IaC, the release publisher, the stage runner, the bootstrap, the
FinanceLambdasTool invoke grant and the tests must agree on. Names only: no account ID, ARN, pool,
client, gateway or memory identifier is ever written to a file. Values that need the account are
built at deploy time from CloudFormation pseudo parameters or at run time from the caller's account.

AgentCore resource names have stricter patterns than IAM/S3 (verified 2026-10-08 against the
CloudFormation reference):

=====================  ===============================  ======================================
resource               pattern                          name used
=====================  ===============================  ======================================
Runtime                ``[a-zA-Z][a-zA-Z0-9_]{0,47}``    ``finplan_<env>_financeagent``
Memory                 ``[a-zA-Z][a-zA-Z0-9_]{0,47}``    ``finplan_<env>_financeagent_sessions``
PolicyEngine / Policy  ``[A-Za-z][A-Za-z0-9_]*`` (<=48)  ``finplan_<env>_financeagent_tools`` /
                                                        ``finplan_<env>_fa_<tool>``
Gateway                ``([0-9a-zA-Z][-]?){1,48}``       ``finplan-<env>-financeagent-gateway``
GatewayTarget          ``([0-9a-zA-Z][-]?){1,100}``      ``<tool-with-dashes>`` (no underscores)
=====================  ===============================  ======================================

The Gateway therefore shows tools as ``<tool-with-dashes>___<tool>`` (FinanceLambdasTool strips the
``<target>___`` prefix; so does the agent's MCP client).
"""

from __future__ import annotations

from finplan_contracts import boundaries as contract_boundaries
from finplan_contracts import ssm as contract_ssm

__all__ = [
    "ENVIRONMENTS",
    "IMAGE_REPOSITORY",
    "LOG_RETENTION_DAYS",
    "PIPELINE_NAME",
    "REPO",
    "RUNTIME_ENDPOINT",
    "agent_stack_name",
    "budget_enforced_role_names",
    "deploy_role_name",
    "ecr_repository_name",
    "env_name",
    "exec_role_name",
    "gateway_name",
    "classical_gateway_name",
    "classical_policy_engine_name",
    "gateway_role_name",
    "identity_stack_name",
    "memory_name",
    "own_ssm",
    "pipeline_store_bucket_name",
    "policy_engine_name",
    "policy_name",
    "role_name",
    "runtime_log_group_prefix",
    "runtime_name",
    "runtime_role_name",
    "secret_name",
    "shared_name",
    "stage_role_name",
    "target_name",
    "tooling_role_names",
    "user_pool_domain_prefix",
    "user_pool_name",
]

REPO = "financeagent"
ENVIRONMENTS = ("beta", "gamma", "prod")
#: Retention of every log group FinanceAgent declares (lesson L6: 30 days).
LOG_RETENTION_DAYS = 30
#: The Runtime endpoint the agent is invoked through (created by the service with every Runtime).
RUNTIME_ENDPOINT = "DEFAULT"
IMAGE_REPOSITORY = "runtime-images"
RESOURCE_SERVER = "finplan-agent"


def env_name(env: str, logical: str, suffix: str | None = None) -> str:
    """``finplan-<env>-financeagent-<logical>[-<suffix>]``."""
    return contract_boundaries.resource_name(env, REPO, logical, suffix)


def shared_name(logical: str, suffix: str | None = None) -> str:
    """``finplan-shared-financeagent-<logical>[-<suffix>]``."""
    return contract_boundaries.resource_name(contract_ssm.SHARED, REPO, logical, suffix)


def role_name(env: str, logical: str) -> str:
    name = env_name(env, logical, "role")
    if len(name) > 64:  # pragma: no cover - guarded by a unit test over every name
        raise ValueError(f"role name {name!r} exceeds 64 characters")
    return name


def identity_stack_name(env: str) -> str:
    return f"finplan-{env}-{REPO}-identity"


def agent_stack_name(env: str) -> str:
    return f"finplan-{env}-{REPO}-agent"


def _underscored(env: str, logical: str) -> str:
    name = f"finplan_{env}_{REPO}" + (f"_{logical}" if logical else "")
    if len(name) > 48:  # pragma: no cover
        raise ValueError(f"{name!r} exceeds 48 characters")
    return name


def runtime_name(env: str) -> str:
    return _underscored(env, "")


def memory_name(env: str) -> str:
    return _underscored(env, "sessions")


def policy_engine_name(env: str) -> str:
    return _underscored(env, "tools")


def policy_name(env: str, tool: str) -> str:
    name = f"finplan_{env}_fa_{tool}"
    if len(name) > 48:  # pragma: no cover
        raise ValueError(f"{name!r} exceeds 48 characters")
    return name


def gateway_name(env: str) -> str:
    return env_name(env, "gateway")


def classical_gateway_name(env: str) -> str:
    return env_name(env, "classical-gateway")


def classical_policy_engine_name(env: str) -> str:
    return _underscored(env, "classical_tools")


def target_name(tool: str) -> str:
    """Gateway target name of ``tool`` (target names allow no underscores)."""
    return tool.replace("_", "-")


def runtime_role_name(env: str) -> str:
    return role_name(env, "runtime")


def gateway_role_name(env: str) -> str:
    """The per-environment Gateway service role (created by the bootstrap, design D2 / GAP-3);
    FinanceLambdasTool admits ``finplan-<env>-financeagent-*`` names of at most 40 suffix characters."""
    return role_name(env, "gateway-service")


def runtime_log_group_prefix(env: str) -> str:
    """Runtime log groups are ``/aws/bedrock-agentcore/runtimes/<runtime-id>-<endpoint>``; the
    runtime ID starts with the runtime name."""
    return f"/aws/bedrock-agentcore/runtimes/{runtime_name(env)}-"


def user_pool_name(env: str) -> str:
    return env_name(env, "users")


def user_pool_domain_prefix(env: str, account: str) -> str:
    """Hosted-domain prefix (token endpoint); ``account`` is a deploy-time token, never a literal."""
    return f"finplan-{env}-{REPO}-{account}"


def secret_name(env: str, logical: str) -> str:
    """``finplan/<env>/financeagent/<logical>`` (the boundaries deny other environments' ``finplan/<env>/*``)."""
    return f"finplan/{env}/{REPO}/{logical}"


def ecr_repository_name() -> str:
    return shared_name(IMAGE_REPOSITORY)


def pipeline_store_bucket_name(account: str) -> str:
    return f"{shared_name('pipeline-store')}-{account}"


PIPELINE_NAME = shared_name("pipeline")


def deploy_role_name(env: str) -> str:
    return shared_name("deploy-role", env)


def exec_role_name(env: str) -> str:
    return shared_name("deploy-role", f"{env}-exec")


def stage_role_name(env: str) -> str:
    return role_name(env, "pipeline-stage")


def tooling_role_names() -> list[str]:
    """Account-level tooling roles, published by the bootstrap at
    ``/finplan/shared/financeagent/config/budget-enforced-role-names`` (contracts D16)."""
    return [shared_name("pipeline", "role"), shared_name("pipeline-build-project", "role")]


def budget_enforced_role_names(env: str) -> list[str]:
    """Per-environment roles the platform's 100% budget deny must cover (contracts D4, design D4 item 5).

    The Runtime role is first: the deny stops its Bedrock invocations (FA-PRV-09). The Gateway service
    role is listed so the deny also stops tool invocations through the Gateway.
    """
    return [runtime_role_name(env), gateway_role_name(env), deploy_role_name(env), exec_role_name(env), stage_role_name(env)]


def own_ssm(env: str, category: str, name: str) -> str:
    return contract_ssm.build(env, REPO, category, name)
