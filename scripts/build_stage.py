#!/usr/bin/env python3
"""The FinanceAgent pipeline's Build stage (tasks 1.4, 3.12, 7.1; FA-PL-02, FA-PL-03, FA-PRV-14; contracts D6).

Normal build (``--rollback-to`` empty or ``none``):

1. pre-synth gates: contract pin (version + wheel digest), agent gates (architecture, model-ID scan,
   provider kind, WebSocket), the configuration check (offline tests use fixtures; every configured model ID
   passes the provider validation), and the offline suites ``tests/unit tests/graph tests/contract``
   under the offline harness (no network, no AWS, NO Bedrock call: FA-PRV-14);
2. ``cdk synth`` ONCE (:func:`scripts.synth.synth`), then the post-synth gates
   (:mod:`scripts.infra_gates`: ownership, boundaries, live permissions, pipeline structure, lessons
   L1/L2/L6, Runtime role scope, environment binding, memory, budget roles, template leaks);
3. the Runtime image built ONCE for linux/arm64, import-checked with its own interpreter (lesson L3:
   a failing import pushes nothing), pushed, pinned BY DIGEST (:func:`scripts.runtime_image.build_and_push`);
4. BuildOutput: the assembly, ``release-info.json`` (new ``release_id``, artifact digest over the
   assembly and the image digest, contract pin, image URI) and the files the post-deploy actions need;
   the pipeline variables ``RELEASE_ID`` / ``IMAGE_URI`` (``--variables``); the release ledger copy.

Any failure raises :class:`BuildFailed` before anything is written to ``--out``: a failing gate produces
no artifact. Rollback (``--rollback-to rel_...``): the recorded release's stored BuildOutput is
re-emitted (digest re-verified); nothing is rebuilt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.release import ReleaseInfo, assembly_digest, contract_pin, fetch_build_output, image_uri, mint_release_id, store_build_output, write_variables  # noqa: E402

__all__ = ["PACKAGE_PATHS", "BuildFailed", "config_problems", "main", "pre_gates", "run_build", "run_rollback"]

#: Copied into BuildOutput for the post-deploy actions (they never read the source checkout).
PACKAGE_PATHS = ("pyproject.toml", "uv.lock", "README.md", "contracts-pin.json", "cdk.json", "vendor", "agent", "config", "policy", "skills", "scripts", "tests", "infra")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "cdk.out")
OFFLINE_SUITES = ("tests/unit", "tests/graph", "tests/contract")


class BuildFailed(RuntimeError):
    pass


def _has_key(node: Any, keys: tuple[str, ...]) -> bool:
    if isinstance(node, dict):
        return any(k in keys or _has_key(v, keys) for k, v in node.items())
    if isinstance(node, list):
        return any(_has_key(v, keys) for v in node)
    return False


def config_problems(root: Path = ROOT) -> list[str]:
    """Environment configuration: valid hosted provider kinds and model IDs; no prices."""
    from finplan_agent.config.provider import model_id_problems

    out = []
    for env in ("beta", "gamma", "prod"):
        cfg = json.loads((root / "config" / f"{env}.json").read_text(encoding="utf-8"))
        expl = cfg.get("explanation") or {}
        kind = expl.get("provider")
        if kind not in ("fixture", "bedrock"):
            out.append(f"config/{env}.json: explanation.provider must be fixture or bedrock")
        if kind not in (cfg.get("provider_kind_allowed") or []):
            out.append(f"config/{env}.json: explanation.provider {kind!r} is not in provider_kind_allowed")
        out += [f"config/{env}.json: explanation.model_id: {p}" for p in model_id_problems(expl.get("model_id"))]
        if _has_key(cfg, ("rates", "input_per_1k_usd", "output_per_1k_usd")):
            out.append(f"config/{env}.json: no rates or prices in repository configuration")
    return out


def pre_gates(root: Path = ROOT, *, run_tests: bool = True, run: Callable[..., Any] = subprocess.run, log: Callable[[str], None] = print) -> list[str]:
    from scripts.agent_gates import run_all
    from scripts.check_contracts_pin import check

    problems = [f"contracts pin: {p}" for p in check(root)]
    problems += [f"agent gate: {f}" for f in run_all(root)]
    problems += config_problems(root)
    if run_tests and not problems:
        proc = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *OFFLINE_SUITES], cwd=root, env={k: v for k, v in os.environ.items() if k != "FINPLAN_TARGET_ENV"})
        if int(getattr(proc, "returncode", 1)) != 0:
            problems.append("offline suites failed")
    for p in problems:
        log(f"FAIL pre-gate: {p}")
    return problems


def _post_gates(assembly: Path, log: Callable[[str], None]) -> list[str]:
    from scripts.infra_gates import GateContext, run_gates

    ctx = GateContext(assembly)
    results = run_gates(ctx)
    problems = [f"[{g}] {p}" for g, ps in results.items() for p in ps]
    for g, notes in ctx.notes.items():
        for note in notes:
            log(f"[NOTE] {g}: {note}")
    for p in problems:
        log(f"FAIL post-gate: {p}")
    return problems


def _served_majors(root: Path) -> list[int]:
    return sorted({int(m) for env in ("beta", "gamma", "prod") for m in json.loads((root / "config" / f"{env}.json").read_text(encoding="utf-8")).get("served_contract_majors", [1])})


def _default_synth(out: Path) -> Path:
    from scripts.synth import synth

    return synth(out)


def run_build(
    root: Path,
    out: Path,
    *,
    source_commit: str,
    region: str,
    account: str | None = None,
    s3: Any | None = None,
    store: str | None = None,
    image_fn: Callable[[str, str], Any] | None = None,
    synth_fn: Callable[[Path], Path] = _default_synth,
    run_tests: bool = True,
    variables: Path | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> ReleaseInfo:
    import re

    if not re.fullmatch(r"[0-9a-f]{40}", source_commit or ""):
        raise BuildFailed("source commit must be the 40-character commit ID from the Source stage")
    if out.exists():
        raise BuildFailed(f"{out} already exists; the build output is produced once per build")
    work = root / ".build" / "stage"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    release_id = mint_release_id(now)
    try:
        if pre_gates(root, run_tests=run_tests, log=log):
            raise BuildFailed("pre-synth gates failed; no artifact produced")
        assembly = synth_fn(work / "cdk.out")
        if _post_gates(assembly, log):
            raise BuildFailed("post-synth gates failed; no artifact produced")
        image = image_fn(release_id, source_commit) if image_fn is not None else None
        digest = getattr(image, "digest", None)
        if image_fn is not None and not digest:
            raise BuildFailed("the Runtime image has no digest; no artifact produced")
        for rel in PACKAGE_PATHS:
            src = root / rel
            if src.is_dir():
                shutil.copytree(src, work / rel, ignore=_IGNORE)
            elif src.is_file():
                shutil.copy2(src, work / rel)
        version, cdigest = contract_pin(root)
        uri = image_uri(str(account), region, str(image.repository), digest) if (image is not None and account) else None
        info = ReleaseInfo(
            release_id=release_id,
            source_commit=source_commit,
            artifact_digest=assembly_digest(assembly, digest),
            contract_version=version,
            contract_digest=cdigest,
            served_contract_majors=_served_majors(root),
            region=region,
            built_at=(now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            image_repository=getattr(image, "repository", None),
            image_digest=digest,
            image_uri=uri,
            base_images=dict(getattr(image, "base_images", {}) or {}),
        )
        (work / "release-info.json").write_text(info.to_json(), encoding="utf-8")
        if s3 is not None:
            if not store:
                raise BuildFailed("the release ledger needs the pipeline store name")
            store_build_output(s3, store, info, work)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(out))
        if variables is not None:
            write_variables(variables, {"RELEASE_ID": info.release_id, "IMAGE_URI": info.image_uri or ""})
        log(f"release {info.release_id} digest {info.artifact_digest} image {info.image_digest}")
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_rollback(release_id: str, out: Path, *, s3: Any, store: str, variables: Path | None = None, log: Callable[[str], None] = print) -> ReleaseInfo:
    if out.exists():
        raise BuildFailed(f"{out} already exists")
    info = fetch_build_output(s3, store, release_id, out)
    if not info.image_uri:
        raise BuildFailed(f"release {release_id} recorded no digest-pinned Runtime image")
    if variables is not None:
        write_variables(variables, {"RELEASE_ID": info.release_id, "IMAGE_URI": info.image_uri})
    log(f"rollback: re-emitting stored release {release_id} (digest {info.artifact_digest}); nothing rebuilt")
    return info


def _account_from_build_arn(arn: str | None) -> str:
    parts = (arn or "").split(":")
    if len(parts) < 5 or not parts[4].isdigit():
        raise BuildFailed("CODEBUILD_BUILD_ARN does not carry the account")
    return parts[4]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CodeBuild entry point (needs AWS)
    ap = argparse.ArgumentParser(description="FinanceAgent pipeline Build stage.")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--source-commit", default="")
    ap.add_argument("--rollback-to", default="none")
    ap.add_argument("--store", default=os.environ.get("FINPLAN_PIPELINE_STORE"))
    ap.add_argument("--image-repository", default=os.environ.get("FINPLAN_IMAGE_REPOSITORY"))
    ap.add_argument("--variables", type=Path, default=None)
    ap.add_argument("--no-publish", action="store_true", help="local run: no image build/push, no release ledger")
    args = ap.parse_args(argv)
    region = os.environ.get("AWS_REGION") or json.loads((ROOT / "config" / "shared.json").read_text())["region"]
    s3 = account = image_fn = None
    if not args.no_publish:
        import boto3

        from finplan_agent.core.aws_clients import client, s3_client
        from scripts.runtime_image import build_and_push

        del boto3
        s3 = s3_client(region)
        account = _account_from_build_arn(os.environ.get("CODEBUILD_BUILD_ARN"))
        ecr = client("ecr", region)
        repo = str(args.image_repository)

        def image_fn(release_id: str, commit: str) -> Any:
            return build_and_push(release_id=release_id, source_commit=commit, account=str(account), region=region, repository=repo, ecr=ecr)

    try:
        if args.rollback_to and args.rollback_to not in ("none", ""):
            if s3 is None or not args.store:
                raise BuildFailed("rollback needs the pipeline store")
            run_rollback(args.rollback_to, args.out, s3=s3, store=args.store, variables=args.variables)
        else:
            run_build(ROOT, args.out, source_commit=args.source_commit, region=region, account=account, s3=s3, store=args.store, image_fn=image_fn, variables=args.variables)
    except BuildFailed as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
