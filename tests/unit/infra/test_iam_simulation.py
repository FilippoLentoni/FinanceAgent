"""Offline IAM policy simulation of every FinanceAgent role WITH its environment permission boundary
(FA-POL-01, FA-PRV-08 incl. a profile routed to three US regions, FA-PRV-14, ENV-03, ENV-05)."""

from __future__ import annotations

import pytest
from finplan_contracts.boundaries import env_permission_boundary
from finplan_contracts.iam import Request, evaluate

from infra.stacks import policies as pol
from scripts.release import bedrock_grant

#: A syntactically valid, obviously fake account ID built at run time (no 12-digit literal in files).
FAKE_ACCOUNT = "0" * 12
OTHER_ACCOUNT = "1" * 12

P, R, A = "aws", "us-east-2", FAKE_ACCOUNT
MEMORY = f"arn:aws:bedrock-agentcore:{R}:{A}:memory/finplan_beta_financeagent_sessions-abcdefghij"
STORE = f"arn:aws:s3:::finplan-shared-financeagent-pipeline-store-{A}"
PROFILE_ID = "fixture.profile-routed-to-three-regions"


class FakeBedrock:
    """``GetInferenceProfile`` of a fixture US profile routed to three US regions (no network)."""

    def __init__(self, regions=("us-east-1", "us-east-2", "us-west-2"), status="ACTIVE", fm="fixture-vendor.fixture-model-v1"):
        self.regions, self.status, self.fm = regions, status, fm
        self.calls = []

    def get_inference_profile(self, inferenceProfileIdentifier):  # noqa: N803 - boto3 casing
        self.calls.append(inferenceProfileIdentifier)
        return {"inferenceProfileArn": f"arn:aws:bedrock:{R}:{A}:inference-profile/{inferenceProfileIdentifier}", "status": self.status, "models": [{"modelArn": f"arn:aws:bedrock:{r}::foundation-model/{self.fm}"} for r in self.regions]}


def _boundary(env="beta"):
    return env_permission_boundary(env, partition=P, region=R, account=A)


def _cfg(model_id, kind="bedrock"):
    return {"environment": "gamma", "provider_kind_allowed": ["fixture", "bedrock"], "explanation": {"provider": kind, "model_id": model_id}}


def _allowed(policy, action, resource, env="beta", **ctx):
    return evaluate(Request(action, resource, ctx), [policy], _boundary(env)).allowed


@pytest.fixture
def us_profile(monkeypatch):
    # the fixture profile ID must pass the agent's model-id validation and start with "us."
    return "us." + PROFILE_ID


def test_runtime_role_bedrock_grant_is_exactly_the_profile_and_its_three_regional_models(us_profile):
    fake = FakeBedrock()
    arns = bedrock_grant(_cfg(us_profile), fake, region=R)
    assert len(arns) == 4 and fake.calls == [us_profile]
    doc = pol.runtime_role_policy("beta", memory_arn=MEMORY, bedrock_resources=arns, partition=P, region=R, account=A)
    for arn in arns:
        assert _allowed(doc, "bedrock:InvokeModel", arn) and _allowed(doc, "bedrock:InvokeModelWithResponseStream", arn)
    other = "arn:aws:bedrock:us-east-1::foundation-model/other-vendor.other-model-v1"
    assert not _allowed(doc, "bedrock:InvokeModel", other)
    assert not _allowed(doc, "bedrock:CreateModelInvocationJob", arns[0])
    assert not _allowed(doc, "bedrock:PutFoundationModelEntitlement", "*")  # model access stays a human/bootstrap step


def test_fixture_provider_gets_no_bedrock_permission():
    assert bedrock_grant(_cfg("us.anything", kind="fixture"), None, region=R) == []
    doc = pol.runtime_role_policy("beta", memory_arn=MEMORY, bedrock_resources=None, partition=P, region=R, account=A)
    assert not _allowed(doc, "bedrock:InvokeModel", f"arn:aws:bedrock:{R}::foundation-model/x.y")


def test_global_and_non_us_profiles_are_refused(us_profile):
    from scripts.release import ManifestError

    with pytest.raises(ManifestError):
        bedrock_grant(_cfg("global." + PROFILE_ID), FakeBedrock(), region=R)
    with pytest.raises(ManifestError):
        bedrock_grant(_cfg("eu." + PROFILE_ID), FakeBedrock(), region=R)
    with pytest.raises(ManifestError, match="outside US"):
        bedrock_grant(_cfg(us_profile), FakeBedrock(regions=("us-east-1", "eu-west-1")), region=R)
    with pytest.raises(ManifestError):
        bedrock_grant(_cfg(us_profile), FakeBedrock(status="LEGACY"), region=R)


def test_runtime_role_has_no_tool_or_platform_path_and_no_user_id_tokens():
    doc = pol.runtime_role_policy("beta", memory_arn=MEMORY, bedrock_resources=["arn:aws:bedrock:us-east-2::foundation-model/x.y-1"], partition=P, region=R, account=A)
    assert not _allowed(doc, "lambda:InvokeFunction", f"arn:aws:lambda:{R}:{A}:function:finplan-beta-financelambdastool-get-plan:current")
    assert not _allowed(doc, "execute-api:Invoke", f"arn:aws:execute-api:{R}:{A}:abc/v1/GET/v1/plans/x")
    assert not _allowed(doc, "bedrock-agentcore:GetWorkloadAccessTokenForUserId", f"arn:aws:bedrock-agentcore:{R}:{A}:workload-identity-directory/default")
    assert _allowed(doc, "bedrock-agentcore:GetWorkloadAccessTokenForJWT", f"arn:aws:bedrock-agentcore:{R}:{A}:workload-identity-directory/default/workload-identity/finplan_beta_financeagent-abc")
    assert _allowed(doc, "bedrock-agentcore:CreateEvent", MEMORY) and _allowed(doc, "bedrock-agentcore:DeleteEvent", MEMORY)
    assert not _allowed(doc, "bedrock-agentcore:CreateEvent", MEMORY.replace("beta", "gamma"))
    assert _allowed(doc, "ssm:GetParameter", f"arn:aws:ssm:{R}:{A}:parameter/finplan/beta/financeagent/config/explanation-provider")
    assert _allowed(doc, "ssm:GetParameter", f"arn:aws:ssm:{R}:{A}:parameter/finplan/beta/financelambdastool/contract/tool-catalog")
    assert not _allowed(doc, "ssm:GetParameter", f"arn:aws:ssm:{R}:{A}:parameter/finplan/gamma/financeagent/config/explanation-provider")
    assert _allowed(doc, "logs:PutLogEvents", f"arn:aws:logs:{R}:{A}:log-group:/aws/bedrock-agentcore/runtimes/finplan_beta_financeagent-abc-DEFAULT:log-stream:x")
    assert _allowed(doc, "cloudwatch:PutMetricData", "*", **{"cloudwatch:namespace": "FinPlan/FinanceAgent"})
    assert not _allowed(doc, "cloudwatch:PutMetricData", "*", **{"cloudwatch:namespace": "AWS/Other"})


def test_runtime_and_stage_roles_read_only_their_environments_catalog_object():
    """The deployed tool-catalog pointer names an object in the FinanceLambdasTool store (read-only, own env)."""
    obj = f"arn:aws:s3:::finplan-shared-financelambdastool-pipeline-store-{A}/releases/rel_01M4DEV4W1NTDNPV1E0DCWQ0JJ/tool-catalog/beta.json"
    runtime = pol.runtime_role_policy("beta", memory_arn=MEMORY, bedrock_resources=None, partition=P, region=R, account=A)
    stage = {"Version": "2012-10-17", "Statement": pol.stage_role_statements("beta", STORE, partition=P, region=R, account=A)}
    for doc in (runtime, stage):
        assert _allowed(doc, "s3:GetObject", obj)
        assert not _allowed(doc, "s3:GetObject", obj.replace("/beta.json", "/gamma.json"))
        assert not _allowed(doc, "s3:PutObject", obj)
        assert not _allowed(doc, "s3:GetObject", obj.replace("tool-catalog/beta.json", "build-output.zip"))


def test_gateway_role_invokes_only_this_environments_tools():
    doc = pol.gateway_role_policy("gamma", partition=P, region=R, account=A)
    own = f"arn:aws:lambda:{R}:{A}:function:finplan-gamma-financelambdastool-get-plan:current"
    assert _allowed(doc, "lambda:InvokeFunction", own, env="gamma")
    assert not _allowed(doc, "lambda:InvokeFunction", own.replace("gamma", "prod"), env="gamma")
    assert not _allowed(doc, "lambda:InvokeFunction", f"arn:aws:lambda:{R}:{A}:function:finplan-gamma-financeagent-x", env="gamma")
    engine = f"arn:aws:bedrock-agentcore:{R}:{A}:policy-engine/finplan_gamma_financeagent_tools-abcdefghij"
    assert _allowed(doc, "bedrock-agentcore:AuthorizeAction", engine, env="gamma") and _allowed(doc, "bedrock-agentcore:GetPolicyEngine", engine, env="gamma")


def test_deploy_exec_role_creates_roles_only_with_the_environment_boundary():
    docs = {"Version": "2012-10-17", "Statement": pol.deploy_execution_statements("beta", STORE, partition=P, region=R, account=A)}
    role = f"arn:aws:iam::{A}:role/finplan-beta-financeagent-runtime-role"
    ok = {"iam:PermissionsBoundary": f"arn:aws:iam::{A}:policy/finplan-beta-permission-boundary"}
    assert _allowed(docs, "iam:CreateRole", role, **ok)
    assert not _allowed(docs, "iam:CreateRole", role, **{"iam:PermissionsBoundary": f"arn:aws:iam::{A}:policy/other"})
    assert not _allowed(docs, "iam:CreateRole", role.replace("beta", "prod"), **ok)
    assert _allowed(docs, "iam:PassRole", role, **{"iam:PassedToService": "bedrock-agentcore.amazonaws.com"})
    assert not _allowed(docs, "iam:PassRole", role, **{"iam:PassedToService": "lambda.amazonaws.com"})
    assert _allowed(docs, "bedrock-agentcore:CreateGateway", f"arn:aws:bedrock-agentcore:{R}:{A}:gateway/finplan-beta-financeagent-gateway-abcdefghij")
    assert not _allowed(docs, "bedrock-agentcore:UpdateGateway", f"arn:aws:bedrock-agentcore:{R}:{A}:gateway/finplan-prod-financeagent-gateway-abcdefghij")
    assert not _allowed(docs, "bedrock-agentcore:CreatePaymentManager", "*")  # live financial (ENV-05)
    assert _allowed(docs, "secretsmanager:CreateSecret", f"arn:aws:secretsmanager:{R}:{A}:secret:finplan/beta/financeagent/ci-test-client-AbCdEf")
    assert not _allowed(docs, "secretsmanager:CreateSecret", f"arn:aws:secretsmanager:{R}:{A}:secret:finplan/gamma/financeagent/ci-test-client-AbCdEf")


def test_stage_and_build_roles_never_invoke_bedrock_or_tools_directly():
    stage = {"Version": "2012-10-17", "Statement": pol.stage_role_statements("beta", STORE, partition=P, region=R, account=A)}
    build = {"Version": "2012-10-17", "Statement": pol.build_role_statements(STORE, partition=P, region=R, account=A)}
    m = f"arn:aws:bedrock:{R}::foundation-model/x.y-1"
    for doc in (stage, build):
        assert not _allowed(doc, "bedrock:InvokeModel", m)
        assert not _allowed(doc, "bedrock:InvokeModelWithResponseStream", m)
    assert not _allowed(stage, "lambda:InvokeFunction", f"arn:aws:lambda:{R}:{A}:function:finplan-beta-financelambdastool-get-plan:current")
    assert _allowed(stage, "bedrock:GetInferenceProfile", "*")
    assert _allowed(stage, "secretsmanager:GetSecretValue", f"arn:aws:secretsmanager:{R}:{A}:secret:finplan/beta/financeagent/ci-test-client-AbCdEf")
    assert not _allowed(stage, "secretsmanager:GetSecretValue", f"arn:aws:secretsmanager:{R}:{A}:secret:finplan/gamma/financeagent/ci-test-client-AbCdEf")
    assert _allowed(stage, "ssm:PutParameter", f"arn:aws:ssm:{R}:{A}:parameter/finplan/beta/financeagent/agent/runtime-ref")
    assert not _allowed(stage, "ssm:PutParameter", f"arn:aws:ssm:{R}:{A}:parameter/finplan/beta/financelambdastool/lambda/x-arn")
