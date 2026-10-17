"""Recommendation trigger + run log IaC (OpenSpec add-recommendation-trigger-and-run-log 1.1, 1.2, 2.2, 3.1)."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from finplan_contracts import iam as contract_iam
from infra.app import load_config
from infra.stacks import naming as n
from infra.stacks.policies import recommendation_runs_deploy_statements
from infra.stacks.recommendation_runs import code_key, lambda_package, trigger_config
from scripts.infra_gates import GateContext, recommendation_trigger_problems, run_gates
from tests.unit.infra.conftest import resources

pytestmark = pytest.mark.synth
ROOT = Path(__file__).resolve().parents[3]
TABLE = "finplan-beta-financeagent-recommendation-runs"


def arn(service: str, resource: str, account: str = "ACCOUNT") -> str:
    """ARN built at run time (no ARN literal in repository files)."""
    return ":".join(("arn", "aws", service, "us-east-2", account, resource))
FUNCTION = "finplan-beta-financeagent-recommendation-trigger"
TRIGGER_IDS = {"RecommendationRunsTable", "RecommendationRunsTableParameter", "RecommendationTriggerFunction", "RecommendationSchedule", "RuntimeRecommendationRunsRead", "RecommendationTriggerLogs"}


def _beta_cfg() -> dict:
    return json.loads((ROOT / "config" / "beta.json").read_text())


def _lid(t: dict, prefix: str) -> str:
    return next(k for k in t["Resources"] if k.startswith(prefix))


def _joined(value) -> str:
    """Render an Fn::Join ARN with placeholder pseudo parameters (for readable assertions)."""
    if isinstance(value, dict) and "Fn::Join" in value:
        return value["Fn::Join"][0].join(_joined(v) for v in value["Fn::Join"][1])
    if isinstance(value, dict) and "Ref" in value:
        return {"AWS::Partition": "aws", "AWS::Region": "us-east-2", "AWS::AccountId": "ACCOUNT"}.get(value["Ref"], value["Ref"])
    if isinstance(value, list):
        return [_joined(v) for v in value]  # type: ignore[return-value]
    return value


# ------------------------------------------------------------------ 1.1 configuration
def test_beta_config_enables_the_hourly_trigger_and_other_environments_do_not():
    cfg = trigger_config("beta", load_config("beta"))
    assert cfg == {
        "schedule_expression": "rate(1 hour)", "timezone": "America/New_York", "portfolio_ids": ["pf_01M547WFC8S9W35652YX87Z204"],
        "methods": ["ppo", "min_variance", "mean_variance", "cvar"], "daily_cap": 40, "narrative_excerpt_bytes": 2048,
        "classical_settings": {"lookback_days": 60, "risk_aversion": 2.0, "cvar_alpha": 0.9, "max_weight": 0.6, "cash_weight": 0.0, "horizon_sessions": 21, "turnover_penalty": 0.001},
    }
    for env in ("gamma", "prod"):
        assert trigger_config(env, load_config(env)) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"enabled": "yes"}, "enabled"),
        ({"schedule_expression": "every hour"}, "schedule_expression"),
        ({"schedule_expression": None}, "schedule_expression"),
        ({"timezone": ""}, "timezone"),
        ({"portfolio_ids": []}, "portfolio_ids"),
        ({"portfolio_ids": ["portfolio-1"]}, "portfolio_ids"),
        ({"methods": ["ppo", "ppo"]}, "methods"),
        ({"methods": ["equal_weight"]}, "methods"),
        ({"daily_cap": 0}, "daily_cap"),
        ({"daily_cap": True}, "daily_cap"),
        ({"narrative_excerpt_bytes": 100000}, "narrative_excerpt_bytes"),
        ({"classical_settings": []}, "classical_settings"),
        ({"unexpected": 1}, "unknown"),
    ],
)
def test_invalid_trigger_configuration_is_rejected(change, message):
    cfg = _beta_cfg()
    cfg["recommendation_trigger"].update(change)
    with pytest.raises(ValueError, match=message):
        trigger_config("beta", cfg)


def test_missing_fields_are_rejected_and_disabled_or_absent_means_off():
    cfg = _beta_cfg()
    del cfg["recommendation_trigger"]["daily_cap"]
    with pytest.raises(ValueError, match="daily_cap"):
        trigger_config("beta", cfg)
    assert trigger_config("beta", {}) is None
    assert trigger_config("beta", {"recommendation_trigger": {"enabled": False}}) is None


def test_enabling_outside_beta_fails_closed():
    block = _beta_cfg()["recommendation_trigger"]
    for env in ("gamma", "prod"):
        with pytest.raises(ValueError, match="only in beta"):
            trigger_config(env, {"recommendation_trigger": block})


def test_config_loader_validates_the_block(tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    cfg = _beta_cfg()
    cfg["recommendation_trigger"]["daily_cap"] = -1
    (tmp_path / "config" / "beta.json").write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="daily_cap"):
        load_config("beta", tmp_path)


# ------------------------------------------------------------------ 1.2 table + 3.1 SSM name
def test_run_table_keys_gsi_billing_tags_and_retention(templates):
    t = templates["agent:beta"]
    table = t["Resources"]["RecommendationRunsTable"]
    props = table["Properties"]
    assert props["TableName"] == TABLE and props["BillingMode"] == "PAY_PER_REQUEST"
    assert props["KeySchema"] == [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]
    assert {a["AttributeName"]: a["AttributeType"] for a in props["AttributeDefinitions"]} == {"pk": "S", "sk": "S", "env": "S", "started_key": "S"}
    assert props["GlobalSecondaryIndexes"] == [{"IndexName": "recent", "KeySchema": [{"AttributeName": "env", "KeyType": "HASH"}, {"AttributeName": "started_key", "KeyType": "RANGE"}], "Projection": {"ProjectionType": "ALL"}}]
    assert "PointInTimeRecoverySpecification" not in props and "StreamSpecification" not in props
    assert {tag["Key"]: tag["Value"] for tag in props["Tags"]} == {"project": "finplan", "owner-repo": "financeagent", "environment": "beta", "logical-role": "recommendation-runs"}
    assert table["DeletionPolicy"] == "Retain"
    param = t["Resources"]["RecommendationRunsTableParameter"]["Properties"]
    assert param == {**param, "Name": "/finplan/beta/financeagent/config/recommendation-runs-table", "Type": "String", "Value": TABLE}


@pytest.mark.parametrize("env", ["gamma", "prod"])
def test_nothing_is_deployed_outside_beta(templates, env):
    t = templates[f"agent:{env}"]
    assert not TRIGGER_IDS & set(t["Resources"]) and not any("Recommendation" in k for k in t["Resources"] if not k.startswith(("Target", "ClassicalTarget", "Policy", "ClassicalPolicy")))
    assert "recommendation-runs" not in json.dumps(t)


def test_disabled_flag_removes_every_trigger_resource():
    import aws_cdk as cdk

    from infra.stacks.agent import AgentStack
    from infra.stacks.identity import IdentityStack
    from infra.stacks.tooling import deployment_synthesizer

    cfg = load_config("beta")
    cfg["recommendation_trigger"] = {**cfg["recommendation_trigger"], "enabled": False}
    app = cdk.App(default_stack_synthesizer=deployment_synthesizer(), context={"cli-telemetry": False})
    stage = cdk.Stage(app, "Beta")
    agent = AgentStack(stage, "Agent", env_name="beta", cfg=cfg, identity=IdentityStack(stage, "Identity", env_name="beta", cfg=cfg))
    ids = {c.node.id for c in agent.node.find_all()}
    assert agent.recommendation_runs is None and not TRIGGER_IDS & ids and "RuntimeRole" in ids


# ------------------------------------------------------------------ 2.2 Lambda, role, logs, schedule
def test_trigger_function_package_runtime_and_configuration(templates):
    t = templates["agent:beta"]
    fn = t["Resources"]["RecommendationTriggerFunction"]["Properties"]
    _, digest = lambda_package()
    assert fn["FunctionName"] == FUNCTION and fn["Runtime"] == "python3.12" and fn["Architectures"] == ["arm64"]
    assert fn["Handler"] == "recommendation_runs.trigger.handler" and fn["Timeout"] == 900 and fn["MemorySize"] == 256
    assert fn["Code"]["S3Key"] == code_key(digest) and _joined(fn["Code"]["S3Bucket"]) == "finplan-shared-financeagent-pipeline-store-ACCOUNT"
    env = fn["Environment"]["Variables"]
    assert env["FINPLAN_ENV"] == "beta" and env["RUNS_TABLE"] == TABLE and json.loads(env["PORTFOLIO_IDS"]) == ["pf_01M547WFC8S9W35652YX87Z204"]
    assert json.loads(env["METHODS"]) == ["ppo", "min_variance", "mean_variance", "cvar"] and env["DAILY_CAP"] == "40" and env["EXCERPT_BYTES"] == "2048"
    assert "ReservedConcurrentExecutions" not in fn  # account concurrency 10 leaves no room for reservations
    assert not re.search(r"secret\"?:|client_secret|token", json.dumps(env), re.I)
    group = t["Resources"]["RecommendationTriggerLogs"]["Properties"]
    assert group["LogGroupName"] == "/aws/lambda/" + FUNCTION and group["RetentionInDays"] == 30
    assert "RecommendationTriggerLogs" in t["Resources"]["RecommendationTriggerFunction"]["DependsOn"]


def test_trigger_role_is_least_privilege_without_wildcard_actions(templates):
    t = templates["agent:beta"]
    role = t["Resources"][_lid(t, "RecommendationTriggerRole")]["Properties"]
    assert role["RoleName"] == "finplan-beta-financeagent-recommendation-trigger-role" and role["PermissionsBoundary"]
    assert role["AssumeRolePolicyDocument"]["Statement"][0]["Principal"] == {"Service": "lambda.amazonaws.com"}
    statements = {s["Sid"]: s for s in role["Policies"][0]["PolicyDocument"]["Statement"]}
    as_list = lambda v: v if isinstance(v, list) else [v]  # noqa: E731
    got = {sid: (sorted(as_list(s["Action"])), sorted(_joined(as_list(s["Resource"])))) for sid, s in statements.items()}
    assert got == {
        "ReadCiClientSecret": (["secretsmanager:GetSecretValue"], [arn("secretsmanager", "secret:finplan/beta/financeagent/ci-test-client-??????", "ACCOUNT")]),
        "ReadReferences": (["ssm:GetParameter"], sorted(arn("ssm", f"parameter/finplan/beta/financeagent/{p}", "ACCOUNT") for p in ("agent/authorizer-metadata-ref", "secret-ref/ci-test-client", "agent/runtime-ref"))),
        "WriteRunLog": (["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"], [arn("dynamodb", f"table/{TABLE}", "ACCOUNT"), arn("dynamodb", f"table/{TABLE}/index/recent", "ACCOUNT")]),
        "OwnLogGroup": (["logs:CreateLogStream", "logs:PutLogEvents"], [arn("logs", f"log-group:/aws/lambda/{FUNCTION}:*", "ACCOUNT")]),
    }
    assert all(s["Effect"] == "Allow" and "*" not in "".join(as_list(s["Action"])) for s in statements.values())


def test_schedule_target_input_retries_and_scheduler_role(templates):
    t = templates["agent:beta"]
    sched = t["Resources"]["RecommendationSchedule"]["Properties"]
    assert sched["Name"] == FUNCTION and sched["ScheduleExpression"] == "rate(1 hour)" and sched["ScheduleExpressionTimezone"] == "America/New_York"
    assert sched["FlexibleTimeWindow"] == {"Mode": "OFF"} and sched["State"] == "ENABLED"
    target = sched["Target"]
    assert target["Arn"] == {"Fn::GetAtt": ["RecommendationTriggerFunction", "Arn"]}
    assert json.loads(target["Input"]) == {"trigger": "scheduled", "window": "<aws.scheduler.scheduled-time>"}
    assert target["RetryPolicy"] == {"MaximumRetryAttempts": 1, "MaximumEventAgeInSeconds": 3600}
    role_id = target["RoleArn"]["Fn::GetAtt"][0]
    role = t["Resources"][role_id]["Properties"]
    assert role["RoleName"] == "finplan-beta-financeagent-recommendation-schedule-role" and role["PermissionsBoundary"]
    assert role["AssumeRolePolicyDocument"]["Statement"][0]["Principal"] == {"Service": "scheduler.amazonaws.com"}
    assert role["Policies"][0]["PolicyDocument"]["Statement"] == [{"Action": "lambda:InvokeFunction", "Effect": "Allow", "Resource": {"Fn::GetAtt": ["RecommendationTriggerFunction", "Arn"]}, "Sid": "InvokeTrigger"}]
    assert not any(r["Type"] == "AWS::Lambda::Url" or r["Type"].startswith("AWS::ApiGateway") for r in t["Resources"].values())


# ------------------------------------------------------------------ 3.1 Runtime read grant
def test_runtime_role_gets_only_read_access_to_the_run_table(templates):
    t = templates["agent:beta"]
    pol = t["Resources"]["RuntimeRecommendationRunsRead"]["Properties"]
    assert pol["Roles"] == [{"Ref": _lid(t, "RuntimeRole")}]
    got = {s["Sid"]: (s["Effect"], sorted(s["Action"]), _joined(s["Resource"])) for s in pol["PolicyDocument"]["Statement"]}
    assert got == {
        "QueryRecommendationRuns": ("Allow", ["dynamodb:GetItem", "dynamodb:Query"], [arn("dynamodb", f"table/{TABLE}", "ACCOUNT"), arn("dynamodb", f"table/{TABLE}/index/recent", "ACCOUNT")]),
        "ReadRecommendationRunsTableName": ("Allow", ["ssm:GetParameter"], [arn("ssm", "parameter/finplan/beta/financeagent/config/recommendation-runs-table", "ACCOUNT")]),
    }
    # the base Runtime role policy is unchanged: no DynamoDB permission there
    runtime = t["Resources"][_lid(t, "RuntimeRole")]["Properties"]
    assert "dynamodb" not in json.dumps(runtime["Policies"])


def test_runtime_grant_permits_query_and_never_writes():
    stmts = [{"Sid": "Q", "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:GetItem"], "Resource": [arn("dynamodb", f"table/{TABLE}", "0" * 12), arn("dynamodb", f"table/{TABLE}/index/recent", "0" * 12)]}]
    table_arn = arn("dynamodb", f"table/{TABLE}", "0" * 12)
    assert _allowed(stmts, "dynamodb:Query", table_arn + "/index/recent")
    for action in ("dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan"):
        assert not _allowed(stmts, action, table_arn)
    assert not _allowed(stmts, "dynamodb:Query", arn("dynamodb", "table/finplan-beta-financialplanning-decisions", "0" * 12))


def _allowed(statements, action, resource):
    return contract_iam.evaluate(contract_iam.Request(action, resource, {}), [{"Version": "2012-10-17", "Statement": statements}]).allowed


# ------------------------------------------------------------------ gate: handler code present, scoped grants
def test_trigger_gate_passes_on_the_synthesized_assembly(assembly):
    ctx = GateContext(assembly)
    assert run_gates(ctx, ["recommendation-trigger", "ownership"]) == {"recommendation-trigger": [], "ownership": []}
    assert "FA-GAP-RECOMMENDATION-RUNS" in " ".join(ctx.notes.get("ownership", []))


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda r: r["RecommendationTriggerFunction"]["Properties"].__setitem__("Code", {"ZipFile": ""}), "code must be the store package"),
        (lambda r: r["RecommendationTriggerFunction"]["Properties"]["Code"].__setitem__("S3Key", "releases/lambda-code/recommendation-trigger-" + "0" * 64 + ".zip"), "code must be the store package"),
        (lambda r: r["RecommendationTriggerFunction"]["Properties"].__setitem__("Handler", "index.handler"), "handler code is missing"),
        (lambda r: r.pop("RecommendationSchedule"), "partial"),
        (lambda r: r["RecommendationSchedule"]["Properties"]["Target"]["RetryPolicy"].__setitem__("MaximumRetryAttempts", 5), "at most one retry"),
        (lambda r: r["RuntimeRecommendationRunsRead"]["Properties"]["PolicyDocument"]["Statement"][0].__setitem__("Action", ["dynamodb:PutItem"]), "read-only"),
        (lambda r: r["RuntimeRecommendationRunsRead"]["Properties"]["PolicyDocument"]["Statement"][0].__setitem__("Action", ["dynamodb:*"]), "wildcard"),
        (lambda r: r["RuntimeRecommendationRunsRead"]["Properties"]["PolicyDocument"]["Statement"][0].__setitem__("Resource", ["*"]), "wildcard"),
    ],
)
def test_trigger_gate_rejects_planted_violations(templates, mutate, expect):
    t = copy.deepcopy(templates["agent:beta"])
    assert recommendation_trigger_problems(t, "beta", "beta") == []
    mutate(t["Resources"])
    assert any(expect in p for p in recommendation_trigger_problems(t, "beta", "beta"))


def test_trigger_gate_rejects_the_trigger_outside_beta(templates):
    assert any("beta-only" in p for p in recommendation_trigger_problems(copy.deepcopy(templates["agent:beta"]), "gamma", "gamma"))


# ------------------------------------------------------------------ deploy role (bootstrap) and build publication
def test_beta_execution_role_can_deploy_exactly_these_resources(templates):
    tooling = templates["tooling"]
    pols = {k: v for k, v in resources(tooling, "AWS::IAM::Policy").items() if k.startswith("DeployExecRoleBeta")}
    extra = next(v for k, v in pols.items() if "RecommendationRuns" in k)["Properties"]
    assert extra["Roles"] == [{"Ref": _lid(tooling, "DeployExecRoleBeta")}] and extra["PolicyName"] == "recommendation-runs-deploy"
    assert not any("RecommendationRuns" in k for k in resources(tooling, "AWS::IAM::Policy") if k.startswith(("DeployExecRoleGamma", "DeployExecRoleProd")))
    assert not resources(tooling, "AWS::IAM::ManagedPolicy")
    # IAM's aggregate inline limit for a role is 10,240 characters (resolved, no whitespace)
    size = sum(len(json.dumps(_joined_doc(v["Properties"]["PolicyDocument"]), separators=(",", ":"))) for v in pols.values())
    assert size < 10_240 - 600, size
    acct = "0" * 12
    stmts = recommendation_runs_deploy_statements("beta", partition="aws", region="us-east-2", account=acct)
    assert all("*" not in a for s in stmts for a in s["Action"])
    allowed = lambda action, res: _allowed(stmts, action, res)  # noqa: E731
    assert allowed("lambda:CreateFunction", arn("lambda", f"function:{FUNCTION}", acct))
    assert not allowed("lambda:CreateFunction", arn("lambda", "function:finplan-beta-financeagent-other", acct))
    assert allowed("dynamodb:CreateTable", arn("dynamodb", f"table/{TABLE}", acct))
    assert not allowed("dynamodb:PutItem", arn("dynamodb", f"table/{TABLE}", acct))
    assert allowed("scheduler:CreateSchedule", arn("scheduler", f"schedule/default/{FUNCTION}", acct))


def _joined_doc(doc):
    if isinstance(doc, dict):
        if "Fn::Join" in doc or "Ref" in doc:
            return _joined(doc)
        if "Fn::GetAtt" in doc:
            return ":".join(("arn", "aws", "s3", "", "", "finplan-shared-financeagent-pipeline-store-" + "0" * 12))
        return {k: _joined_doc(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_joined_doc(v) for v in doc]
    return doc


def test_build_publishes_the_package_at_the_template_key(templates):
    from scripts.build_stage import publish_lambda_code

    class S3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kw):
            self.calls.append(kw)

    s3 = S3()
    key = publish_lambda_code(s3, "store-bucket", log=lambda _m: None)
    data, _ = lambda_package()
    assert key == templates["agent:beta"]["Resources"]["RecommendationTriggerFunction"]["Properties"]["Code"]["S3Key"]
    assert key.startswith("releases/") and s3.calls[0]["Body"] == data and s3.calls[0]["Bucket"] == "store-bucket" and s3.calls[0]["ChecksumSHA256"]
    assert n.recommendation_trigger_name("beta") == FUNCTION and n.recommendation_runs_table_name("beta") == TABLE
