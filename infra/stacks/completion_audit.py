"""Stage-specific completion telemetry and model credential references, resolved by CloudFormation.

The separate judge stack/operator publishes these non-secret SSM references.
No Langfuse key value, JWT, queue account ID or runtime ID is stored in source.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aws_cdk import Aws, Fn
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct


def completion_audit_environment(scope: Construct, environment: str, config: Mapping[str, Any], role: iam.Role) -> dict[str, str]:
    def reference(key: str) -> str:
        return ssm.StringParameter.value_for_string_parameter(scope, f"/finplan/{environment}/financeagent/config/{key}")

    result: dict[str, str] = {}
    if (config.get("explanation") or {}).get("provider") == "openai":
        model_secret = reference("openai-secret-arn")
        role.add_to_policy(iam.PolicyStatement(actions=["secretsmanager:GetSecretValue"], resources=[model_secret]))
        result["FINPLAN_OPENAI_SECRET_ARN"] = model_secret
    if not (config.get("completion_audit") or {}).get("enabled", False):
        return result
    if environment not in ("beta", "gamma"):
        raise ValueError("completion audit is enabled only for beta/gamma until production approval")

    queue_arn = reference("judge-queue-arn")
    secret_arn = reference("langfuse-secret-arn")
    bucket = reference("judge-outbox-bucket")
    role.add_to_policy(iam.PolicyStatement(actions=["sqs:SendMessage"], resources=[queue_arn]))
    role.add_to_policy(iam.PolicyStatement(actions=["secretsmanager:GetSecretValue"], resources=[secret_arn]))
    role.add_to_policy(iam.PolicyStatement(actions=["s3:PutObject"], resources=[Fn.join("", ["arn:", Aws.PARTITION, ":s3:::", bucket, "/completions/*"])]))
    return {
        **result,
        "FINPLAN_JUDGE_ENABLED": "true",
        "FINPLAN_JUDGE_QUEUE_URL": reference("judge-queue-url"),
        "FINPLAN_RUNTIME_ARN": reference("judge-source-runtime-arn"),
        "FINPLAN_LANGFUSE_SECRET_ARN": secret_arn,
        "FINPLAN_JUDGE_OUTBOX_BUCKET": bucket,
    }
