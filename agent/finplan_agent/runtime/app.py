"""AgentCore Runtime entry point (design D1, D5; task 2.2).

Implements the AgentCore Runtime HTTP protocol contract (docs "HTTP protocol contract", read
2026-10-08) with the Bedrock AgentCore Python SDK's ``BedrockAgentCoreApp`` (a Starlette app):

* container listens on ``0.0.0.0:8080`` (ARM64 image, ``container/Dockerfile``);
* ``POST /invocations``: JSON in; ``application/json`` out for ``stream: false``, otherwise
  ``text/event-stream`` (one ``data: <json>`` SSE event per agent event);
* ``GET /ping``: ``{"status": "Healthy" | "HealthyBusy"}`` (handled by the SDK; it never advances
  ``time_of_last_update`` on unchanged status, so idle sessions still time out);
* ``/ws``: NO WebSocket handler is registered in phase 1 (FA-OQ-6, design D5), so an upgrade is
  closed and HTTP invocation remains available. Registering one requires the environment's
  ``streaming.websocket`` flag WITH a recorded ``requirement_ref`` (``Settings.websocket_enabled``) and
  the build check in ``scripts/agent_gates.py``.

The Runtime passes the session ID in ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`` and, with
``Authorization`` in the Runtime's request-header allowlist and a ``customJWTAuthorizer`` configured,
the VALIDATED bearer token (``RequestContext.request_headers``).

Configuration is validated at import (:func:`create_app`): an invalid provider configuration raises, the
server never starts, and the Runtime health check fails.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["create_app", "main"]

log = logging.getLogger("finplan_agent.runtime")


def create_app(service: Any = None) -> Any:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

    from .service import AgentService

    svc = service if service is not None else AgentService.from_environment()
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: Any, context: RequestContext) -> Any:
        return svc.handle(payload, context.request_headers or {}, context.session_id)

    app.state.agent_service = svc
    return app


def main() -> None:  # pragma: no cover - container entry point
    logging.basicConfig(level=os.environ.get("FINPLAN_LOG_LEVEL", "INFO"))
    app = create_app()
    app.run(port=8080, host="0.0.0.0")  # noqa: S104 - the Runtime contract requires 0.0.0.0:8080


if __name__ == "__main__":  # pragma: no cover
    main()
