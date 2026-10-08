"""Caller identity and the session store (FA-SS-01/02/04; tasks 4.1, 4.2, 4.4)."""

from __future__ import annotations

import pytest

from finplan_agent import GRAPH_VERSION
from finplan_agent.core.errors import AgentError
from finplan_agent.core.ids import idempotency_key, new_session_id, validate_session_id
from finplan_agent.session.identity import caller_from_headers
from finplan_agent.session.store import REGISTRY_ACTOR, InMemorySessionOwnership, MemorySessionOwnership, make_checkpointer, open_session
from tests.fakes.agent import SESSION, auth, fake_jwt


def test_caller_from_validated_token():
    c = caller_from_headers(auth("user-a", groups=("researcher", "Bad Group")), "beta")
    assert c.actor_id.startswith("u-") and len(c.actor_id) == 42 and "user-a" not in c.actor_id
    assert c.roles == ("researcher",) and c.channel == "hosted_agent"
    assert "bearer" not in c.public() and "Bearer" not in repr(c)


def test_machine_client_is_ci_test_channel():
    tok = fake_jwt("client123", groups=(), extra={"client_id": "client123"})
    c = caller_from_headers({"authorization": f"Bearer {tok}"}, "beta")
    assert c.channel == "ci_test" and "ci_test" in c.roles


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Bearer a.e30.c"}])
def test_missing_or_bad_token_is_unauthorized(headers):
    with pytest.raises(AgentError) as e:
        caller_from_headers(headers, "beta")
    assert e.value.code == "UNAUTHORIZED"


def test_session_ids():
    sid = new_session_id()
    assert validate_session_id(sid) == sid and len(sid) >= 33
    for bad in ("x" * 32, "x" * 101, "-" + "x" * 40, "x" * 40 + "!"):
        with pytest.raises(AgentError):
            validate_session_id(bad)


def test_idempotency_key_is_deterministic():
    a = idempotency_key(SESSION, 1, "publish_plan_version", {"plan_version_id": "pv_1"})
    assert a == idempotency_key(SESSION, 1, "publish_plan_version", {"plan_version_id": "pv_1", "idempotency_key": "ignored"})
    assert a != idempotency_key(SESSION, 2, "publish_plan_version", {"plan_version_id": "pv_1"})


def test_open_session_claims_then_forbids_others():
    own = InMemorySessionOwnership()
    a = caller_from_headers(auth("a"), "beta")
    b = caller_from_headers(auth("b"), "beta")
    assert open_session(own, SESSION, a) == "new" and open_session(own, SESSION, a) == "resume"
    with pytest.raises(AgentError) as e:
        open_session(own, SESSION, b)
    assert e.value.code == "FORBIDDEN"
    own.records[SESSION]["graph_version"] = GRAPH_VERSION + 1
    with pytest.raises(AgentError) as e:
        open_session(own, SESSION, a)
    assert e.value.code == "PRECONDITION_FAILED"


class StubMemory:
    """Stub of the bedrock-agentcore data-plane events API."""

    def __init__(self):
        self.events = []

    def create_event(self, **kw):
        ev = {"eventId": f"{len(self.events)}#ab", "eventTimestamp": kw["eventTimestamp"].isoformat(), **kw}
        self.events.append(ev)
        return {"event": ev}

    def list_events(self, **kw):
        assert kw["actorId"] == REGISTRY_ACTOR and kw["includePayloads"] is True
        return {"events": [e for e in self.events if e["sessionId"] == kw["sessionId"] and e["actorId"] == kw["actorId"]]}

    def delete_event(self, **kw):
        self.events = [e for e in self.events if e["eventId"] != kw["eventId"]]


def test_memory_owner_registry_first_claim_wins():
    mem = StubMemory()
    reg = MemorySessionOwnership(mem, "finplanbetamem-abcdefghij")
    a = caller_from_headers(auth("a"), "beta")
    b = caller_from_headers(auth("b"), "beta")
    assert open_session(reg, SESSION, a) == "new"
    reg.claim(SESSION, b.actor_id, GRAPH_VERSION)  # a later claim never takes over
    with pytest.raises(AgentError):
        open_session(reg, SESSION, b)
    assert open_session(reg, SESSION, a) == "resume"
    assert mem.events[0]["payload"][0]["blob"] == {"actor_id": a.actor_id, "graph_version": GRAPH_VERSION}
    reg.release(SESSION)
    assert reg.owner(SESSION) is None


def test_checkpointer_factory():
    from langgraph_checkpoint_aws import AgentCoreMemorySaver

    assert isinstance(make_checkpointer("finplanbetamem-abcdefghij", "us-east-2"), AgentCoreMemorySaver)
    with pytest.raises(AgentError):
        make_checkpointer(None, "us-east-2")
    assert make_checkpointer(None, "us-east-2", allow_in_memory=True) is not None
