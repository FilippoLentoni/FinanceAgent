"""Identifiers the agent mints or checks: correlation IDs, session IDs and idempotency keys.

* Session IDs are the AgentCore Runtime session IDs (``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``).
  The Runtime requires at least 33 characters; the Memory checkpointer uses the same value as its
  ``sessionId`` (pattern ``[a-zA-Z0-9][a-zA-Z0-9-_]*``, at most 100 characters). A session ID that
  satisfies both is accepted; anything else is ``VALIDATION_FAILED``. New IDs are random (UUID4 hex
  plus a random suffix), so they are unguessable (spec agent-session-persistence).
* Idempotency keys of state-changing tool calls are derived deterministically from the session, the
  turn and the call's canonical arguments, so a crash-and-resume re-issues the SAME key and the
  producer's idempotency record prevents a duplicate state change.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from typing import Any

from .errors import AgentError

__all__ = [
    "SESSION_ID_MIN",
    "SESSION_ID_MAX",
    "new_correlation_id",
    "new_session_id",
    "validate_session_id",
    "valid_correlation_id",
    "idempotency_key",
    "canonical_json",
]

SESSION_ID_MIN = 33
SESSION_ID_MAX = 100
_SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*\Z")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}\Z")


def new_correlation_id() -> str:
    return f"corr-{uuid.uuid4().hex}"


def valid_correlation_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_CORRELATION_RE.match(value))


def new_session_id() -> str:
    """A fresh unguessable session ID (48 characters)."""
    return f"{uuid.uuid4().hex}{secrets.token_hex(8)}"


def validate_session_id(value: Any) -> str:
    if not isinstance(value, str) or not (SESSION_ID_MIN <= len(value) <= SESSION_ID_MAX) or not _SESSION_RE.match(value):
        raise AgentError.validation(
            f"session_id must be {SESSION_ID_MIN}-{SESSION_ID_MAX} characters of [A-Za-z0-9_-] starting with a letter or digit",
            pointer="/session_id",
        )
    return value


def canonical_json(value: Any) -> str:
    """Canonical JSON (sorted keys, no whitespace) used for hashing tool arguments."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def idempotency_key(session_id: str, turn: int, tool: str, arguments: dict[str, Any]) -> str:
    """Deterministic idempotency key (contract pattern ``[A-Za-z0-9_-]{1,128}``)."""
    args = {k: v for k, v in arguments.items() if k != "idempotency_key"}
    digest = hashlib.sha256(f"{session_id}|{turn}|{tool}|{canonical_json(args)}".encode()).hexdigest()
    return f"fa-{digest[:48]}"
