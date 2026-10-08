"""The authenticated caller of one invocation (spec mcp-access-policy, agent-session-persistence).

The Runtime's inbound ``CUSTOM_JWT`` authorizer (the environment's Cognito user pool, resolved from
``/finplan/<env>/financeagent/agent/authorizer-metadata-ref``) validates the bearer token BEFORE the
request reaches this container; with ``Authorization`` in the Runtime's request-header allowlist the
validated header is forwarded to the agent (AgentCore docs "Pass custom headers to Amazon Bedrock
AgentCore Runtime"). The agent therefore only DECODES the already-validated token to learn the caller.
It fails closed: no bearer token means ``UNAUTHORIZED`` and no graph step runs.

* ``actor_id`` (Memory actor and checkpoint owner) = ``u-`` + HMAC-SHA256 of the IdP ``sub`` keyed by
  environment and issuer: no PII in Memory or logs, and the same ``sub`` in another environment's pool
  is a different actor (environment isolation).
* ``roles`` are the ``cognito:groups`` claim (``viewer``, ``researcher``, ``plan_editor``,
  ``plan_publisher``, ``ci_test``); authorization itself happens at the Gateway policy, identically
  for every channel.
* ``channel`` is ``ci_test`` for the pipeline's ``ci_test`` client, else ``hosted_agent``.
* The raw token is kept only in memory for forwarding to the Gateway (:attr:`Caller.bearer`); it is
  never logged, put in graph state or checkpointed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import AgentError

__all__ = ["Caller", "caller_from_headers", "actor_id_for"]

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")


def actor_id_for(environment: str, issuer: str, sub: str) -> str:
    key = f"finplan-agent:{environment}:{issuer}".encode()
    return "u-" + hmac.new(key, sub.encode(), hashlib.sha256).hexdigest()[:40]


def _b64json(segment: str) -> Any:
    pad = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + pad))


@dataclass(frozen=True)
class Caller:
    actor_id: str
    roles: tuple[str, ...]
    channel: str
    environment: str
    bearer: str = field(repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        """The checkpoint-safe view (no token, no ``sub``)."""
        return {"actor_id": self.actor_id, "roles": list(self.roles), "channel": self.channel}


def caller_from_headers(headers: Mapping[str, str] | None, environment: str) -> Caller:
    auth = None
    for k, v in (headers or {}).items():
        if k.lower() == "authorization":
            auth = v
    if not auth or not auth.lower().startswith("bearer "):
        raise AgentError.unauthorized("a bearer token from this environment's identity provider is required")
    token = auth.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise AgentError.unauthorized("the bearer token is not a JWT")
    try:
        claims = _b64json(parts[1])
    except (ValueError, json.JSONDecodeError):
        raise AgentError.unauthorized("the bearer token is not a JWT") from None
    if not isinstance(claims, dict):
        raise AgentError.unauthorized("the bearer token has no claims")
    sub, iss = claims.get("sub"), claims.get("iss")
    if not isinstance(sub, str) or not sub or not isinstance(iss, str) or not iss:
        raise AgentError.unauthorized("the bearer token has no subject or issuer")
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    role_set = {g for g in groups if isinstance(g, str) and _ROLE_RE.match(g)}
    # Cognito client-credentials (machine) tokens carry sub == client_id and no user groups: the only
    # machine client of the pool is the environment's ci_test client (design D2).
    if claims.get("client_id") and claims.get("client_id") == sub and "username" not in claims:
        role_set.add("ci_test")
    roles = tuple(sorted(role_set))
    channel = "ci_test" if "ci_test" in roles else "hosted_agent"
    return Caller(actor_id=actor_id_for(environment, iss, sub), roles=roles, channel=channel, environment=environment, bearer=token)
