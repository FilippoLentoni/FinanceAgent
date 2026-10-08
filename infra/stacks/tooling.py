"""FinanceAgent account-level stacks (environment ``shared``), deployed ONLY by the authenticated bootstrap
(``scripts/bootstrap.py``, ``docs/bootstrap.md``), never by the pipeline. Mirrors the deployed
FinancialPlanning / FinanceModel tooling pattern (lesson L1):

* ``finplan-shared-financeagent-pipeline-store`` (:class:`StoreStack`): the pipeline store bucket
  ``finplan-shared-financeagent-pipeline-store-<account>`` (CodePipeline artifacts, the release ledger
  under ``releases/``, the staged tooling template under ``bootstrap/``). SSE-S3, TLS only, Block
  Public Access, versioned, retained. :class:`aws_cdk.LegacyStackSynthesizer`: deployed inline with the
  operator's credentials, NO ``cdk-hnb659fds`` role and NO ``cdk-*-assets`` bucket.
* ``finplan-shared-financeagent-tooling`` (:class:`ToolingStack`):
  - the Runtime image repository ``finplan-shared-financeagent-runtime-images`` (matrix row
    ``financeagent-ecr-repository``: immutable tags, scan on push, digest-addressed; the newest
    :data:`KEEP_RELEASE_IMAGES` release images kept);
  - the per-environment Gateway service roles ``finplan-<env>-financeagent-gateway-service-role``
    (matrix row ``gateway-service-role``; tagged with their environment and bounded by that
    environment's boundary). They exist BEFORE FinanceLambdasTool's Gateway-facing release (CONTRACT
    GAP-3 resolution): the bootstrap publishes ``/finplan/<env>/financeagent/agent/gateway-principal-ref``;
  - the pipeline (:mod:`infra.stacks.pipeline`).
  :class:`aws_cdk.CliCredentialsStackSynthesizer` stages the template in the store under ``bootstrap/``.

FinanceAgent creates NO permission boundary and NO budget (both FinancialPlanning-owned; lesson L6).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
import jsii
from aws_cdk import Aws, Duration, Tags
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct
from finplan_contracts import boundaries as contract_boundaries
from finplan_contracts import ssm as contract_ssm

from . import naming as n
from .common import tag_role
from .policies import gateway_role_policy, gateway_trust_conditions

__all__ = [
    "BOOTSTRAP_PREFIX",
    "KEEP_RELEASE_IMAGES",
    "RELEASES_PREFIX",
    "STORE_STACK_NAME",
    "TOOLING_STACK_NAME",
    "StoreStack",
    "ToolingStack",
    "add_log_group",
    "add_to_app",
    "deployment_synthesizer",
    "get_tooling_stack",
]

TOOLING_CONSTRUCT_ID = "Tooling"
TOOLING_STACK_NAME = n.shared_name("tooling")
STORE_CONSTRUCT_ID = "PipelineStore"
STORE_STACK_NAME = n.shared_name("pipeline-store")
BOOTSTRAP_PREFIX = "bootstrap/"
RELEASES_PREFIX = "releases/"
ASSET_PREFIX = "assets/"
KEEP_RELEASE_IMAGES = 10
QUALIFIER = "finplan"


def add_log_group(scope: Construct, cid: str, name: str, logical_role: str) -> logs.LogGroup:
    """An explicit log group: 30-day retention (lesson L6), deleted with the stack, tagged with its owner's logical role."""
    group = logs.LogGroup(scope, cid, log_group_name=name, retention=logs.RetentionDays.ONE_MONTH, removal_policy=cdk.RemovalPolicy.DESTROY)
    tag_role(group, logical_role)
    return group


def _shared_tags(stack: cdk.Stack) -> None:
    base = contract_ssm.cost_allocation_tags(n.REPO, contract_ssm.SHARED, "placeholder")
    for key in ("project", "owner-repo", "environment"):
        Tags.of(stack).add(key, base[key])


def _synth(prefix: str) -> cdk.CliCredentialsStackSynthesizer:
    return cdk.CliCredentialsStackSynthesizer(file_assets_bucket_name=n.pipeline_store_bucket_name("${AWS::AccountId}"), bucket_prefix=prefix, image_assets_repository_name=n.ecr_repository_name(), qualifier=QUALIFIER)


@jsii.implements(cdk.IReusableStackSynthesizer)
class _PerStackSynthesizer:
    """App default: a fresh CLI-credentials synthesizer per environment stack (no CDKToolkit, no
    bootstrap-version rule); the environment stacks carry no file or image asset."""

    def reusable_bind(self, stack: cdk.Stack) -> cdk.IBoundStackSynthesizer:
        synth = _synth(ASSET_PREFIX)
        synth.bind(stack)
        return synth


def deployment_synthesizer() -> cdk.IReusableStackSynthesizer:
    return _PerStackSynthesizer()


class StoreStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, shared: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(
            scope,
            construct_id,
            stack_name=STORE_STACK_NAME,
            env=cdk.Environment(region=str(shared["region"])),
            synthesizer=cdk.LegacyStackSynthesizer(),
            termination_protection=True,
            description="FinanceAgent pipeline store (environment shared): pipeline artifacts, release ledger, staged tooling template. Deployed only by the authenticated bootstrap.",
            **kwargs,
        )
        _shared_tags(self)
        self.bucket = s3.Bucket(
            self,
            "Store",
            bucket_name=n.pipeline_store_bucket_name(Aws.ACCOUNT_ID),
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(id="pipeline-artifacts", prefix=n.PIPELINE_NAME[:20] + "/", expiration=Duration.days(30)),
                s3.LifecycleRule(id="assets", prefix=ASSET_PREFIX, expiration=Duration.days(30)),
                s3.LifecycleRule(id="bootstrap", prefix=BOOTSTRAP_PREFIX, expiration=Duration.days(30)),
                s3.LifecycleRule(id="noncurrent", noncurrent_version_expiration=Duration.days(7), abort_incomplete_multipart_upload_after=Duration.days(7)),
            ],
        )
        tag_role(self.bucket, "pipeline-artifact-bucket")


class ToolingStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, shared: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(
            scope,
            construct_id,
            stack_name=TOOLING_STACK_NAME,
            env=cdk.Environment(region=str(shared["region"])),
            synthesizer=_synth(BOOTSTRAP_PREFIX),
            termination_protection=True,
            description="FinanceAgent account-level tooling (environment shared): Runtime image repository, per-environment Gateway service roles, pipeline. Deployed only by the authenticated bootstrap. No budget and no permission boundary (both FinancialPlanning-owned).",
            **kwargs,
        )
        self.shared = shared
        _shared_tags(self)
        self.enforced_roles: list[iam.Role] = []
        iam.PermissionsBoundary.of(self).apply(iam.ManagedPolicy.from_managed_policy_name(self, "SharedBoundary", contract_boundaries.boundary_name(contract_ssm.SHARED)))
        self.repository = ecr.Repository(
            self,
            "RuntimeImages",
            repository_name=n.ecr_repository_name(),
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            encryption=ecr.RepositoryEncryption.AES_256,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(rule_priority=1, description="untagged layers of interrupted pushes", tag_status=ecr.TagStatus.UNTAGGED, max_image_age=Duration.days(1)),
                ecr.LifecycleRule(rule_priority=2, description="keep the newest release images (rollback window)", tag_status=ecr.TagStatus.TAGGED, tag_prefix_list=["rel_"], max_image_count=KEEP_RELEASE_IMAGES),
            ],
        )
        tag_role(self.repository, "financeagent-image-repository")
        p, r, a = Aws.PARTITION, Aws.REGION, Aws.ACCOUNT_ID
        self.gateway_roles: dict[str, iam.Role] = {}
        for env in n.ENVIRONMENTS:
            role = iam.Role(
                self,
                f"GatewayServiceRole{env.capitalize()}",
                role_name=n.gateway_role_name(env),
                assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", conditions=gateway_trust_conditions(env, partition=p, region=r, account=a)),
                description=f"FinanceAgent {env} AgentCore Gateway service role: invokes {env} FinanceLambdasTool tools only",
                inline_policies={"gateway": iam.PolicyDocument.from_json(gateway_role_policy(env, partition=p, region=r, account=a))},
            )
            tag_role(role, "gateway-service-role")
            self.scope_to_environment(role, env)
            self.gateway_roles[env] = role
            cdk.CfnOutput(self, f"GatewayServiceRoleName{env.capitalize()}", value=role.role_name)

    def register_enforced_role(self, role: iam.Role) -> None:
        self.enforced_roles.append(role)

    @staticmethod
    def scope_to_environment(role: iam.Role, env: str) -> None:
        """Per-environment role in the account-level stack: ``environment=<env>`` and that environment's boundary."""
        Tags.of(role).add("environment", env, priority=200)
        iam.PermissionsBoundary.of(role).apply(iam.ManagedPolicy.from_managed_policy_name(role, f"{env.capitalize()}Boundary", contract_boundaries.boundary_name(env)))


def get_tooling_stack(app: cdk.App, shared: Mapping[str, Any]) -> ToolingStack:
    existing = app.node.try_find_child(TOOLING_CONSTRUCT_ID)
    if existing is not None:
        assert isinstance(existing, ToolingStack)
        return existing
    store = app.node.try_find_child(STORE_CONSTRUCT_ID) or StoreStack(app, STORE_CONSTRUCT_ID, shared=shared)
    tooling = ToolingStack(app, TOOLING_CONSTRUCT_ID, shared=shared)
    tooling.add_stack_dependency(store)
    return tooling


def add_to_app(app: cdk.App, shared: Mapping[str, Any], stages: Mapping[str, Any]) -> None:
    get_tooling_stack(app, shared)
