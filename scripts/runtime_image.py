#!/usr/bin/env python3
"""Build the FinanceAgent AgentCore Runtime image ONCE, verify it, push it and record its digest
(design D1 container image; contracts D6 immutable promotion; lesson L3).

1. Base images named by the Dockerfile's ``ARG PYTHON_IMAGE=`` / ``ARG UV_IMAGE=`` defaults are pulled
   for ``linux/arm64`` and pinned by digest; the pinned references are passed as build args.
2. ``docker build --platform linux/arm64`` (the AgentCore Runtime requires ARM64) with
   ``container/Dockerfile`` from the repository root (its ``.dockerignore`` allow-list keeps tests,
   fixtures and secrets out of the image). The build stage runs on an arm64 build host.
3. **Import check (lesson L3)**: the built image must import the Runtime entry point and every
   runtime dependency (:data:`IMAGE_IMPORTS`) with ITS OWN interpreter, network disabled, and
   ``create_app`` must be importable. A failing check pushes nothing.
4. The image is tagged with the ``release_id`` and pushed; the digest read back from ECR is what every
   environment's Runtime references (``<repository>@sha256:...``), so beta, gamma and prod run the same
   bytes.

``--local-check`` runs the same import check WITHOUT docker: it installs the locked runtime
dependencies only (``uv sync --frozen --no-dev``) into a fresh environment and imports
:data:`IMAGE_IMPORTS` there, which proves the dependency closure of the artifact on the build host.

Every command goes through an injected runner and the ECR client is injected, so the unit suite runs
the sequence with fakes (no docker, no AWS).
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = Path("container/Dockerfile")
PLATFORM = "linux/arm64"
BASE_ARGS = ("PYTHON_IMAGE", "UV_IMAGE")
#: Modules the Runtime image must import with only its own environment.
IMAGE_IMPORTS = (
    "finplan_agent.runtime.app",
    "finplan_agent.runtime.service",
    "finplan_agent.providers.bedrock",
    "finplan_agent.session.store",
    "finplan_contracts",
    "finplan_contracts.budget",
    "langgraph.graph",
    "langgraph.checkpoint.memory",
    "langgraph_checkpoint_aws",
    "bedrock_agentcore.runtime",
    "boto3",
    "uvicorn",
    "starlette",
    "jsonschema",
)
IMAGE_IMPORT_CHECK = "import importlib, sys; [importlib.import_module(m) for m in sys.argv[1:]]; from finplan_agent.runtime.app import create_app; print('image imports ok')"
_ARG_RE = re.compile(r"^ARG\s+(?P<name>[A-Z_]+)=(?P<value>\S+)\s*$", re.MULTILINE)
_DIGEST_REF_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}\Z")

Runner = Callable[..., str]

__all__ = ["IMAGE_IMPORTS", "IMAGE_IMPORT_CHECK", "PLATFORM", "BuiltImage", "ImageError", "base_image_defaults", "build_and_push", "local_import_check"]


class ImageError(RuntimeError):
    pass


@dataclass
class BuiltImage:
    repository: str
    tag: str
    digest: str
    base_images: dict[str, str] = field(default_factory=dict)


def _run(cmd: list[str], *, cwd: Path, input: str | None = None, env: dict[str, str] | None = None) -> str:  # noqa: A002
    full_env = {**os.environ, "DOCKER_BUILDKIT": "1", **(env or {})}
    proc = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, text=True, check=False, env=full_env)
    if proc.returncode != 0:
        raise ImageError(f"{' '.join(cmd[:3])} ... failed ({proc.returncode}): {(proc.stdout + proc.stderr)[-2000:]}")
    return proc.stdout


def base_image_defaults(root: Path = ROOT) -> dict[str, str]:
    text = (root / DOCKERFILE).read_text(encoding="utf-8")
    found = {m["name"]: m["value"] for m in _ARG_RE.finditer(text)}
    missing = [a for a in BASE_ARGS if a not in found]
    if missing:
        raise ImageError(f"{DOCKERFILE} has no default for {missing}")
    return {a: found[a] for a in BASE_ARGS}


def pin_base_images(root: Path, run: Runner) -> dict[str, str]:
    pinned: dict[str, str] = {}
    for arg, ref in base_image_defaults(root).items():
        if _DIGEST_REF_RE.match(ref):
            pinned[arg] = ref
            continue
        run(["docker", "pull", "--platform", PLATFORM, ref], cwd=root)
        digests = run(["docker", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", ref], cwd=root).split()
        name = ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref
        match = next((d for d in digests if d.startswith(name + "@sha256:")), digests[0] if digests else "")
        if not _DIGEST_REF_RE.match(match):
            raise ImageError(f"could not pin {ref} by digest")
        pinned[arg] = match
    return pinned


def build_and_push(*, release_id: str, source_commit: str, account: str, region: str, repository: str, ecr: Any, root: Path = ROOT, run: Runner = _run) -> BuiltImage:
    if not re.fullmatch(r"rel_[0-7][0-9A-HJKMNP-TV-Z]{25}", release_id):
        raise ImageError("the image tag must be the release_id")
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    uri = f"{registry}/{repository}"
    base = pin_base_images(root, run)
    cmd = ["docker", "build", "--platform", PLATFORM, "-f", str(DOCKERFILE), "-t", f"{uri}:{release_id}", "--label", f"org.opencontainers.image.revision={source_commit}", "--label", f"finplan.release-id={release_id}"]
    for arg, ref in sorted(base.items()):
        cmd += ["--build-arg", f"{arg}={ref}"]
    cmd.append(".")
    run(cmd, cwd=root)
    try:
        run(["docker", "run", "--rm", "--network", "none", "--platform", PLATFORM, "--entrypoint", "python", f"{uri}:{release_id}", "-c", IMAGE_IMPORT_CHECK, *IMAGE_IMPORTS], cwd=root)
    except ImageError as exc:
        raise ImageError(f"the built image does not import the agent and its dependencies (nothing pushed): {exc}") from None
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    run(["docker", "login", "--username", user, "--password-stdin", registry], cwd=root, input=password)
    run(["docker", "push", f"{uri}:{release_id}"], cwd=root)
    details = ecr.describe_images(repositoryName=repository, imageIds=[{"imageTag": release_id}])["imageDetails"]
    if not details or not str(details[0].get("imageDigest", "")).startswith("sha256:"):
        raise ImageError("the pushed image has no digest in ECR")
    return BuiltImage(repository=repository, tag=release_id, digest=str(details[0]["imageDigest"]), base_images=base)


def local_import_check(root: Path = ROOT, run: Runner = _run) -> str:
    """Install ONLY the locked runtime dependencies into a fresh environment and import the image
    module list there (dependency-closure proof without docker)."""
    with tempfile.TemporaryDirectory(prefix="fa-runtime-env-") as tmp:
        env_dir = Path(tmp) / "venv"
        run(["uv", "sync", "--frozen", "--no-dev", "--no-editable"], cwd=root, env={"UV_PROJECT_ENVIRONMENT": str(env_dir)})
        py = env_dir / "bin" / "python"
        return run([str(py), "-I", "-c", IMAGE_IMPORT_CHECK, *IMAGE_IMPORTS], cwd=Path(tmp))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--local-check", action="store_true", help="dependency-closure import check without docker")
    args = ap.parse_args(argv)
    if args.local_check:
        try:
            print(local_import_check().strip())
        except ImageError as exc:
            print(f"FAIL: {exc}")
            return 1
        return 0
    print(base_image_defaults())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
