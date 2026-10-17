"""Recommendation trigger and run log (OpenSpec add-recommendation-trigger-and-run-log, tasks 1.1, 1.2,
2.2, 3.1). Synthesized into the agent stack only when ``recommendation_trigger.enabled`` (beta only).

Resources (all tagged ``logical-role`` = ``recommendation-runs``):

* DynamoDB on-demand table ``finplan-<env>-financeagent-recommendation-runs`` (``pk`` run_id, ``sk``
  portfolio_id; GSI ``recent`` = ``env`` + ``started_key``), retained on stack deletion;
* SSM ``/finplan/<env>/financeagent/config/recommendation-runs-table`` = the table name;
* Lambda ``finplan-<env>-financeagent-recommendation-trigger`` (python3.12, arm64, 900 s, 256 MB), its
  role (CI client secret + its SSM references + runtime-ref, own table writes, own log group) and its
  30-day log group;
* EventBridge Scheduler schedule (configured expression and timezone, flexible window OFF, at most one
  retry, max event age 1 h) with a role that may only invoke this function;
* a read-only grant for the Runtime role: ``dynamodb:Query``/``GetItem`` on the table and its
  ``recent`` index, ``ssm:GetParameter`` on the table-name parameter.

Packaging: CloudFormation inline code is limited to 4096 bytes and the handler
(:mod:`finplan_agent.recommendation_runs`) is larger. The pipeline publishes no CDK file assets, so the
code is a deterministic zip of that package, content-addressed at
``releases/lambda-code/recommendation-trigger-<sha256>.zip`` in the pipeline store; the Build stage
uploads it with its existing release-ledger grant (``scripts/build_stage.py``) and the CloudFormation
execution role reads it with its existing store read grant. The ``recommendation-trigger`` post-synth
gate refuses a template whose code key does not match the package built from the current source.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aws_cdk import Aws, CfnTag, Fn, RemovalPolicy
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_ssm as ssm

from . import naming as n
from .common import cfn_tags, tag_role

__all__ = ["CODE_PREFIX", "LOGICAL_ROLE", "METHODS", "PACKAGE_FILES", "build_recommendation_runs", "code_key", "lambda_package", "table_parameter", "trigger_config"]

LOGICAL_ROLE = "recommendation-runs"
METHODS = ("ppo", "min_variance", "mean_variance", "cvar")
HANDLER = "recommendation_runs.trigger.handler"
CODE_PREFIX = "releases/lambda-code/"
PACKAGE_DIR = Path(__file__).resolve().parents[2] / "agent" / "finplan_agent" / "recommendation_runs"
PACKAGE_FILES = ("__init__.py", "runlog.py", "trigger.py")
INDEX = "recent"
_PORTFOLIO = re.compile(r"pf_[0-9A-HJKMNP-TV-Z]{26}")
_SCHEDULE = re.compile(r"(rate\(\d+ (minute|minutes|hour|hours|day|days)\)|cron\([^()]+\))")
_KEYS = {"enabled", "schedule_expression", "timezone", "portfolio_ids", "methods", "daily_cap", "narrative_excerpt_bytes", "classical_settings", "$comment"}


def table_parameter(env: str) -> str:
    return n.own_ssm(env, "config", "recommendation-runs-table")


def trigger_config(env: str, cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validated ``recommendation_trigger`` block, or None when absent/disabled. Raises ValueError."""
    block = cfg.get("recommendation_trigger")
    if block is None:
        return None
    if not isinstance(block, Mapping) or not isinstance(block.get("enabled"), bool):
        raise ValueError("recommendation_trigger.enabled must be a boolean")
    if not block["enabled"]:
        return None
    unknown = set(block) - _KEYS
    if unknown:
        raise ValueError(f"recommendation_trigger has unknown fields {sorted(unknown)}")
    if env != "beta":
        raise ValueError("recommendation_trigger may be enabled only in beta (the deploy grants are beta-only)")
    expr, tz = block.get("schedule_expression"), block.get("timezone")
    if not isinstance(expr, str) or not _SCHEDULE.fullmatch(expr):
        raise ValueError("recommendation_trigger.schedule_expression must be rate(...) or cron(...)")
    if not isinstance(tz, str) or not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z_]+)*", tz):
        raise ValueError("recommendation_trigger.timezone must be an IANA zone name")
    pids = block.get("portfolio_ids")
    if not isinstance(pids, list) or not 1 <= len(pids) <= 10 or len(set(pids)) != len(pids) or not all(isinstance(p, str) and _PORTFOLIO.fullmatch(p) for p in pids):
        raise ValueError("recommendation_trigger.portfolio_ids must list 1-10 distinct saved portfolio ids (pf_...)")
    methods = block.get("methods")
    if not isinstance(methods, list) or not methods or len(set(methods)) != len(methods) or not set(methods) <= set(METHODS):
        raise ValueError(f"recommendation_trigger.methods must be distinct values of {list(METHODS)}")
    cap, excerpt = block.get("daily_cap"), block.get("narrative_excerpt_bytes")
    if type(cap) is not int or not 1 <= cap <= 200:
        raise ValueError("recommendation_trigger.daily_cap must be an integer 1-200")
    if type(excerpt) is not int or not 256 <= excerpt <= 8192:
        raise ValueError("recommendation_trigger.narrative_excerpt_bytes must be an integer 256-8192")
    settings = block.get("classical_settings", {})
    if not isinstance(settings, Mapping):
        raise ValueError("recommendation_trigger.classical_settings must be an object")
    return {"schedule_expression": expr, "timezone": tz, "portfolio_ids": list(pids), "methods": list(methods), "daily_cap": cap, "narrative_excerpt_bytes": excerpt, "classical_settings": dict(settings)}


def lambda_package() -> tuple[bytes, str]:
    """Deterministic zip (stored, fixed timestamps) of the handler package and its SHA-256 hex digest."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name in PACKAGE_FILES:
            info = zipfile.ZipInfo(f"recommendation_runs/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            zf.writestr(info, (PACKAGE_DIR / name).read_bytes())
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def code_key(digest: str) -> str:
    return f"{CODE_PREFIX}recommendation-trigger-{digest}.zip"


def _tags(env: str) -> list[CfnTag]:
    return [CfnTag(key=k, value=v) for k, v in cfn_tags(env, LOGICAL_ROLE).items()]


def build_recommendation_runs(stack: Any, env: str, cfg: Mapping[str, Any], runtime_role: iam.Role) -> dict[str, Any] | None:
    settings = trigger_config(env, cfg)
    if settings is None:
        return None
    p, r, a = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID
    arn = lambda svc, res: Fn.join("", ["arn:", p, f":{svc}:", r, ":", a, ":", res])  # noqa: E731
    table_name, fn_name = n.recommendation_runs_table_name(env), n.recommendation_trigger_name(env)
    table = ddb.CfnTable(
        stack, "RecommendationRunsTable", table_name=table_name, billing_mode="PAY_PER_REQUEST",
        key_schema=[ddb.CfnTable.KeySchemaProperty(attribute_name="pk", key_type="HASH"), ddb.CfnTable.KeySchemaProperty(attribute_name="sk", key_type="RANGE")],
        attribute_definitions=[ddb.CfnTable.AttributeDefinitionProperty(attribute_name=k, attribute_type="S") for k in ("pk", "sk", "env", "started_key")],
        global_secondary_indexes=[ddb.CfnTable.GlobalSecondaryIndexProperty(
            index_name=INDEX, key_schema=[ddb.CfnTable.KeySchemaProperty(attribute_name="env", key_type="HASH"), ddb.CfnTable.KeySchemaProperty(attribute_name="started_key", key_type="RANGE")],
            projection=ddb.CfnTable.ProjectionProperty(projection_type="ALL"))],
        tags=_tags(env),
    )
    table.apply_removal_policy(RemovalPolicy.RETAIN)
    table_arn = arn("dynamodb", f"table/{table_name}")
    index_arn = arn("dynamodb", f"table/{table_name}/index/{INDEX}")
    param = ssm.CfnParameter(stack, "RecommendationRunsTableParameter", name=table_parameter(env), type="String", value=table_name,
                             description=f"FinanceAgent {env} recommendation run-log table name", tags=cfn_tags(env, LOGICAL_ROLE))
    param.node.add_dependency(table)

    group_name = "/aws/lambda/" + fn_name
    group = logs.CfnLogGroup(stack, "RecommendationTriggerLogs", log_group_name=group_name, retention_in_days=n.LOG_RETENTION_DAYS)
    group.apply_removal_policy(RemovalPolicy.DESTROY)
    tag_role(group, LOGICAL_ROLE)
    ssm_refs = [f"/finplan/{env}/{n.REPO}/agent/authorizer-metadata-ref", f"/finplan/{env}/{n.REPO}/secret-ref/ci-test-client", f"/finplan/{env}/{n.REPO}/agent/runtime-ref"]
    role = iam.Role(
        stack, "RecommendationTriggerRole", role_name=n.role_name(env, "recommendation-trigger"), assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        description=f"FinanceAgent {env} recommendation trigger: read-only CI client token, own run-log table, own logs",
        inline_policies={"recommendation-trigger": iam.PolicyDocument.from_json({"Version": "2012-10-17", "Statement": [
            {"Sid": "ReadCiClientSecret", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": [arn("secretsmanager", f"secret:{n.secret_name(env, 'ci-test-client')}-??????")]},
            {"Sid": "ReadReferences", "Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": [arn("ssm", "parameter" + ref) for ref in ssm_refs]},
            {"Sid": "WriteRunLog", "Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem", "dynamodb:Query"], "Resource": [table_arn, index_arn]},
            {"Sid": "OwnLogGroup", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [arn("logs", f"log-group:{group_name}:*")]},
        ]})},
    )
    tag_role(role, LOGICAL_ROLE)
    _, digest = lambda_package()
    function = lam.CfnFunction(
        stack, "RecommendationTriggerFunction", function_name=fn_name, description=f"FinanceAgent {env} scheduled/on-demand saved-portfolio recommendation runs (read-only)",
        runtime="python3.12", architectures=["arm64"], handler=HANDLER, role=role.role_arn, timeout=900, memory_size=256,
        code=lam.CfnFunction.CodeProperty(s3_bucket=n.pipeline_store_bucket_name(Aws.ACCOUNT_ID), s3_key=code_key(digest)),
        environment=lam.CfnFunction.EnvironmentProperty(variables={
            "FINPLAN_ENV": env, "RUNS_TABLE": table_name, "PORTFOLIO_IDS": json.dumps(settings["portfolio_ids"]), "METHODS": json.dumps(settings["methods"]),
            "DAILY_CAP": str(settings["daily_cap"]), "EXCERPT_BYTES": str(settings["narrative_excerpt_bytes"]),
            "CLASSICAL_SETTINGS": json.dumps(settings["classical_settings"], sort_keys=True)}),
        tags=_tags(env),
    )
    function.node.add_dependency(group)
    function.node.add_dependency(role)
    function.add_metadata("logical-role", LOGICAL_ROLE)

    schedule_role = iam.Role(
        stack, "RecommendationScheduleRole", role_name=n.role_name(env, "recommendation-schedule"),
        assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": a}}),
        description=f"FinanceAgent {env} recommendation schedule: invoke the trigger function only",
        inline_policies={"invoke-trigger": iam.PolicyDocument.from_json({"Version": "2012-10-17", "Statement": [
            {"Sid": "InvokeTrigger", "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": [function.attr_arn]}]})},
    )
    tag_role(schedule_role, LOGICAL_ROLE)
    schedule = scheduler.CfnSchedule(
        stack, "RecommendationSchedule", name=fn_name, description=f"FinanceAgent {env} recommendation trigger ({settings['schedule_expression']})",
        schedule_expression=settings["schedule_expression"], schedule_expression_timezone=settings["timezone"], state="ENABLED",
        flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
        target=scheduler.CfnSchedule.TargetProperty(
            arn=function.attr_arn, role_arn=schedule_role.role_arn, input=json.dumps({"trigger": "scheduled", "window": "<aws.scheduler.scheduled-time>"}),
            retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=1, maximum_event_age_in_seconds=3600)),
    )
    schedule.node.add_dependency(schedule_role)
    schedule.add_metadata("logical-role", LOGICAL_ROLE)

    read = iam.CfnPolicy(
        stack, "RuntimeRecommendationRunsRead", policy_name="recommendation-runs-read", roles=[runtime_role.role_name],
        policy_document={"Version": "2012-10-17", "Statement": [
            {"Sid": "QueryRecommendationRuns", "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:GetItem"], "Resource": [table_arn, index_arn]},
            {"Sid": "ReadRecommendationRunsTableName", "Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": [arn("ssm", "parameter" + table_parameter(env))]},
        ]},
    )
    read.add_metadata("logical-role", LOGICAL_ROLE)
    return {"table": table, "parameter": param, "function": function, "role": role, "schedule": schedule, "schedule_role": schedule_role, "runtime_read": read, "log_group": group}
