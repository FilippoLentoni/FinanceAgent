"""Session persistence: LangGraph checkpoints in AgentCore Memory plus the session-owner registry
(design D3; tasks 4.1, 4.2, 4.4).

* **Checkpointer.** :class:`langgraph_checkpoint_aws.AgentCoreMemorySaver` on the environment's Memory
  resource (``FINPLAN_MEMORY_ID``), ``thread_id`` = Runtime session ID, ``actor_id`` = the caller's
  salted hash. LangGraph writes a checkpoint after every completed step. Event expiry (default 30
  days, 7-365) and the absence of long-term strategies are properties of the Memory resource (IaC).
* **Owner registry.** Memory namespaces events by actor, so caller B presenting caller A's session ID
  would silently get an EMPTY session instead of the ``FORBIDDEN`` the spec requires. The registry
  records the owning actor of every session ID under one fixed registry actor in the same Memory
  resource (one blob event per claim). Before ANY checkpoint read the agent checks it: unknown -> the
  caller claims it; another actor -> ``FORBIDDEN`` (nothing is read or written); newer graph version
  -> ``PRECONDITION_FAILED`` (start a new session instead of misreading it). Session IDs from another
  environment are unknown here (one Memory resource per environment), so no state of theirs is read.
* **Deletion.** :func:`delete_session` removes the caller's checkpoints and the registry claim; the next
  message with that ID starts a new empty session.

Offline tests and local runs use :class:`InMemorySessionOwnership` and LangGraph's ``InMemorySaver``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from .. import GRAPH_VERSION
from ..core.errors import AgentError
from .identity import Caller

__all__ = [
    "REGISTRY_ACTOR",
    "SessionOwnership",
    "InMemorySessionOwnership",
    "MemorySessionOwnership",
    "open_session",
    "delete_session",
    "make_checkpointer",
]

REGISTRY_ACTOR = "finplan-session-registry"


class SessionOwnership(Protocol):
    def owner(self, session_id: str) -> dict[str, Any] | None: ...

    def claim(self, session_id: str, actor_id: str, graph_version: int) -> None: ...

    def release(self, session_id: str) -> None: ...


class InMemorySessionOwnership:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def owner(self, session_id: str) -> dict[str, Any] | None:
        return self.records.get(session_id)

    def claim(self, session_id: str, actor_id: str, graph_version: int) -> None:
        self.records[session_id] = {"actor_id": actor_id, "graph_version": graph_version}

    def release(self, session_id: str) -> None:
        self.records.pop(session_id, None)


class MemorySessionOwnership:
    """Owner registry in the environment's AgentCore Memory (data plane ``bedrock-agentcore``)."""

    def __init__(self, client: Any, memory_id: str) -> None:
        self._client = client
        self._memory_id = memory_id

    def _events(self, session_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        token = None
        for _ in range(20):
            kw: dict[str, Any] = {"memoryId": self._memory_id, "actorId": REGISTRY_ACTOR, "sessionId": session_id, "includePayloads": True, "maxResults": 100}
            if token:
                kw["nextToken"] = token
            resp = self._client.list_events(**kw)
            events.extend(resp.get("events", []))
            token = resp.get("nextToken")
            if not token:
                break
        return events

    def owner(self, session_id: str) -> dict[str, Any] | None:
        try:
            events = self._events(session_id)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") == "ResourceNotFoundException":
                return None
            raise AgentError.dependency("the session store is unavailable") from None
        claims = []
        for ev in events:
            for p in ev.get("payload", []):
                blob = p.get("blob")
                if isinstance(blob, dict) and blob.get("actor_id"):
                    claims.append((ev.get("eventTimestamp"), blob))
        if not claims:
            return None
        # The FIRST claim owns the session: a later claim by another actor can never take it over.
        claims.sort(key=lambda c: str(c[0]))
        return dict(claims[0][1])

    def claim(self, session_id: str, actor_id: str, graph_version: int) -> None:
        try:
            self._client.create_event(
                memoryId=self._memory_id,
                actorId=REGISTRY_ACTOR,
                sessionId=session_id,
                eventTimestamp=datetime.now(UTC),
                payload=[{"blob": {"actor_id": actor_id, "graph_version": graph_version}}],
            )
        except Exception:
            raise AgentError.dependency("the session store is unavailable") from None

    def release(self, session_id: str) -> None:
        try:
            for ev in self._events(session_id):
                self._client.delete_event(memoryId=self._memory_id, actorId=REGISTRY_ACTOR, sessionId=session_id, eventId=ev["eventId"])
        except Exception:
            raise AgentError.dependency("the session store is unavailable") from None


def open_session(ownership: SessionOwnership, session_id: str, caller: Caller) -> str:
    """Check (and on first use claim) ``session_id`` for ``caller`` BEFORE any checkpoint read.

    Returns ``"new"`` or ``"resume"``; raises ``FORBIDDEN`` for another caller's session."""
    rec = ownership.owner(session_id)
    if rec is None:
        ownership.claim(session_id, caller.actor_id, GRAPH_VERSION)
        rec = ownership.owner(session_id)
        if rec is not None and rec.get("actor_id") != caller.actor_id:  # lost a concurrent claim race
            raise AgentError.forbidden("this session belongs to another caller")
        return "new"
    if rec.get("actor_id") != caller.actor_id:
        raise AgentError.forbidden("this session belongs to another caller")
    if int(rec.get("graph_version", 0)) > GRAPH_VERSION:
        raise AgentError("PRECONDITION_FAILED", "this session was written by a newer agent version; start a new session", {"graph_version": rec.get("graph_version")})
    return "resume"


def delete_session(ownership: SessionOwnership, checkpointer: Any, session_id: str, caller: Caller) -> None:
    rec = ownership.owner(session_id)
    if rec is None:
        return
    if rec.get("actor_id") != caller.actor_id:
        raise AgentError.forbidden("this session belongs to another caller")
    try:
        checkpointer.delete_thread(session_id, caller.actor_id)
    except TypeError:  # LangGraph's InMemorySaver takes the thread ID only
        checkpointer.delete_thread(session_id)
    ownership.release(session_id)


def make_checkpointer(memory_id: str | None, region: str, *, allow_in_memory: bool = False) -> Any:
    """The AgentCore Memory checkpointer; an in-process saver only when explicitly allowed (tests)."""
    if memory_id:
        from langgraph_checkpoint_aws import AgentCoreMemorySaver

        return AgentCoreMemorySaver(memory_id, region_name=region)
    if allow_in_memory:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    raise AgentError.validation("FINPLAN_MEMORY_ID is not set; the agent cannot persist sessions", pointer="/FINPLAN_MEMORY_ID")
