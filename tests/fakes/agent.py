"""Test doubles for the offline suites: a fake Gateway tool client, unsigned test JWTs, a stubbed
Bedrock Runtime client and a fully wired in-process :class:`AgentService`."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from finplan_agent.config.provider import ProviderConfig, build_provider_config
from finplan_agent.config.settings import Settings, load_repo_config
from finplan_agent.providers import FixtureProvider
from finplan_agent.providers.base import ToolSpec
from finplan_agent.runtime.service import AgentService, Deps
from finplan_agent.session.store import InMemorySessionOwnership
from finplan_agent.tools.catalog import ToolCatalog
from finplan_agent.tools.mcp_client import ToolOutcome

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "config" / "provider-configs.json"
RELEASE_ID = "rel_01JABCDEFGHJKMNPQRSTVWXYZ0"
PV = "pv_01JABCDEFGHJKMNPQRSTVWXYZ1"
PL = "pl_01JABCDEFGHJKMNPQRSTVWXYZ2"
RUN = "run_01JABCDEFGHJKMNPQRSTVWXYZ3"
CHECKSUM = "sha256:" + "ab" * 32

#: (name, state_changing, role_class) in the FinanceLambdasTool D1 catalog.
TOOLS = [
    ("describe_capabilities", False, "reader"),
    ("query_market_data", False, "reader"),
    ("get_plan", False, "reader"),
    ("get_plan_version", False, "reader"),
    ("list_plan_versions", False, "reader"),
    ("get_job_status", False, "reader"),
    ("get_experiment_result", False, "reader"),
    ("refresh_market_data", True, "submitter"),
    ("submit_experiment", True, "submitter"),
    ("create_override_version", True, "plan-writer"),
    ("validate_plan_version", True, "plan-writer"),
    ("publish_plan_version", True, "plan-writer"),
]


def fixture_config() -> dict[str, Any]:
    return json.loads(FIXTURES.read_text())


def catalog_document(environment: str = "beta") -> dict[str, Any]:
    from finplan_contracts.schemas import schema_id

    def sid(tool: str, kind: str) -> str:
        ns = "finance" if tool in ("query_market_data", "refresh_market_data") else "core"
        return schema_id(ns, f"tools/{tool.replace('_', '-')}-{kind}")

    return {
        "environment": environment,
        "release_id": RELEASE_ID,
        "contract_version": "1.0.0",
        "tools": [
            {
                "name": n,
                "description": f"{n} tool",
                "input_schema_id": sid(n, "request"),
                "output_schema_id": sid(n, "response"),
                "state_changing": sc,
                "role_class": rc,
                "lambda_ref_parameter": f"/finplan/{environment}/financelambdastool/lambda/{n.replace('_', '-')}-arn",
            }
            for n, sc, rc in TOOLS
        ],
    }


def b64(doc: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(doc).encode()).decode().rstrip("=")


def fake_jwt(sub: str = "user-a", *, iss: str = "https://issuer.invalid/pool-beta", groups: tuple[str, ...] = ("researcher",), extra: dict[str, Any] | None = None) -> str:
    """An UNSIGNED test token (the Runtime validates real tokens before the agent sees them)."""
    claims = {"sub": sub, "iss": iss, "cognito:groups": list(groups), "token_use": "access", **(extra or {})}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def auth(sub: str = "user-a", **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {fake_jwt(sub, **kw)}"}


def default_results() -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "describe_capabilities": lambda a: {"contract_version": "1.0.0", "environment": "beta", "tool_count": 12, "phase": 1},
        "get_plan_version": lambda a: {
            "plan_version_id": a["plan_version_id"],
            "plan_id": PL,
            "checksum": CHECKSUM,
            "status": "published",
            "allocation": {"weights": [{"instrument": "SPY", "weight": 0.6}, {"instrument": "AGG", "weight": 0.4}]},
            "expected_return": 0.0612,
        },
        "get_plan": lambda a: {"plan_id": a["plan_id"], "head_plan_version_id": PV, "revision": 3},
        "list_plan_versions": lambda a: {"plan_id": a["plan_id"], "plan_versions": [{"plan_version_id": PV}], "truncated": False},
        "get_job_status": lambda a: {"run_id": a["run_id"], "state": "running", "purpose": "backtest"},
        "get_experiment_result": lambda a: {"run_id": a["run_id"], "state": "succeeded", "sharpe": 0.84},
        "publish_plan_version": lambda a: {"publication_id": "pub_01JABCDEFGHJKMNPQRSTVWXYZ4", "plan_version_id": a["plan_version_id"]},
    }


class FakeToolClient:
    """Stands in for the Gateway MCP client: records calls, answers from ``results``."""

    def __init__(self, results: dict[str, Callable[[dict[str, Any]], Any]] | None = None, *, errors: dict[str, dict[str, Any]] | None = None, extra_tools: list[str] | None = None) -> None:
        self.results = results or default_results()
        self.errors = errors or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.extra_tools = extra_tools or []

    def list_tools(self) -> list[ToolSpec]:
        names = [n for n, _, _ in TOOLS] + self.extra_tools
        return [ToolSpec(name=n, description=f"{n} tool", input_schema={"type": "object"}) for n in names]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, json.loads(json.dumps(arguments))))
        if name in self.errors:
            return ToolOutcome(tool=name, ok=False, error=self.errors[name])
        fn = self.results.get(name)
        if fn is None:
            return ToolOutcome(tool=name, ok=False, error={"code": "DEPENDENCY_UNAVAILABLE", "message": "no fixture", "retryable": True, "details": {}})
        return ToolOutcome(tool=name, ok=True, result=fn(arguments))


class StubBedrockClient:
    """A stubbed ``bedrock-runtime`` client (no botocore, no network). Scripted responses."""

    def __init__(self, responses: list[dict[str, Any]] | None = None, *, error: Exception | None = None, stream_events: list[list[dict[str, Any]]] | None = None) -> None:
        self.responses = list(responses or [])
        self.stream_events = list(stream_events or [])
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kw: Any) -> dict[str, Any]:
        self.requests.append(kw)
        if self.error is not None:
            err, self.error = self.error, None
            raise err
        return self.responses.pop(0)

    def converse_stream(self, **kw: Any) -> dict[str, Any]:
        self.requests.append(kw)
        if self.error is not None:
            err, self.error = self.error, None
            raise err
        return {"stream": iter(self.stream_events.pop(0))}


class ClientError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": message}}


def text_response(text: str, *, inp: int = 100, out: int = 20, cache_read: int = 0, cache_write: int = 0) -> dict[str, Any]:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn", "usage": {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": cache_read, "cacheWriteInputTokens": cache_write}}


def tool_use_response(name: str, args: dict[str, Any], *, tid: str = "tu-1") -> dict[str, Any]:
    return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": tid, "name": name, "input": args}}]}}, "stopReason": "tool_use", "usage": {"inputTokens": 120, "outputTokens": 30}}


def bedrock_config(environment: str = "gamma", **overrides: Any) -> ProviderConfig:
    fx = fixture_config()
    guards = {**fx["guards"]["bedrock_valid"], **overrides}
    return build_provider_config(environment, "bedrock", fx["model_ids"]["prod_profile"], guards)


def fixture_provider_config(environment: str = "beta", **overrides: Any) -> ProviderConfig:
    repo = load_repo_config(environment)
    return build_provider_config(environment, "fixture", None, overrides, guard_defaults=repo["guard_defaults"])


def make_service(
    *,
    environment: str = "beta",
    tools: FakeToolClient | None = None,
    provider: Any = None,
    provider_config: ProviderConfig | None = None,
    checkpointer: Any = None,
    ownership: Any = None,
    spend: Any = None,
    budget_reader: Any = None,
    usage: list[dict[str, Any]] | None = None,
) -> tuple[AgentService, FakeToolClient]:
    from langgraph.checkpoint.memory import InMemorySaver

    tools = tools or FakeToolClient()
    cfg = provider_config or fixture_provider_config(environment)
    settings = Settings(environment=environment, region="us-east-2", release_id=RELEASE_ID, memory_id=None, gateway_url=None, repo_config=load_repo_config(environment))
    deps = Deps(
        settings=settings,
        provider_config=cfg,
        provider=provider or FixtureProvider(),
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        ownership=ownership if ownership is not None else InMemorySessionOwnership(),
        catalog_loader=lambda: ToolCatalog.from_document(catalog_document(environment), environment=environment),
        tool_client_factory=lambda caller: tools,
        budget_reader=budget_reader,
        usage_sink=(usage.append if usage is not None else None),
        skills=({"name": "capability-overview", "version": "0.0.0-fixture"},),
    )
    if spend is not None:
        deps.spend = spend
    return AgentService(deps), tools


SESSION = "s" + "0123456789abcdef" * 3  # 49 characters
SESSION_B = "t" + "0123456789abcdef" * 3


def run(service: AgentService, payload: dict[str, Any], *, headers: dict[str, str] | None = None, session: str = SESSION) -> Any:
    out = service.handle(payload, headers if headers is not None else auth(), session)
    return list(out) if not isinstance(out, dict) else out
