#!/usr/bin/env python3
"""Actions of each environment stage (tasks 5.2, 7.1, 7.3-7.5; FA-GW-02/03, FA-PL-03/04). They run in
the per-environment stage project from the BuildOutput artifact only (never the source checkout, never
``cdk synth``):

``resolve`` (first action, before any deploy)
    Promotion check (``scripts/check_contracts_pin.py --env``: a 0.x pin is beta-only; the release must
    carry a digest-pinned Runtime image), then :func:`scripts.release.resolve`: the FinanceLambdasTool
    compatibility gate and target resolution from THIS environment's released references, and the
    Bedrock grant. The results are written to ``--variables`` and exported by the buildspec as pipeline
    variables (namespace ``Resolve<Env>``) that the agent stack's deploy action passes as parameters.
    A rollback build re-registers the target set recorded for that release.
``publish``
    :func:`scripts.release.publish_release`; the registered target set is read back from the DEPLOYED
    agent stack's parameters (what CloudFormation actually applied); prod first reads the manual approval.
``tests``
    The environment suite (``integration-beta`` in beta, ``gamma`` = integration + gamma checks in gamma,
    ``smoke`` in prod) with ``FINPLAN_TARGET_ENV`` set: the suites send REAL payloads to the deployed
    Runtime and Gateway with the stage role's real credentials (lesson L5). Zero executed tests fail.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.release import NONE, DependencyMissing, ManifestError, ReleaseInfo, approval_record, publish_release, recorded_targets, resolve, write_variables  # noqa: E402

__all__ = ["SUITES", "deployed_targets", "main", "precheck_problems", "suite_counts", "tests_action"]

SUITES: dict[str, tuple[str, list[str]]] = {
    "beta": ("integration-beta", ["tests/integration_beta"]),
    "gamma": ("gamma", ["tests/integration_beta", "tests/gamma"]),
    "prod": ("smoke", ["tests/smoke"]),
}


def precheck_problems(env: str, info: ReleaseInfo, *, root: Path = ROOT) -> list[str]:
    from scripts.check_contracts_pin import check

    problems = [f"contracts pin: {p}" for p in check(root, env=env, check_installed=False)]
    if not (info.image_uri or "").count("@sha256:") == 1:
        problems.append("release-info.json carries no digest-pinned Runtime image (lesson L3: never a tag or a source-only artifact)")
    return problems


def deployed_targets(cfn: Any, env: str) -> dict[str, str]:
    """Tool -> Lambda reference as applied by CloudFormation to the agent stack (``none`` = not registered)."""
    from infra.stacks import naming as n
    from infra.stacks.agent import target_param
    from infra.stacks.tool_schemas import contract_tools

    stack = cfn.describe_stacks(StackName=n.agent_stack_name(env))["Stacks"][0]
    params = {p["ParameterKey"]: p.get("ParameterValue", NONE) for p in stack.get("Parameters") or []}
    return {t: params.get(target_param(t), NONE) for t in contract_tools()}


def suite_counts(junit_xml: Path) -> dict[str, int]:
    root = ET.parse(junit_xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {k: 0 for k in ("tests", "failures", "errors", "skipped")}
    for s in suites:
        for k in total:
            total[k] += int(s.get(k, 0))
    total["executed"] = total["tests"] - total["skipped"]
    return total


def tests_action(env: str, *, root: Path = ROOT, release_id: str | None = None, run: Callable[..., Any] = subprocess.run, environ: Mapping[str, str] | None = None, out: Callable[[str], None] = print) -> int:
    suite, paths = SUITES[env]
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        env_vars = {**(environ if environ is not None else os.environ), "FINPLAN_TARGET_ENV": env, "FINPLAN_SUITE": suite}
        env_vars.pop("FINPLAN_OFFLINE_TESTS", None)
        if release_id:
            env_vars["FINPLAN_RELEASE_ID"] = release_id
        proc = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *paths], cwd=root, env=env_vars)
        rc = int(getattr(proc, "returncode", 1))
        counts = suite_counts(junit) if junit.is_file() else {"tests": 0, "executed": 0, "failures": 0, "errors": 0, "skipped": 0}
    out(f"{suite}: {counts}")
    if rc == 5 or counts["executed"] < 1:
        out(f"FAIL: the {suite} suite executed no test (a stage with zero executed tests is a false pass)")
        return 1
    return 0 if rc == 0 else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CodeBuild entry point (needs AWS)
    ap = argparse.ArgumentParser(description="FinanceAgent stage actions: resolve, publish or tests.")
    ap.add_argument("action", choices=("resolve", "publish", "tests"))
    ap.add_argument("--env", required=True, choices=("beta", "gamma", "prod"))
    ap.add_argument("--release-info", type=Path, default=ROOT / "release-info.json")
    ap.add_argument("--pipeline-execution-id", default=None)
    ap.add_argument("--store", default=os.environ.get("FINPLAN_PIPELINE_STORE"))
    ap.add_argument("--variables", type=Path, default=None)
    args = ap.parse_args(argv)
    info = ReleaseInfo.load(args.release_info)
    if args.action == "tests":
        return tests_action(args.env, release_id=info.release_id)
    from finplan_agent.core.aws_clients import client, s3_client

    region = info.region
    ssm, cfn = client("ssm", region), client("cloudformation", region)
    s3 = s3_client(region)
    account = client("sts", region).get_caller_identity()["Account"]
    try:
        if args.action == "resolve":
            problems = precheck_problems(args.env, info)
            if problems:
                for p in problems:
                    print(f"FAIL: {p}", file=sys.stderr)
                return 1
            recorded = recorded_targets(s3, args.store, info.release_id, args.env) if (info.rollback and args.store) else None
            res = resolve(args.env, ssm=ssm, bedrock=client("bedrock", region), account=account, region=region, recorded_targets=recorded, s3=s3)
            for note in res.notes:
                print(f"[NOTE] {note}")
            print(f"{args.env}: registering {res.registered} (tool catalog {res.catalog_release_id}); Bedrock grant: {len(res.bedrock_arns)} ARN(s)")
            if args.variables:
                write_variables(args.variables, res.variables())
            return 0
        from infra.stacks import naming as n

        approval = approval_record(client("codepipeline", region), n.PIPELINE_NAME, str(args.pipeline_execution_id)) if args.env == "prod" else None
        manifest = publish_release(info, args.env, ssm=ssm, cfn=cfn, targets=deployed_targets(cfn, args.env), s3=s3, store_bucket=args.store, approval=approval)
        print(f"published {args.env} manifest for {manifest['release_id']} (previous {manifest['previous_release_id']}; outputs {sorted(manifest['outputs'])})")
        print(json.dumps({"registered": [t for t, v in deployed_targets(cfn, args.env).items() if v != NONE]}))
        return 0
    except (DependencyMissing, ManifestError) as exc:
        print(f"STAGE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
