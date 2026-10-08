#!/usr/bin/env python3
"""Post-synth gates of the FinanceAgent build stage (tasks 1.4, 3.7, 4.4, 5.0, 5.1, 7.1; FA-PL-01,
FA-PL-02, FA-POL-01, FA-POL-07, FA-PRV-08, FA-PRV-09, FA-SS-04, FA-GW-01). Offline; every contract
check reuses the pinned ``finplan-contracts`` package.

=====================  =======================================================================
gate                   what
=====================  =======================================================================
ownership              ``finplan_contracts.ownership`` per template (OWN-01); only the exact
                       contract gaps of :mod:`infra.stacks.contract_gaps` are tolerated (reported)
boundaries             permission boundary on every role (ENV-18), shared-resource rule (ENV-16)
live-perm-scan-synth   ``finplan_contracts.live_perms`` over every template (ENV-05)
pipeline-structure     ``finplan_contracts.pipeline_check`` (ENV-09) and scoped deploy roles
                       (``bootstrap.check_deploy_roles``, ENV-12) on the tooling template
no-cdk-bootstrap       lesson L1: no template or asset manifest references ``cdk-hnb659fds`` roles
                       or ``cdk-*-assets`` buckets
no-budget              lesson L6: FinanceAgent declares no AWS Budgets resource
log-retention          lesson L6: every declared log group keeps 30 days
dynamodb-policies      lesson L2: no DynamoDB resource policy lists stream actions
runtime-role           FA-POL-01 / FA-PRV-08: the Runtime role allows no Lambda / execute-api
                       call, denies them explicitly, and its only Bedrock grant is
                       ``InvokeModel`` + ``InvokeModelWithResponseStream`` on the resolved
                       ``BedrockInvokeArns`` parameter (no wildcard, absent while ``none``)
environment-binding    FA-POL-07 / FA-GW-01: one pool per identity template; one Gateway and one
                       Runtime per agent template, both with a CUSTOM_JWT authorizer imported from
                       the SAME environment's identity stack; target Lambda parameters admit only
                       this environment's FinanceLambdasTool functions; the Runtime image is a
                       digest-pinned parameter (L3: never a tag or source)
memory                 FA-SS-04: no long-term memory strategy; expiry 7-365 days
budget-roles           FA-PRV-09 / L6: the published budget-enforced role names include the
                       Runtime role of every environment
template-leaks         ``finplan_contracts.leak_scan`` over the synthesized templates (no account
                       IDs, ARNs with accounts, pool/client IDs)
=====================  =======================================================================

Usage: ``uv run python scripts/infra_gates.py --assembly cdk.out [--only GATE ...]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from infra.stacks import naming as n  # noqa: E402

__all__ = ["GATES", "GateContext", "main", "run_gates", "templates_of"]

_CDK_BOOTSTRAP_RE = re.compile(r"cdk-hnb659fds|cdk-[a-z0-9]+-assets-")
_STREAM_ACTIONS = ("dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:DescribeStream", "dynamodb:ListStreams")


@dataclass
class GateContext:
    assembly: Path
    notes: dict[str, list[str]] = field(default_factory=dict)

    def note(self, gate: str, msg: str) -> None:
        self.notes.setdefault(gate, []).append(msg)


def templates_of(assembly: Path) -> list[Path]:
    return sorted(p for p in assembly.rglob("*.template.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resources(t: dict[str, Any], rtype: str) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in (t.get("Resources") or {}).items() if isinstance(v, dict) and v.get("Type") == rtype}


def _kind(path: Path) -> str:
    name = path.name
    if name.startswith("Tooling"):
        return "tooling"
    if name.startswith("PipelineStore"):
        return "store"
    for env in n.ENVIRONMENTS:
        if name.startswith(env.capitalize() + "Identity"):
            return f"identity:{env}"
        if name.startswith(env.capitalize() + "Agent"):
            return f"agent:{env}"
    return "other"


# ===================================================================== gates
def gate_ownership(ctx: GateContext) -> list[str]:
    from finplan_contracts.ownership import check_file

    from infra.stacks.contract_gaps import match_gap

    problems: list[str] = []
    for path in templates_of(ctx.assembly):
        report = check_file(path, n.REPO)
        gap_lids: set[str] = set()
        rest = []
        for p in report.problems:
            g = match_gap(p.resource_type, p.message)
            if g is not None:
                gap_lids.add(p.logical_id)
                ctx.note("ownership", f"CONTRACT GAP {g.gap_id} ({g.row}): {path.name} {p.logical_id} {p.resource_type}")
            else:
                rest.append(p)
        for p in rest:
            helper_of = re.match(r"CDK-generated helper of ([A-Za-z0-9, ]+?), which", p.message)
            if helper_of and {x.strip() for x in helper_of.group(1).split(",")} <= gap_lids:
                ctx.note("ownership", f"helper of a contract-gap resource: {path.name} {p.logical_id}")
                continue
            problems.append(f"{path.name}: {p}")
    return problems


def gate_boundaries(ctx: GateContext) -> list[str]:
    from finplan_contracts.boundaries import check_role_boundaries, check_shared_resources

    problems = []
    for path in templates_of(ctx.assembly):
        t = _load(path)
        kind = _kind(path)
        env = kind.split(":", 1)[1] if ":" in kind else None
        problems += [f"{path.name}: {f}" for f in check_role_boundaries(t, env)]
        problems += [f"{path.name}: {f}" for f in check_shared_resources(t, repo=n.REPO)]
    return problems


def gate_live_perms(ctx: GateContext) -> list[str]:
    from finplan_contracts.live_perms import scan_document

    return [f"{path.name}: {f}" for path in templates_of(ctx.assembly) for f in scan_document(_load(path), path.name)]


def gate_pipeline_structure(ctx: GateContext) -> list[str]:
    from finplan_contracts.bootstrap import check_deploy_roles
    from finplan_contracts.pipeline_check import check_pipeline_template

    tooling = [p for p in templates_of(ctx.assembly) if _kind(p) == "tooling"]
    if not tooling:
        return ["no tooling template (the pipeline) in the assembly"]
    t = _load(tooling[0])
    return [str(f) for f in check_pipeline_template(t)] + check_deploy_roles(t)


def gate_no_cdk_bootstrap(ctx: GateContext) -> list[str]:
    out = []
    for p in sorted(ctx.assembly.rglob("*.json")):
        if p.name in ("tree.json", "manifest.json") or p.name.endswith(".metadata.json"):
            continue
        if _CDK_BOOTSTRAP_RE.search(p.read_text(encoding="utf-8", errors="replace")):
            out.append(f"{p.relative_to(ctx.assembly)} references the CDK bootstrap stack (cdk-hnb659fds roles / cdk-*-assets buckets); FinanceAgent deploys without CDKToolkit (lesson L1)")
    return out


def gate_no_budget(ctx: GateContext) -> list[str]:
    return [
        f"{p.name}: {lid} ({r['Type']}): FinanceAgent creates no budget (FinancialPlanning owns it; lesson L6)"
        for p in templates_of(ctx.assembly)
        for lid, r in (_load(p).get("Resources") or {}).items()
        if str(r.get("Type", "")).startswith("AWS::Budgets::")
    ]


def gate_log_retention(ctx: GateContext) -> list[str]:
    out = []
    for p in templates_of(ctx.assembly):
        for lid, r in _resources(_load(p), "AWS::Logs::LogGroup").items():
            if (r.get("Properties") or {}).get("RetentionInDays") != n.LOG_RETENTION_DAYS:
                out.append(f"{p.name}: log group {lid} does not keep exactly {n.LOG_RETENTION_DAYS} days (lesson L6)")
    return out


def gate_dynamodb_policies(ctx: GateContext) -> list[str]:
    out = []
    for p in templates_of(ctx.assembly):
        for lid, r in _resources(_load(p), "AWS::DynamoDB::Table").items():
            pol = json.dumps((r.get("Properties") or {}).get("ResourcePolicy") or {})
            hits = [a for a in _STREAM_ACTIONS if a in pol]
            if hits:
                out.append(f"{p.name}: table {lid} resource policy lists stream actions {hits} (DynamoDB rejects them; lesson L2)")
    return out


def _statements(doc: Any) -> list[dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    st = doc.get("Statement") or []
    return [s for s in (st if isinstance(st, list) else [st]) if isinstance(s, dict)]


def _actions(s: dict[str, Any]) -> list[str]:
    a = s.get("Action") or []
    return [str(x) for x in (a if isinstance(a, list) else [a])]


def runtime_role_problems(t: dict[str, Any], name: str) -> list[str]:
    out = []
    roles = {lid: r for lid, r in _resources(t, "AWS::IAM::Role").items() if json.dumps((r.get("Properties") or {}).get("RoleName", "")).find("-runtime-role") >= 0}
    if len(roles) != 1:
        return [f"{name}: expected exactly one Runtime role, found {len(roles)}"]
    lid, role = next(iter(roles.items()))
    docs = [pol.get("PolicyDocument") for pol in (role.get("Properties") or {}).get("Policies") or []]
    for plid, pol in _resources(t, "AWS::IAM::Policy").items():
        if lid in json.dumps((pol.get("Properties") or {}).get("Roles")):
            docs.append((pol.get("Properties") or {}).get("PolicyDocument"))
            if plid != "RuntimeBedrockInvoke" and "Condition" not in pol:
                pass
    allows = [s for d in docs for s in _statements(d) if s.get("Effect") == "Allow"]
    denies = [s for d in docs for s in _statements(d) if s.get("Effect") == "Deny"]
    for s in allows:
        for a in _actions(s):
            al = a.lower()
            if al.startswith("lambda:") or al.startswith("execute-api:") or al == "*" or al.endswith(":*"):
                out.append(f"{name}: Runtime role allows {a} (single tool path: tools only through the Gateway, FA-POL-01)")
            if al.startswith("bedrock:"):
                if al not in ("bedrock:invokemodel", "bedrock:invokemodelwithresponsestream"):
                    out.append(f"{name}: Runtime role allows {a}; only InvokeModel and InvokeModelWithResponseStream are permitted (FA-PRV-08)")
                if s.get("Resource") != {"Ref": "BedrockInvokeArns"}:
                    out.append(f"{name}: the Runtime role's Bedrock grant must be scoped to the resolved BedrockInvokeArns parameter, got {s.get('Resource')!r}")
    if not any("lambda:InvokeFunction" in _actions(s) for s in denies):
        out.append(f"{name}: Runtime role does not explicitly deny lambda:InvokeFunction")
    bedrock_pol = _resources(t, "AWS::IAM::Policy").get("RuntimeBedrockInvoke")
    if bedrock_pol is None or bedrock_pol.get("Condition") != "HasBedrockGrant":
        out.append(f"{name}: the Bedrock grant must be a separate policy conditioned on HasBedrockGrant (no grant while the provider is fixture)")
    return out


def gate_runtime_role(ctx: GateContext) -> list[str]:
    return [p for path in templates_of(ctx.assembly) if _kind(path).startswith("agent:") for p in runtime_role_problems(_load(path), path.name)]


def environment_problems(t: dict[str, Any], name: str, env: str) -> list[str]:
    out = []
    gws = _resources(t, "AWS::BedrockAgentCore::Gateway")
    rts = _resources(t, "AWS::BedrockAgentCore::Runtime")
    if len(gws) != 1 or len(rts) != 1:
        out.append(f"{name}: expected exactly one Gateway and one Runtime, found {len(gws)} and {len(rts)}")
    identity_prefix = n.identity_stack_name(env) + ":"
    for kind, res in (("Gateway", gws), ("Runtime", rts)):
        for lid, r in res.items():
            props = r.get("Properties") or {}
            if kind == "Gateway" and props.get("AuthorizerType") != "CUSTOM_JWT":
                out.append(f"{name}: {lid} authorizer is {props.get('AuthorizerType')!r}, not CUSTOM_JWT")
            jwt = ((props.get("AuthorizerConfiguration") or {}).get("CustomJWTAuthorizer")) or {}
            imports = re.findall(r'"Fn::ImportValue": "([^"]+)"', json.dumps(jwt))
            if not jwt.get("DiscoveryUrl") or not imports or any(not i.startswith(identity_prefix) for i in imports):
                out.append(f"{name}: {lid} JWT authorizer is not bound to the {env} identity stack (imports {imports})")
            if kind == "Runtime":
                allow = ((props.get("RequestHeaderConfiguration") or {}).get("RequestHeaderAllowlist")) or []
                if "Authorization" not in allow:
                    out.append(f"{name}: {lid} does not pass the Authorization header to the agent")
                uri = (((props.get("AgentRuntimeArtifact") or {}).get("ContainerConfiguration")) or {}).get("ContainerUri")
                if uri != {"Ref": "ImageUri"}:
                    out.append(f"{name}: {lid} image must be the digest-pinned ImageUri parameter (got {uri!r}; lesson L3)")
    params = t.get("Parameters") or {}
    img = params.get("ImageUri") or {}
    if "@sha256:" not in str(img.get("AllowedPattern", "")):
        out.append(f"{name}: ImageUri must only admit digest references")
    for pname, p in params.items():
        if pname.startswith("Target") and pname.endswith("Arn"):
            pat = str(p.get("AllowedPattern", ""))
            if f"finplan-{env}-financelambdastool-" not in pat or any(f"finplan-{o}-" in pat for o in n.ENVIRONMENTS if o != env):
                out.append(f"{name}: {pname} admits Lambda references outside {env} ({pat})")
    return out


def gate_environment_binding(ctx: GateContext) -> list[str]:
    out = []
    for path in templates_of(ctx.assembly):
        kind = _kind(path)
        if kind.startswith("agent:"):
            out += environment_problems(_load(path), path.name, kind.split(":")[1])
        elif kind.startswith("identity:"):
            t = _load(path)
            pools = _resources(t, "AWS::Cognito::UserPool")
            if len(pools) != 1:
                out.append(f"{path.name}: expected exactly one user pool, found {len(pools)}")
            for lid, r in pools.items():
                if not ((r.get("Properties") or {}).get("AdminCreateUserConfig") or {}).get("AllowAdminCreateUserOnly"):
                    out.append(f"{path.name}: {lid} allows self sign-up")
            groups = {(r.get("Properties") or {}).get("GroupName") for r in _resources(t, "AWS::Cognito::UserPoolGroup").values()}
            missing = {"viewer", "researcher", "plan_editor", "plan_publisher", "ci_test"} - groups
            if missing:
                out.append(f"{path.name}: missing groups {sorted(missing)}")
    return out


def gate_memory(ctx: GateContext) -> list[str]:
    out = []
    for path in templates_of(ctx.assembly):
        for lid, r in _resources(_load(path), "AWS::BedrockAgentCore::Memory").items():
            props = r.get("Properties") or {}
            if props.get("MemoryStrategies"):
                out.append(f"{path.name}: {lid} configures long-term memory strategies (FA-SS-04)")
            if not (7 <= int(props.get("EventExpiryDuration", 0)) <= 365):
                out.append(f"{path.name}: {lid} event expiry outside 7-365 days")
    return out


def gate_budget_roles(ctx: GateContext) -> list[str]:
    return [f"{env}: the budget-enforced role names do not include the Runtime role" for env in n.ENVIRONMENTS if n.runtime_role_name(env) not in n.budget_enforced_role_names(env)]


def gate_template_leaks(ctx: GateContext) -> list[str]:
    from finplan_contracts.leak_scan import scan_text

    return [f"{p.name}: {f}" for p in templates_of(ctx.assembly) for f in scan_text(p.read_text(encoding="utf-8"), p.name)]


Gate = tuple[str, Callable[[GateContext], list[str]]]
GATES: list[Gate] = [
    ("ownership", gate_ownership),
    ("boundaries", gate_boundaries),
    ("live-perm-scan-synth", gate_live_perms),
    ("pipeline-structure", gate_pipeline_structure),
    ("no-cdk-bootstrap", gate_no_cdk_bootstrap),
    ("no-budget", gate_no_budget),
    ("log-retention", gate_log_retention),
    ("dynamodb-policies", gate_dynamodb_policies),
    ("runtime-role", gate_runtime_role),
    ("environment-binding", gate_environment_binding),
    ("memory", gate_memory),
    ("budget-roles", gate_budget_roles),
    ("template-leaks", gate_template_leaks),
]


def run_gates(ctx: GateContext, only: Iterable[str] | None = None) -> dict[str, list[str]]:
    sel = set(only or [g for g, _ in GATES])
    return {name: fn(ctx) for name, fn in GATES if name in sel}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FinanceAgent post-synth gates (offline).")
    ap.add_argument("--assembly", type=Path, default=ROOT / "cdk.out")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    ctx = GateContext(args.assembly)
    results = run_gates(ctx, args.only)
    ok = True
    for name, problems in results.items():
        for note in ctx.notes.get(name, []):
            print(f"[NOTE] {name}: {note}")
        if problems:
            ok = False
            for p in problems:
                print(f"FAIL [{name}] {p}")
        else:
            print(f"PASS [{name}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
