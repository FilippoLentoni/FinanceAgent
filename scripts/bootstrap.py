#!/usr/bin/env python3
"""One-time authenticated bootstrap of the FinanceAgent pipeline (task 7.2; design D7, Migration Plan
step 2; contracts D6, D11, D12, D16; CONTRACT GAP-3 ordering). Runbook: ``docs/bootstrap.md``.

DO NOT RUN during implementation work. The user approved the bootstrap in principle on 2026-10-07; a
human runs it once, only after this IaC is synthesized, and only after the exact stacks and a cost
estimate have been shown and confirmed (``deploy`` typed).

The sequence is the contract package's :func:`finplan_contracts.bootstrap.run_bootstrap` (never
re-implemented); this entry point supplies the FinanceAgent specifics:

1. **Assembly** (:func:`bootstrap_assembly`): refuses without a synthesized assembly, copies ONLY the
   account-level stacks ``finplan-shared-financeagent-pipeline-store`` and
   ``finplan-shared-financeagent-tooling`` into ``cdk.out.bootstrap/`` and refuses an assembly that
   references the CDK bootstrap (``cdk-hnb659fds`` roles, ``cdk-*-assets`` buckets) or carries
   container-image assets (lesson L1).
2. **Pre-run plan, caller, region, connection, scoped-role checks** (contract).
3. **Local configuration**, read like the platform's: ``--config PATH``, else ``$FINPLAN_BOOTSTRAP_CONFIG``,
   else the shared ``~/.finplan/bootstrap.json`` (account, primary region, the EXISTING connection),
   refused inside the repository; platform-only keys are ignored and never printed. Without a
   ``codeconnection_arn`` the platform's published connection is reused (read-only).
4. **Deploy** (:class:`ToolingDeployer`): ``npx aws-cdk@2 deploy --all`` of the filtered assembly,
   then, as the ``bootstrap`` writer (:func:`finplan_contracts.ssm.check_write`):

   * ``/finplan/<env>/financeagent/agent/gateway-principal-ref`` for beta, gamma and prod (the Gateway
     service roles exist BEFORE FinanceLambdasTool's Gateway-facing release; GAP-3);
   * ``/finplan/shared/financeagent/config/budget-enforced-role-names`` (pipeline and build roles, D16).

   No budget and no budget parameter is written (FinancialPlanning owns them; lesson L6). It reports,
   read-only, whether the configured explanation model's inference profile is ACTIVE in the region
   (model access for it is enabled by the operator in the Bedrock console: a human step).
5. **Source-stage dry run** (contract).

Credentials come from the default boto3 chain only (the DevDesktop instance role works as is).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finplan_contracts import bootstrap as contract_bootstrap  # noqa: E402
from finplan_contracts import budget as contract_budget  # noqa: E402
from finplan_contracts import ssm as contract_ssm  # noqa: E402
from finplan_contracts.bootstrap import BootstrapConfig, BootstrapStop, Clients, Plan  # noqa: E402

from infra.stacks import naming as n  # noqa: E402
from infra.stacks.tooling import STORE_STACK_NAME, TOOLING_STACK_NAME  # noqa: E402

__all__ = ["BOOTSTRAP_STACKS", "ToolingDeployer", "bootstrap_assembly", "cdk_bootstrap_references", "load_bootstrap_config", "main", "publish_bootstrap_parameters", "read_local_config", "report_model_access", "run"]

BOOTSTRAP_STACKS = (STORE_STACK_NAME, TOOLING_STACK_NAME)
DEFAULT_ASSEMBLY = ROOT / "cdk.out"
BOOTSTRAP_ASSEMBLY = ROOT / "cdk.out.bootstrap"
DEFAULT_CONFIG = Path("~/.finplan/financeagent-bootstrap.json")
SHARED_CONFIG = contract_bootstrap.DEFAULT_CONFIG_PATH
_PLATFORM_ONLY_KEYS = ("repo", "github_repository", "pipeline_name", "source_stage", "next_stage", "budget_notification_email", "scope_budget_to_project_tag")
DRY_RUN_RECORD = Path("~/.finplan/financeagent-source-dry-run.json")
PLATFORM_CONNECTION_PARAMETER = contract_ssm.build(contract_ssm.SHARED, "financialplanning", "config", "codeconnection-ref")
SHARED_ENFORCED_ROLES_PARAMETER = contract_ssm.build(contract_ssm.SHARED, n.REPO, "config", contract_ssm.BUDGET_ENFORCED_ROLE_NAMES)
_CDK_BOOTSTRAP_RE = re.compile(r"cdk-hnb659fds|cdk-[a-z0-9]+-assets-")


# ===================================================================== assembly
def cdk_bootstrap_references(path: Path) -> list[str]:
    return [p.relative_to(path).as_posix() for p in sorted(path.rglob("*.json")) if _CDK_BOOTSTRAP_RE.search(p.read_text(encoding="utf-8", errors="replace"))]


def bootstrap_assembly(assembly: str | os.PathLike[str], out: str | os.PathLike[str]) -> Path:
    src = Path(assembly)
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        raise BootstrapStop("prerun", f"the bootstrap IaC is not synthesized: no cloud assembly at {src} (run: uv run python scripts/synth.py). Nothing was deployed.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    arts = manifest.get("artifacts") or {}
    keep = {aid: a for aid, a in arts.items() if a.get("type") == "aws:cloudformation:stack" and (a.get("properties") or {}).get("stackName") in BOOTSTRAP_STACKS}
    names = {(a.get("properties") or {}).get("stackName") for a in keep.values()}
    missing = [s for s in BOOTSTRAP_STACKS if s not in names]
    if missing:
        raise BootstrapStop("prerun", f"the synthesized assembly does not contain the tooling stacks {missing}; nothing was deployed")
    for _aid, art in list(keep.items()):
        for dep in art.get("dependencies") or []:
            if dep in arts and arts[dep].get("type") == "cdk:asset-manifest":
                keep[dep] = arts[dep]
    dst = Path(out)
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    files: set[str] = set()
    for art in keep.values():
        props = art.get("properties") or {}
        for key in ("templateFile", "file"):
            if props.get(key):
                files.add(props[key])
        # The CLI opens every artifact's metadata file; leaving it out made the first deploy fail
        # with ENOENT before any stack was created.
        if art.get("additionalMetadataFile"):
            files.add(art["additionalMetadataFile"])
        if art.get("type") == "cdk:asset-manifest":
            doc = json.loads((src / props["file"]).read_text(encoding="utf-8"))
            for asset in (doc.get("files") or {}).values():
                files.add(str((asset.get("source") or {}).get("path")))
            if doc.get("dockerImages"):
                raise BootstrapStop("prerun", "the tooling stacks must not contain container-image assets")
    for rel in sorted(files):
        s = src / rel
        if s.is_dir():
            shutil.copytree(s, dst / rel)
        elif s.is_file():
            shutil.copy2(s, dst / rel)
        else:
            raise BootstrapStop("prerun", f"{rel} is missing from the cloud assembly")
    out_manifest = {**{k: v for k, v in manifest.items() if k != "artifacts"}, "artifacts": {}}
    for aid, art in keep.items():
        art = json.loads(json.dumps(art))
        art["dependencies"] = [d for d in art.get("dependencies") or [] if d in keep]
        props = art.get("properties") or {}
        if "additionalDependencies" in props:
            props["additionalDependencies"] = [d for d in props["additionalDependencies"] if d in keep]
        out_manifest["artifacts"][aid] = art
    (dst / "manifest.json").write_text(json.dumps(out_manifest, indent=1) + "\n", encoding="utf-8")
    refs = cdk_bootstrap_references(dst)
    if refs:
        raise BootstrapStop("prerun", f"the bootstrap assembly references the CDK bootstrap stack (cdk-hnb659fds roles or cdk-*-assets buckets) in {refs}; it must deploy without CDKToolkit")
    return dst


# ===================================================================== configuration
def _get(ssm: Any, name: str) -> str | None:
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def read_local_config(path: str | os.PathLike[str] | None = None, *, environ: Mapping[str, str] | None = None, overlay: Path | None = DEFAULT_CONFIG, repo_root: Path = ROOT) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    shared = Path(path) if path else Path(env[contract_bootstrap.CONFIG_ENV]) if env.get(contract_bootstrap.CONFIG_ENV) else SHARED_CONFIG
    data: dict[str, Any] = {}
    for candidate, drop in ((shared, _PLATFORM_ONLY_KEYS), (overlay, _PLATFORM_ONLY_KEYS[3:])):
        if candidate is None:
            continue
        candidate = Path(candidate).expanduser()
        if not candidate.is_file():
            continue
        if _inside(candidate, repo_root):
            raise BootstrapStop("configuration", f"bootstrap configuration {candidate} is inside the repository; keep it local and untracked (for example ~/.finplan/bootstrap.json)")
        doc = json.loads(candidate.read_text(encoding="utf-8"))
        data.update({k: v for k, v in doc.items() if k not in drop})
    for env_key, key in (("FINPLAN_ACCOUNT_ID", "account_id"), ("FINPLAN_PRIMARY_REGION", "primary_region"), ("FINPLAN_CODECONNECTION_ARN", "codeconnection_arn")):
        if env.get(env_key):
            data[key] = env[env_key]
    return data


def load_bootstrap_config(local: Mapping[str, Any], ssm: Any) -> BootstrapConfig:
    shared = json.loads((ROOT / "config" / "shared.json").read_text(encoding="utf-8"))
    data = dict(local)
    data["repo"] = n.REPO
    data.setdefault("github_repository", str((shared.get("source") or {}).get("repository") or "FilippoLentoni/FinanceAgent"))
    data["pipeline_name"] = n.PIPELINE_NAME
    for key in _PLATFORM_ONLY_KEYS[5:]:
        data.pop(key, None)
    if not data.get("codeconnection_arn"):
        reused = _get(ssm, PLATFORM_CONNECTION_PARAMETER)
        if not reused:
            raise BootstrapStop("connection", f"no CodeConnection in the local configuration and none published at {PLATFORM_CONNECTION_PARAMETER}; bootstrap FinancialPlanning first or name the existing connection locally")
        data["codeconnection_arn"] = reused
    try:
        return BootstrapConfig.from_mapping(data)
    except ValueError as exc:
        raise BootstrapStop("configuration", str(exc)) from None


# ===================================================================== parameters
def _write(ssm: Any, name: str, value: str, description: str, out: Callable[[str], None]) -> None:
    decision = contract_ssm.check_write(name, contract_ssm.Writer(n.REPO, "bootstrap"))
    problems = list(decision.reasons) + contract_ssm.validate_value(name, value)
    if problems:
        raise BootstrapStop("parameters", f"{name}: " + "; ".join(problems))
    if _get(ssm, name) == value:
        out(f"[OK] {name} already {value}")
        return
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True, Description=description)
    out(f"[WROTE] {name} = {value}")


def publish_bootstrap_parameters(ssm: Any, *, out: Callable[[str], None] = print) -> dict[str, str]:
    """Gateway principals (GAP-3: before FinanceLambdasTool's Gateway-facing release) and the shared
    tooling role names for the platform's budget action (D16). Role NAMES only, never ARNs."""
    written: dict[str, str] = {}
    for env in n.ENVIRONMENTS:
        name = n.own_ssm(env, "agent", "gateway-principal-ref")
        _write(ssm, name, n.gateway_role_name(env), f"FinanceAgent {env} Gateway service role (FinanceLambdasTool grants it invoke)", out)
        written[name] = n.gateway_role_name(env)
    value = ",".join(n.tooling_role_names())
    _write(ssm, SHARED_ENFORCED_ROLES_PARAMETER, value, "FinanceAgent account-level tooling roles the platform budget action denies at 100%", out)
    written[SHARED_ENFORCED_ROLES_PARAMETER] = value
    out("[ACTION] re-run the FinanceLambdasTool pipeline (its pre-deploy step reads gateway-principal-ref) and, after the first FinanceAgent beta deploy, the FinancialPlanning bootstrap (its budget action reads the published role names)")
    return written


def report_model_access(bedrock: Any | None, *, out: Callable[[str], None] = print) -> dict[str, str]:
    """Read-only: is each environment's configured inference profile ACTIVE here? (task 3.9 pre-check)."""
    status: dict[str, str] = {}
    for env in n.ENVIRONMENTS:
        cfg = json.loads((ROOT / "config" / f"{env}.json").read_text(encoding="utf-8"))
        expl = cfg.get("explanation") or {}
        model_id = str(expl.get("model_id") or "")
        if bedrock is None or not model_id:
            status[env] = "unchecked"
            continue
        try:
            prof = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
            status[env] = str(prof.get("status", "unknown"))
        except Exception as exc:  # noqa: BLE001 - read-only report
            status[env] = f"unavailable ({type(exc).__name__})"
        out(f"[{'OK' if status[env] == 'ACTIVE' else 'ACTION'}] {env}: explanation model profile {status[env]} (provider {expl.get('provider')}); model access is enabled by the operator in the Bedrock console before any switch to bedrock")
    return status


# ===================================================================== deploy
class ToolingDeployer:
    def __init__(self, assembly: Path, ssm: Any, *, region: str, dry_run_passed: bool, runner: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print, bedrock: Any | None = None) -> None:
        self.assembly = assembly
        self.ssm = ssm
        self.region = region
        self.dry_run_passed = dry_run_passed
        self.runner = runner
        self.out = out
        self.bedrock = bedrock
        self.commands: list[list[str]] = []

    def command(self) -> list[str]:
        return [
            "npx",
            "--yes",
            "aws-cdk@2",
            "deploy",
            "--app",
            str(self.assembly),
            "--all",
            "--require-approval",
            "never",
            "--progress",
            "events",
            "--parameters",
            f"{TOOLING_STACK_NAME}:SourceDryRunPassed={'true' if self.dry_run_passed else 'false'}",
        ]

    def __call__(self, stack_names: list[str]) -> None:
        if sorted(stack_names) != sorted(BOOTSTRAP_STACKS):
            raise BootstrapStop("deploy", f"the bootstrap deploys only {list(BOOTSTRAP_STACKS)}, got {stack_names}")
        allocation = _get(self.ssm, contract_budget.ALLOCATION_PARAMETER)
        if allocation is None:
            self.out(f"[WARN] {contract_budget.ALLOCATION_PARAMETER} is absent: the agent's budget check uses the contract defaults until the FinancialPlanning bootstrap writes it (FinanceAgent never writes it)")
        else:
            self.out(f"[OK] shared budget allocation (FinancialPlanning-owned, read-only): {allocation}")
        cmd = self.command()
        self.commands.append(cmd)
        self.out("running: " + " ".join(cmd))
        env = {**os.environ, "AWS_REGION": self.region, "AWS_DEFAULT_REGION": self.region, "CDK_DISABLE_VERSION_CHECK": "1"}
        proc = self.runner(cmd, cwd=str(ROOT), env=env)
        if int(getattr(proc, "returncode", 1)) != 0:
            raise BootstrapStop("deploy", "cdk deploy of the FinanceAgent tooling stacks failed (CloudFormation rolls back automatically)")
        publish_bootstrap_parameters(self.ssm, out=self.out)
        report_model_access(self.bedrock, out=self.out)


def _interactive_approve(plan: Plan) -> bool:  # pragma: no cover - interactive
    print("\nThe stacks and cost estimate above will be deployed under the user's in-principle approval of 2026-10-07.")
    return input("Type 'deploy' to deploy exactly these stacks, anything else to stop: ").strip() == "deploy"


def run(
    config: BootstrapConfig,
    clients: Clients,
    *,
    session_region: str | None,
    assembly: Path = DEFAULT_ASSEMBLY,
    bootstrap_dir: Path = BOOTSTRAP_ASSEMBLY,
    approve: Callable[[Plan], bool] = _interactive_approve,
    runner: Callable[..., Any] = subprocess.run,
    record_path: Path | None = None,
    sleep: Callable[[float], None] | None = None,
    bedrock: Any | None = None,
    out: Callable[[str], None] = print,
) -> contract_bootstrap.Report:
    filtered = bootstrap_assembly(assembly, bootstrap_dir)
    record = Path(record_path).expanduser() if record_path else None
    passed = bool(record and record.is_file() and json.loads(record.read_text(encoding="utf-8")).get("deploy_stages_enabled") is True)
    deployer = ToolingDeployer(filtered, clients.ssm, region=config.primary_region, dry_run_passed=passed, runner=runner, out=out, bedrock=bedrock)
    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return contract_bootstrap.run_bootstrap(config, clients, session_region=session_region, assembly_dir=filtered, approve=approve, deployer=deployer, out=out, record_path=record, **kwargs)


def make_clients(region: str) -> tuple[Clients, Any]:  # pragma: no cover - needs AWS
    import boto3

    session = boto3.session.Session(region_name=region)
    return Clients(
        sts=session.client("sts"), codeconnections=session.client("codeconnections"), ssm=session.client("ssm"), codepipeline=session.client("codepipeline"), pricing=session.client("pricing", region_name="us-east-1")
    ), session.client("bedrock")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - the authenticated run is a human step
    ap = argparse.ArgumentParser(description="One-time authenticated bootstrap of the FinanceAgent pipeline. Read docs/bootstrap.md first.")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--assembly", type=Path, default=DEFAULT_ASSEMBLY)
    ap.add_argument("--record", type=Path, default=DRY_RUN_RECORD)
    args = ap.parse_args(argv)
    try:
        local = read_local_config(args.config)
    except (BootstrapStop, ValueError, OSError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    region = str(local.get("primary_region") or "us-east-2")
    clients, bedrock = make_clients(region)
    args.record.expanduser().parent.mkdir(parents=True, exist_ok=True)
    try:
        config = load_bootstrap_config(local, clients.ssm)
        run(config, clients, session_region=region, assembly=args.assembly, record_path=args.record, bedrock=bedrock)
    except BootstrapStop as stop:
        print(f"[STOPPED] {stop.step}: {stop.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
