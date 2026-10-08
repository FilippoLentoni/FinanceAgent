"""Shared CDK building blocks for every FinanceAgent stack.

* :class:`EnvStack` - base of the per-environment stacks: the contract cost-allocation tags
  (``project``, ``owner-repo`` = ``financeagent``, ``environment``) on everything in the stack, and
  the environment permission boundary ``finplan-<env>-permission-boundary`` (FinancialPlanning-owned)
  on EVERY IAM role in the stack, CDK-generated ones included. Prod stacks get termination protection.
* :func:`tag_role` - the ``logical-role`` tag (ownership-matrix key) of a construct tree.
* :func:`cfn_tags` - the same tags as a ``{key: value}`` map for AgentCore L1 resources whose
  ``Tags`` property is a map (Runtime, Gateway, Memory) and therefore not reached by ``Tags.of``
  in every CDK version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack, Tags
from aws_cdk import aws_iam as iam
from constructs import Construct, IConstruct
from finplan_contracts import boundaries as contract_boundaries
from finplan_contracts import ssm as contract_ssm

from . import naming as n

__all__ = ["EnvStack", "StageContext", "cfn_tags", "tag_role"]


def tag_role(construct: IConstruct, logical_role: str) -> None:
    Tags.of(construct).add("logical-role", logical_role)


def cfn_tags(env: str, logical_role: str) -> dict[str, str]:
    return contract_ssm.cost_allocation_tags(n.REPO, env, logical_role)


class EnvStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, region: str, description: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, env=cdk.Environment(region=region), description=description, termination_protection=env_name == "prod", **kwargs)
        self.env_name = env_name
        base = contract_ssm.cost_allocation_tags(n.REPO, env_name, "placeholder")
        for key in ("project", "owner-repo", "environment"):
            Tags.of(self).add(key, base[key])
        boundary = iam.ManagedPolicy.from_managed_policy_name(self, "EnvPermissionBoundary", contract_boundaries.boundary_name(env_name))
        iam.PermissionsBoundary.of(self).apply(boundary)


class StageContext:
    """What ``infra/app.py`` hands to the pipeline module for one environment."""

    def __init__(self, env: str, cfg: Mapping[str, Any], stage: cdk.Stage) -> None:
        self.env = env
        self.cfg = cfg
        self.stage = stage
        self.stacks: dict[str, Stack] = {}
