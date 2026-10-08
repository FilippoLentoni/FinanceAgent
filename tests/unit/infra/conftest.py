"""One offline synthesis of the full app per test session (all environments, tooling, pipeline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def assembly(tmp_path_factory) -> Path:
    from scripts.synth import synth

    return synth(tmp_path_factory.mktemp("cdk") / "cdk.out")


def _template(assembly: Path, prefix: str) -> dict:
    hits = sorted(p for p in assembly.rglob("*.template.json") if p.name.startswith(prefix))
    assert hits, prefix
    return json.loads(hits[0].read_text())


@pytest.fixture(scope="session")
def templates(assembly):
    out = {"tooling": _template(assembly, "Tooling"), "store": _template(assembly, "PipelineStore")}
    for env in ("beta", "gamma", "prod"):
        out[f"identity:{env}"] = _template(assembly, f"{env.capitalize()}Identity")
        out[f"agent:{env}"] = _template(assembly, f"{env.capitalize()}Agent")
    return out


def resources(t: dict, rtype: str) -> dict:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == rtype}
