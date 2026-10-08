#!/usr/bin/env python3
"""FinanceAgent agent-code gates for the build stage (tasks 2.1, 2.3, 3.12; FA-RT-01, FA-RT-03,
FA-PRV-11, FA-PRV-14). Offline; exit 1 with named findings on failure.

1. **Architecture check** (FA-RT-01): the agent package may host only the LangGraph graph. It fails on
   imports of other agent frameworks or a managed agent harness, and on a Runtime entry point that
   is not ``finplan_agent.runtime.app`` wrapping ``finplan_agent.graph.build``.
2. **Model-identifier scan** (FA-PRV-11): no literal Bedrock model or inference-profile identifier in
   agent code, skills, scripts or tests outside ``tests/fixtures/config/``; the ID lives only in
   ``/finplan/<env>/financeagent/config/explanation-model-id``.
3. **Provider-kind check** (FA-PRV-14): beta allows only the ``fixture`` provider.
4. **WebSocket gate** (FA-RT-03): an environment may enable WebSocket only with a recorded
   ``requirement_ref`` naming a design decision, and the agent may register a WebSocket handler only
   through the same service (same graph, policy and session store) as HTTP.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

__all__ = ["Finding", "architecture_findings", "model_id_findings", "provider_kind_findings", "websocket_findings", "run_all", "main"]

#: Agent frameworks / managed harnesses that must never be imported (design D1).
DISALLOWED_IMPORTS = (
    "strands",
    "crewai",
    "autogen",
    "autogen_agentchat",
    "llama_index",
    "agents",  # OpenAI Agents SDK
    "openai",
    "semantic_kernel",
    "smolagents",
    "pydantic_ai",
    "haystack",
    "bedrock_agentcore.harness",
    "langchain.agents",
)
_MODEL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_/-])(?:(?:us|eu|apac|ap|global|jp|au|ca|us-gov)\.)?"
    r"(?:anthropic|amazon|meta|mistral|cohere|ai21|qwen|deepseek|openai|writer|stability|twelvelabs|luma|minimax|moonshot|google|nvidia)"
    r"\.[a-z0-9][a-z0-9-]*(?:[.:][a-z0-9-]+)*(?![A-Za-z0-9_(])"
)
_SCAN_DIRS = ("agent", "skills", "scripts", "tests", "cli", "infra", "policy")
_EXEMPT = ("tests/fixtures/config/",)
_TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml"}
#: Python modules whose names collide with the regex but are not model IDs.
_NOT_MODEL = {"amazon.com", "amazonaws.com"}


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.path}: {self.message}"


def _py_files(root: Path, sub: str) -> list[Path]:
    base = root / sub
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts) if base.is_dir() else []


def architecture_findings(root: Path = ROOT) -> list[Finding]:
    out: list[Finding] = []
    for path in _py_files(root, "agent"):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            hit = next((n for n in names for bad in DISALLOWED_IMPORTS if n == bad or n.startswith(bad + ".")), None)
            if hit is not None:
                out.append(Finding("architecture", rel, f"disallowed agent framework or harness import {hit!r} (LangGraph only, design D1)"))
    app = root / "agent" / "finplan_agent" / "runtime" / "app.py"
    if not app.is_file():
        out.append(Finding("architecture", "agent/finplan_agent/runtime/app.py", "the Runtime entry point is missing"))
    else:
        text = app.read_text(encoding="utf-8")
        if "@app.entrypoint" not in text or "AgentService" not in text:
            out.append(Finding("architecture", "agent/finplan_agent/runtime/app.py", "the Runtime entry point must hand every invocation to AgentService (the LangGraph graph)"))
    entrypoints = []
    for path in _py_files(root, "agent"):
        if "@app.entrypoint" in path.read_text(encoding="utf-8") or "BedrockAgentCoreApp(" in path.read_text(encoding="utf-8"):
            entrypoints.append(path.relative_to(root).as_posix())
    extra = [e for e in entrypoints if e != "agent/finplan_agent/runtime/app.py"]
    for e in extra:
        out.append(Finding("architecture", e, "a second Runtime entry point is not allowed"))
    svc = root / "agent" / "finplan_agent" / "runtime" / "service.py"
    if svc.is_file() and "build_graph" not in svc.read_text(encoding="utf-8"):
        out.append(Finding("architecture", "agent/finplan_agent/runtime/service.py", "the service does not build the LangGraph graph"))
    return out


def model_id_findings(root: Path = ROOT) -> list[Finding]:
    out: list[Finding] = []
    for sub in _SCAN_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(e) for e in _EXEMPT):
                continue
            for m in _MODEL_ID_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                token = m.group(0)
                # Real Bedrock IDs always contain a hyphen (claude-opus-5, nova-pro-v1:0, qwen3-32b-v1:0);
                # Python attribute chains such as ``meta.config`` never do.
                if token in _NOT_MODEL or "-" not in token:
                    continue
                out.append(Finding("model-id", rel, f"literal Bedrock model or profile identifier {token!r}; read it from the explanation-model-id parameter"))
    return out


def _env_configs(root: Path) -> dict[str, dict]:
    out = {}
    for env in ("beta", "gamma", "prod"):
        p = root / "config" / f"{env}.json"
        if p.is_file():
            out[env] = json.loads(p.read_text(encoding="utf-8"))
    return out


def provider_kind_findings(root: Path = ROOT) -> list[Finding]:
    out = []
    beta = _env_configs(root).get("beta", {})
    if beta.get("provider_kind_allowed") != ["fixture"]:
        out.append(Finding("provider-kind", "config/beta.json", "beta must allow only the fixture provider (no Bedrock calls in CI)"))
    return out


def websocket_findings(root: Path = ROOT) -> list[Finding]:
    out = []
    for env, cfg in _env_configs(root).items():
        ws = cfg.get("streaming", {}).get("websocket", {})
        if ws.get("enabled") and not (isinstance(ws.get("requirement_ref"), str) and re.match(r"^design\.md#D\d+", ws["requirement_ref"])):
            out.append(Finding("websocket", f"config/{env}.json", "WebSocket is enabled without a recorded requirement_ref (design.md#D<n>)"))
    for path in _py_files(root, "agent"):
        text = path.read_text(encoding="utf-8")
        if "@app.websocket" in text or ".websocket(" in text:
            if "AgentService" not in text or "websocket_enabled" not in text:
                out.append(Finding("websocket", path.relative_to(root).as_posix(), "a WebSocket handler must go through AgentService and Settings.websocket_enabled"))
    return out


def run_all(root: Path = ROOT) -> list[Finding]:
    return architecture_findings(root) + model_id_findings(root) + provider_kind_findings(root) + websocket_findings(root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    findings = run_all(args.root)
    for f in findings:
        print(f"FAIL: {f}")
    if findings:
        return 1
    print("PASS: agent gates (architecture, model-id scan, provider kind, websocket)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
