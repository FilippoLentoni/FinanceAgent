"""Ownership-matrix gaps of the pinned contracts (finplan-contracts 1.0.0) that FinanceAgent's deployable
IaC needs (reported as CONTRACT GAPs to FinancialPlanning; each needs a contract minor that adds the
type to the named matrix row).

Unlike FinanceLambdasTool's off-by-default switches, these resources are REQUIRED for a working cloud
deployment (the user decision of 2026-10-08: cloud deployment is a must), so they are always
synthesized. The ownership gate (``scripts/infra_gates.py``) still fails on every problem EXCEPT an
exact match of an entry below (row, resource type, logical role) and the CDK helpers (default
policies) of such a resource. Anything else - another type, another role, another repository's row -
fails the build as before. Remove an entry as soon as the pinned contracts list the type.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["KNOWN_GAPS", "ContractGap", "match_gap"]


@dataclass(frozen=True)
class ContractGap:
    gap_id: str
    row: str
    resource_type: str
    logical_role: str
    reason: str


KNOWN_GAPS: tuple[ContractGap, ...] = (
    ContractGap("FA-GAP-RUNTIME-ROLE", "agentcore-runtime-and-gateway", "AWS::IAM::Role", "agent-runtime", "the AgentCore Runtime needs an execution role (design D4 IAM); the row lists no AWS::IAM::Role"),
    ContractGap(
        "FA-GAP-POLICY-ENGINE", "agentcore-runtime-and-gateway", "AWS::BedrockAgentCore::PolicyEngine", "gateway-policy-engine", "the row names the policy engine (logical role gateway-policy-engine) but not its CloudFormation type"
    ),
    ContractGap("FA-GAP-POLICY", "agentcore-runtime-and-gateway", "AWS::BedrockAgentCore::Policy", "gateway-policy-engine", "the Cedar policies of the policy engine (AWS::BedrockAgentCore::Policy)"),
    ContractGap("FA-GAP-COGNITO-GROUPS", "oidc-identity-provider", "AWS::Cognito::UserPoolGroup", "oidc-identity-provider", "roles are Cognito groups (design D2); the row lists no AWS::Cognito::UserPoolGroup"),
    ContractGap("FA-GAP-COGNITO-GROUPS", "oidc-identity-provider", "AWS::Cognito::UserPoolGroup", "ci-test-client", "the ci_test group"),
    ContractGap("FA-GAP-CI-SECRET", "oidc-identity-provider", "AWS::SecretsManager::Secret", "ci-test-client", "the ci_test client secret lives in Secrets Manager (design D2/D7); the row lists no AWS::SecretsManager::Secret"),
    ContractGap("FA-GAP-PIPELINE-LOGS", "pipeline-financeagent", "AWS::Logs::LogGroup", "pipeline-build-project", "explicit 30-day CodeBuild log groups (lesson L6), as contracts 0.2.1/1.0.0 added for FinancialPlanning and FinanceModel"),
)


def match_gap(resource_type: str, message: str) -> ContractGap | None:
    """The known gap a contract ownership problem is about (exact type + logical role), or None."""
    for g in KNOWN_GAPS:
        if resource_type != g.resource_type:
            continue
        if f"'{g.logical_role}'" in message or f"({g.logical_role})" in message:
            return g
    return None
