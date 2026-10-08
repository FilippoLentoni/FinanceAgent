#!/usr/bin/env python3
"""FinanceAgent CDK app: one ``Stage`` per environment (beta, gamma, prod) plus the account-level stacks.

Synthesis is offline: stacks are account-agnostic (``AWS::AccountId`` is a deploy-time pseudo
parameter), configuration comes from ``config/<env>.json`` and ``config/shared.json``, and no context
lookup is used. ``npx aws-cdk@2 synth`` (``cdk.json``) or ``uv run python scripts/synth.py``.

Per environment (deploy order, pipeline stage ``<Env>``):

1. ``finplan-<env>-financeagent-identity`` - the Cognito user pool, clients, ci_test secret;
2. ``finplan-<env>-financeagent-agent`` - Memory, policy engine, Gateway, targets, policies,
   Runtime role, Runtime, Runtime log group.

Account level (deployed only by ``scripts/bootstrap.py``): ``finplan-shared-financeagent-pipeline-store``
and ``finplan-shared-financeagent-tooling`` (image repository, per-environment Gateway service roles,
pipeline).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import aws_cdk as cdk  # noqa: E402

from infra.stacks import naming as n  # noqa: E402
from infra.stacks.agent import AgentStack  # noqa: E402
from infra.stacks.common import StageContext  # noqa: E402
from infra.stacks.identity import IdentityStack  # noqa: E402

__all__ = ["build_app", "load_config", "load_shared"]


def load_shared(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / "config" / "shared.json").read_text(encoding="utf-8"))


def load_config(env: str, root: Path = ROOT) -> dict[str, Any]:
    if env not in n.ENVIRONMENTS:
        raise ValueError(f"unknown environment {env!r}")
    return {**load_shared(root), **json.loads((root / "config" / f"{env}.json").read_text(encoding="utf-8"))}


def build_app(app: cdk.App | None = None, envs: Iterable[str] | None = None, *, pipeline: bool = True) -> cdk.App:
    app = app or cdk.App()
    selected = list(envs) if envs is not None else _selected(app)
    shared = load_shared()
    stages: dict[str, StageContext] = {}
    for env in selected:
        cfg = load_config(env)
        stage = cdk.Stage(app, env.capitalize())
        ctx = StageContext(env, cfg, stage)
        identity = IdentityStack(stage, "Identity", env_name=env, cfg=cfg)
        agent = AgentStack(stage, "Agent", env_name=env, cfg=cfg, identity=identity)
        ctx.stacks.update(identity=identity, agent=agent)
        stages[env] = ctx
    from infra.stacks import pipeline as pipeline_mod
    from infra.stacks import tooling

    tooling.add_to_app(app, shared, stages)
    if pipeline:
        pipeline_mod.add_to_app(app, shared, stages)
    return app


def _selected(app: cdk.App) -> list[str]:
    raw = app.node.try_get_context("envs")
    if not raw:
        return list(n.ENVIRONMENTS)
    envs = [e.strip() for e in str(raw).split(",") if e.strip()]
    unknown = [e for e in envs if e not in n.ENVIRONMENTS]
    if unknown:
        raise SystemExit(f"unknown environments in -c envs: {unknown}")
    return envs


if __name__ == "__main__":
    from infra.stacks.tooling import deployment_synthesizer

    build_app(cdk.App(default_stack_synthesizer=deployment_synthesizer())).synth()
