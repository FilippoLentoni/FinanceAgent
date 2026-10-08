"""The agent service behind the Runtime HTTP contract (tasks 2.2, 2.4-2.6, 4.1-4.2).

One :class:`AgentService` per process. :meth:`AgentService.handle` takes the ``/invocations`` JSON
payload, the forwarded request headers and the Runtime session ID, and returns either a JSON document
(non-streaming) or an iterator of events (streaming; the Runtime app renders it as SSE).

Request payload (documented in ``docs/agent-api.md``)::

    {"action": "invoke" | "confirm" | "describe" | "delete_session"   (default "invoke"),
     "prompt": "...",                              (invoke)
     "tool_request": {"name": ..., "arguments": {...}},   (invoke, optional structured request)
     "approve": true | false,                      (confirm)
     "stream": true | false,                       (default true)
     "correlation_id": "...",                      (optional; minted when absent)
     "session_id": "..."}                          (only when no Runtime session header is present)

Stream events, in order: ``progress`` (``accepted``, then per node), ``tool_call``,
``tool_result_summary``, ``token``, and exactly one terminal ``final`` carrying ``session_id`` and
``correlation_id``. The ``final`` payload is built from the checkpointed graph state, so a streamed and
a non-streamed run of the same prompt produce identical ``final`` payloads apart from IDs.

Order of checks for every request: caller authenticated (bearer token forwarded by the Runtime) ->
session ID valid -> session owned by the caller (``FORBIDDEN`` otherwise, before any checkpoint read)
-> graph.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from .. import FRAMEWORK, GRAPH_VERSION
from ..budget.guard import BudgetGuard, SpendSource, SsmBudgetReader, StaticSpendSource
from ..config.provider import ProviderConfig, load_provider_config
from ..config.settings import Settings
from ..core.errors import AgentError, contract_version
from ..core.ids import new_correlation_id, new_session_id, valid_correlation_id, validate_session_id
from ..explanations.config import ExplanationSettings, load_explanation_settings
from ..graph.build import build_graph
from ..graph.state import AgentContext
from ..providers import ExplanationProvider, make_provider
from ..session.identity import Caller, caller_from_headers
from ..session.store import SessionOwnership, delete_session, open_session
from ..tools.catalog import ToolCatalog, load_tool_catalog
from ..tools.mcp_client import GatewayMcpClient, ToolClient

__all__ = ["ACTIONS", "AgentService", "Deps"]

log = logging.getLogger("finplan_agent.service")
ACTIONS = ("invoke", "confirm", "describe", "delete_session")
MAX_PROMPT_CHARS = 20000


@dataclass
class Deps:
    """Everything the service needs, injectable for tests."""

    settings: Settings
    provider_config: ProviderConfig
    provider: ExplanationProvider
    checkpointer: Any
    ownership: SessionOwnership
    catalog_loader: Callable[[], ToolCatalog]
    tool_client_factory: Callable[[Caller], ToolClient]
    spend: SpendSource = field(default_factory=StaticSpendSource)
    budget_reader: Any = None
    usage_sink: Callable[[dict[str, Any]], None] | None = None
    stable_instructions: tuple[str, ...] = ()
    skills: tuple[dict[str, Any], ...] = ()
    #: Explanation settings; default: the repository ``explanations`` block of the environment.
    explanation_settings: ExplanationSettings | None = None


class AgentService:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.graph = build_graph(deps.checkpointer)
        self.guard = BudgetGuard(deps.provider_config, spend=deps.spend, budget_reader=deps.budget_reader)
        if deps.explanation_settings is None:
            deps.explanation_settings = load_explanation_settings(deps.settings.repo_config)
        self._catalog: tuple[float, ToolCatalog] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_environment(cls) -> AgentService:
        """Production wiring. Invalid configuration raises, so the process exits and /ping never
        reports healthy (spec: unknown provider kind or Qwen/global. model fails the health check)."""
        from ..budget.metrics import CloudWatchSpendSource, emf_record, put_usage
        from ..core.aws_clients import client, s3_client
        from ..session.store import MemorySessionOwnership, make_checkpointer

        settings = Settings.from_env()
        ssm = client("ssm")
        s3 = s3_client(settings.region)
        provider_config = load_provider_config(settings.environment, ssm)
        provider = make_provider(provider_config)
        if not settings.memory_id or not settings.gateway_url:
            raise AgentError.validation("FINPLAN_MEMORY_ID and FINPLAN_GATEWAY_URL must be set by the Runtime stack", pointer="/environment")
        cw = client("cloudwatch")
        spend = CloudWatchSpendSource(cw)
        gw = settings.gateway

        def tool_client(caller: Caller) -> ToolClient:
            return GatewayMcpClient(
                settings.gateway_url or "",
                lambda: caller.bearer,
                protocol_version=str(gw.get("mcp_protocol_version", "2025-11-25")),
                timeout_seconds=float(gw.get("request_timeout_seconds", 60)),
                max_response_bytes=int(gw.get("max_response_bytes", 262144)),
            )

        def sink(usage: dict[str, Any]) -> None:
            put_usage(
                cw,
                emf_record(
                    environment=settings.environment, release_id=settings.release_id, provider_kind=usage.get("provider_kind") or provider.kind, model_id=usage.get("model_id"), usage=usage, tool_calls=int(usage.get("tool_calls", 0))
                ),
            )

        deps = Deps(
            settings=settings,
            provider_config=provider_config,
            provider=provider,
            checkpointer=make_checkpointer(settings.memory_id, settings.region),
            ownership=MemorySessionOwnership(client("bedrock-agentcore"), settings.memory_id),
            catalog_loader=lambda: load_tool_catalog(ssm, settings.environment, s3=s3),
            tool_client_factory=tool_client,
            spend=spend,
            budget_reader=SsmBudgetReader(ssm),
            usage_sink=sink,
            explanation_settings=load_explanation_settings(settings.repo_config, ssm, settings.ssm.explanation_limits),
        )
        return cls(deps)

    # ------------------------------------------------------------------ helpers
    def catalog(self) -> ToolCatalog:
        with self._lock:
            if self._catalog is None or time.monotonic() - self._catalog[0] > 300:
                self._catalog = (time.monotonic(), self.deps.catalog_loader())
            return self._catalog[1]

    def describe(self) -> dict[str, Any]:
        s = self.deps.settings
        return {
            "type": "describe",
            "framework": FRAMEWORK,
            "graph_version": GRAPH_VERSION,
            "environment": s.environment,
            "release_id": s.release_id,
            "contract_version": contract_version(),
            "provider": self.deps.provider_config.describe(),
            "skills": [dict(sk) for sk in self.deps.skills],
            "streaming": {"http": True, "websocket": s.websocket_enabled},
        }

    def _context(self, caller: Caller, session_id: str) -> AgentContext:
        spend = self.deps.spend

        def on_usage(usage: dict[str, Any]) -> None:
            if self.deps.usage_sink is None:
                return
            try:
                self.deps.usage_sink(usage)
            except Exception:  # noqa: BLE001 - metrics never fail a turn
                log.warning("usage metric emission failed")

        return AgentContext(
            environment=self.deps.settings.environment,
            release_id=self.deps.settings.release_id,
            provider=self.deps.provider,
            provider_config=self.deps.provider_config,
            guard=self.guard,
            tools=self.deps.tool_client_factory(caller),
            catalog=self.catalog(),
            session_id=session_id,
            stable_instructions=self.deps.stable_instructions,
            on_usage=on_usage,
            on_spend=getattr(spend, "record_local", None),
            explanations=self.deps.explanation_settings,
        )

    def _final(self, config: dict[str, Any], session_id: str, correlation_id: str) -> dict[str, Any]:
        snap = self.graph.get_state(config)
        st = dict(snap.values or {})
        status = st.get("status") or "failed"
        confirmation = None
        if snap.interrupts:
            status = "awaiting_confirmation"
            confirmation = snap.interrupts[0].value
        evidence = [{k: v for k, v in r.items() if k in ("id", "tool", "ok", "summary", "error", "declined")} for r in st.get("tool_results") or []]
        return {
            "type": "final",
            "session_id": session_id,
            "correlation_id": correlation_id,
            "status": status,
            "answer": {
                "narrative": st.get("narrative") or "",
                "narrative_status": "pending_confirmation" if confirmation else st.get("narrative_status", "not_needed"),
                "evidence": evidence,
                "claim_check": st.get("claim_check") or {},
                "explanation": st.get("explanation_result"),
            },
            "confirmation": confirmation,
            "in_progress": st.get("in_progress"),
            "declined": (st.get("declined") or [])[-1:] if (st.get("confirmation") or {}).get("decision") == "declined" else [],
            "error": st.get("error"),
            "degraded": st.get("degraded"),
            "usage": st.get("turn_usage") or {},
            "graph_version": GRAPH_VERSION,
            "release_id": self.deps.settings.release_id,
        }

    def _failed(self, exc: AgentError, session_id: str | None, correlation_id: str) -> dict[str, Any]:
        return {
            "type": "final",
            "session_id": session_id,
            "correlation_id": correlation_id,
            "status": "failed",
            "answer": None,
            "confirmation": None,
            "in_progress": None,
            "declined": [],
            "error": exc.to_envelope(correlation_id),
            "degraded": None,
            "usage": {},
            "graph_version": GRAPH_VERSION,
            "release_id": self.deps.settings.release_id,
        }

    # ------------------------------------------------------------------ entry
    def handle(self, payload: Any, headers: Mapping[str, str] | None, runtime_session_id: str | None) -> dict[str, Any] | Iterator[dict[str, Any]]:
        cid = payload.get("correlation_id") if isinstance(payload, dict) and valid_correlation_id(payload.get("correlation_id")) else new_correlation_id()
        stream = not (isinstance(payload, dict) and payload.get("stream") is False)
        session_id: str | None = None
        try:
            if not isinstance(payload, dict):
                raise AgentError.validation("the request body must be a JSON object", pointer="")
            action = payload.get("action", "invoke")
            if action not in ACTIONS:
                raise AgentError.validation(f"unknown action {action!r}", pointer="/action", allowed=list(ACTIONS))
            caller = caller_from_headers(headers, self.deps.settings.environment)
            if action == "describe":
                return self.describe()
            session_id = validate_session_id(runtime_session_id or payload.get("session_id") or new_session_id())
            config = {"configurable": {"thread_id": session_id, "actor_id": caller.actor_id}}
            if action == "delete_session":
                delete_session(self.deps.ownership, self.deps.checkpointer, session_id, caller)
                return {"type": "deleted", "session_id": session_id, "correlation_id": cid}
            open_session(self.deps.ownership, session_id, caller)  # FORBIDDEN before any checkpoint read
            graph_input = self._graph_input(action, payload, config, caller, cid)
            context = self._context(caller, session_id)
        except AgentError as exc:
            final = self._failed(exc, session_id, cid)
            return iter([final]) if stream else final
        if stream:
            return self._stream(graph_input, config, context, session_id, cid)
        try:
            for item in graph_input:
                self.graph.invoke(item, config, context=context)
        except AgentError as exc:
            return self._failed(exc, session_id, cid)
        except Exception:  # noqa: BLE001 - never a stack trace to the caller
            log.exception("graph run failed (correlation_id=%s)", cid)
            return self._failed(AgentError.internal(), session_id, cid)
        return self._final(config, session_id, cid)

    def _graph_input(self, action: str, payload: dict[str, Any], config: dict[str, Any], caller: Caller, cid: str) -> list[Any]:
        snap = self.graph.get_state(config)
        pending_interrupt = bool(snap.interrupts)
        if action == "confirm":
            if not pending_interrupt:
                raise AgentError("PRECONDITION_FAILED", "no confirmation is pending in this session")
            if not isinstance(payload.get("approve"), bool):
                raise AgentError.validation("confirm needs approve: true or false", pointer="/approve")
            return [Command(resume={"approve": payload["approve"]}, update={"correlation_id": cid})]
        if pending_interrupt:
            raise AgentError("PRECONDITION_FAILED", "a confirmation is pending in this session; answer it with action confirm first")
        prompt = payload.get("prompt")
        tool_request = payload.get("tool_request")
        explanation = payload.get("explanation")
        if explanation is not None and not isinstance(explanation, dict):
            raise AgentError.validation("explanation must be an object", pointer="/explanation")
        if explanation is not None and tool_request is not None:
            raise AgentError.validation("send either explanation or tool_request, not both", pointer="/explanation")
        if tool_request is not None and (not isinstance(tool_request, dict) or not isinstance(tool_request.get("name"), str) or not isinstance(tool_request.get("arguments", {}), dict)):
            raise AgentError.validation("tool_request must be {name: str, arguments: object}", pointer="/tool_request")
        if not isinstance(prompt, str) or not prompt.strip():
            if explanation is not None:
                prompt = f"Explain ({explanation.get('type')})."
            elif tool_request is None:
                raise AgentError.validation("prompt must be a non-empty string", pointer="/prompt")
            else:
                prompt = f"Run the tool {tool_request['name']}."
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AgentError.validation(f"prompt exceeds {MAX_PROMPT_CHARS} characters", pointer="/prompt")
        content: list[dict[str, Any]] = [{"text": prompt}]
        if tool_request is not None:
            content.append({"tool_request": {"name": tool_request["name"], "arguments": dict(tool_request.get("arguments") or {})}})
        items: list[Any] = []
        if snap.next:
            # The previous turn did not finish (process recycled mid-step): resume it from its last
            # completed step first; a re-issued tool call reuses its recorded idempotency key.
            items.append(None)
        items.append({"messages": [{"role": "user", "content": content}], "correlation_id": cid, "caller": caller.public(), "explanation_request": explanation})
        return items

    def _stream(self, graph_input: list[Any], config: dict[str, Any], context: AgentContext, session_id: str, cid: str) -> Iterator[dict[str, Any]]:
        yield {"type": "progress", "stage": "accepted", "session_id": session_id, "correlation_id": cid}
        try:
            for item in graph_input:
                for chunk in self.graph.stream(item, config, context=context, stream_mode="custom"):
                    if isinstance(chunk, dict) and chunk.get("type"):
                        yield chunk
        except AgentError as exc:
            yield self._failed(exc, session_id, cid)
            return
        except Exception:  # noqa: BLE001
            log.exception("graph run failed (correlation_id=%s)", cid)
            yield self._failed(AgentError.internal(), session_id, cid)
            return
        yield self._final(config, session_id, cid)
