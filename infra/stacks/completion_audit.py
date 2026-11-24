"""Optional beta completion telemetry references, resolved by CloudFormation.

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
    if not (config.get("completion_audit") or {}).get("enabled", False):
        return {}
    if environment != "beta":
        raise ValueError("completion audit is beta-only until acceptance and promotion")

    def reference(key: str) -> str:
        return ssm.StringParameter.value_for_string_parameter(scope, f"/finplan/{environment}/financeagent/config/{key}")

    queue_arn = reference("judge-queue-arn")
    secret_arn = reference("langfuse-secret-arn")
    bucket = reference("judge-outbox-bucket")
    role.add_to_policy(iam.PolicyStatement(actions=["sqs:SendMessage"], resources=[queue_arn]))
    role.add_to_policy(iam.PolicyStatement(actions=["secretsmanager:GetSecretValue"], resources=[secret_arn]))
    role.add_to_policy(iam.PolicyStatement(actions=["s3:PutObject"], resources=[Fn.join("", ["arn:", Aws.PARTITION, ":s3:::", bucket, "/completions/*"])]))
    return {
        "FINPLAN_JUDGE_ENABLED": "true",
        "FINPLAN_JUDGE_QUEUE_URL": reference("judge-queue-url"),
        "FINPLAN_RUNTIME_ARN": reference("judge-source-runtime-arn"),
        "FINPLAN_LANGFUSE_SECRET_ARN": secret_arn,
        "FINPLAN_JUDGE_OUTBOX_BUCKET": bucket,
    }
