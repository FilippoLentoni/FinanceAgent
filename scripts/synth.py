#!/usr/bin/env python3
"""Offline synthesis of the FinanceAgent cloud assembly (the build stage runs it ONCE; contracts D6).

Same app as ``infra/app.py`` with the deployment synthesizer: environment stacks carry no CDK
bootstrap-version rule and no ``cdk-hnb659fds`` reference (lesson L1), so the pipeline's CloudFormation
actions deploy them without a ``CDKToolkit`` stack. The Runtime artifact is the ARM64 image built and
pushed BY DIGEST by the build stage and passed to the agent stack as the ``ImageUri`` parameter (whose
pattern refuses a tag), so a release can never deploy a source-only or tag-addressed artifact (L3).

Usage: ``uv run python scripts/synth.py --out cdk.out [--envs beta,gamma]``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import aws_cdk as cdk  # noqa: E402

from infra.app import build_app  # noqa: E402
from infra.stacks.tooling import deployment_synthesizer  # noqa: E402

__all__ = ["synth"]


def synth(outdir: str | os.PathLike[str] | None = None, envs: list[str] | None = None) -> Path:
    out = Path(outdir or os.environ.get("CDK_OUTDIR") or ROOT / "cdk.out")
    app = cdk.App(default_stack_synthesizer=deployment_synthesizer(), outdir=str(out), context={"cli-telemetry": False})
    build_app(app, envs)
    directory = Path(app.synth().directory)
    _compact_templates(directory)
    return directory


def _compact_templates(directory: Path) -> None:
    # CodePipeline deploys these exact files through S3, whose template limit
    # is 1 MB. CDK indentation alone can exceed that with large inline schemas
    # and conditional dependencies. Preserve every value, including Cedar text.
    for path in directory.rglob("*.template.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthesize the FinanceAgent cloud assembly (offline).")
    ap.add_argument("--out", help="assembly directory (default: $CDK_OUTDIR or ./cdk.out)")
    ap.add_argument("--envs", help="comma-separated environments (default: all; the pipeline needs all)")
    args = ap.parse_args(argv)
    envs = [e for e in args.envs.split(",") if e] if args.envs else None
    print(synth(args.out, envs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
