"""MCP client for the environment's AgentCore Gateway (design D1 tool-call node, D2; task 5.6/5.7).

The hosted agent reaches tools ONLY through this client (spec mcp-access-policy "Single tool access
path"): the Runtime role holds no ``lambda:InvokeFunction``. Each request carries the END USER's bearer
token (design D2 fallback "forward the user token restricted to the Gateway audience"; the on-behalf-of
exchange replaces :class:`TokenProvider` once verified, FA-OQ-2). The agent never uses a service token.

Wire protocol (AgentCore Gateway docs, "Call a tool in a AgentCore gateway", read 2026-10-08):
``POST https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`` with
``Accept: application/json, text/event-stream``, ``Content-Type: application/json``,
``Authorization: Bearer <token>``, ``MCP-Protocol-Version: <a version the gateway supports>`` and a
JSON-RPC 2.0 body (``initialize``, ``tools/list`` with ``cursor`` pagination, ``tools/call``). The
response is JSON or an SSE stream of JSON-RPC messages; both are accepted. Gateway tool names are
``<target>___<tool>``; this client exposes the bare tool name and keeps the mapping.

A Lambda target's result arrives as MCP ``content`` (text holding the Lambda's JSON). A FinanceLambdasTool
error is the contract error envelope; it is surfaced as a failed :class:`ToolOutcome`, never raised, so
the graph can report it. Transport-level failures map to contract codes:

=====================  ======================
HTTP 401               UNAUTHORIZED
HTTP 403               FORBIDDEN
HTTP 404               NOT_FOUND
HTTP 429               RATE_LIMITED
HTTP 400               VALIDATION_FAILED
HTTP 5xx / network     DEPENDENCY_UNAVAILABLE
oversized result       DEPENDENCY_UNAVAILABLE (``details.reason = response_too_large``)
=====================  ======================

The token is never logged, stored in state or checkpointed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.errors import AgentError, registered_codes
from ..providers.base import ToolSpec

__all__ = [
    "TOOL_NAME_SEPARATOR",
    "GatewayMcpClient",
    "HttpResponse",
    "ToolClient",
    "ToolOutcome",
    "TokenProvider",
    "urllib_transport",
    "is_error_envelope",
]

TOOL_NAME_SEPARATOR = "___"
DEFAULT_PROTOCOL_VERSION = "2025-11-25"
CLIENT_INFO = {"name": "finplan-agent", "version": "1"}

TokenProvider = Callable[[], str]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str], bytes, float], HttpResponse]


@dataclass(frozen=True)
class ToolOutcome:
    tool: str
    ok: bool
    result: Any = None
    error: dict[str, Any] | None = None
    size_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class ToolClient(Protocol):
    def list_tools(self) -> list[ToolSpec]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


def is_error_envelope(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("code") in registered_codes() and isinstance(doc.get("message"), str) and "retryable" in doc


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https Gateway URL validated by Settings
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b"")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgentError.dependency("the tool Gateway is unreachable", reason=type(exc).__name__) from None


def _sse_messages(body: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    data: list[str] = []
    for raw in body.decode("utf-8", errors="replace").splitlines() + [""]:
        if raw.startswith("data:"):
            data.append(raw[5:].lstrip())
        elif raw == "" and data:
            try:
                msg = json.loads("\n".join(data))
                if isinstance(msg, dict):
                    out.append(msg)
            except json.JSONDecodeError:
                pass
            data = []
    return out


_HTTP_CODES = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMITED", 400: "VALIDATION_FAILED"}


class GatewayMcpClient:
    """A per-request MCP client bound to one caller's token."""

    def __init__(
        self,
        url: str,
        token_provider: TokenProvider,
        *,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 262144,
        transport: Transport | None = None,
        initialize: bool = True,
    ) -> None:
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            raise ValueError("the Gateway URL must be https")
        self._url = url
        self._token = token_provider
        self._version = protocol_version
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._transport = transport or urllib_transport
        self._do_initialize = initialize
        self._session_id: str | None = None
        self._initialized = False
        self._names: dict[str, str] = {}
        self._next_id = 0

    # ------------------------------------------------------------------ JSON-RPC
    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, notification: bool = False) -> Any:
        token = self._token()
        if not token:
            raise AgentError.unauthorized("no caller token is available for the tool Gateway")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": self._version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notification:
            self._next_id += 1
            msg["id"] = f"fa-{self._next_id}"
        resp = self._transport(self._url, headers, json.dumps(msg).encode("utf-8"), self._timeout)
        if resp.status in (200, 202) and notification:
            return None
        if resp.status != 200:
            code = _HTTP_CODES.get(resp.status, "DEPENDENCY_UNAVAILABLE")
            details: dict[str, Any] = {"http_status": resp.status, "method": method}
            if code == "VALIDATION_FAILED":
                details["pointer"] = "/params"
            raise AgentError(code, f"the tool Gateway rejected {method} (HTTP {resp.status})", details)
        if len(resp.body) > self._max_bytes:
            raise AgentError.dependency("the tool result exceeds the response byte limit; request a compact form", reason="response_too_large", limit_bytes=self._max_bytes)
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            messages = _sse_messages(resp.body)
        else:
            try:
                doc = json.loads(resp.body or b"null")
            except json.JSONDecodeError:
                raise AgentError.dependency("the tool Gateway returned a non-JSON response", method=method) from None
            messages = doc if isinstance(doc, list) else [doc]
        reply = next((m for m in messages if isinstance(m, dict) and m.get("id") == msg.get("id")), None)
        if reply is None:
            raise AgentError.dependency("the tool Gateway returned no JSON-RPC response", method=method)
        if "error" in reply:
            err = reply["error"] if isinstance(reply["error"], dict) else {}
            rpc_code = err.get("code")
            code = "VALIDATION_FAILED" if rpc_code == -32602 else "NOT_FOUND" if rpc_code == -32601 else "DEPENDENCY_UNAVAILABLE"
            details = {"rpc_code": rpc_code, "method": method}
            if code == "VALIDATION_FAILED":
                details["pointer"] = "/params"
            raise AgentError(code, f"the tool Gateway returned a JSON-RPC error for {method}", details)
        return reply.get("result")

    def _ensure_initialized(self) -> None:
        if self._initialized or not self._do_initialize:
            return
        self._rpc("initialize", {"protocolVersion": self._version, "capabilities": {}, "clientInfo": CLIENT_INFO})
        self._rpc("notifications/initialized", notification=True)
        self._initialized = True

    # ------------------------------------------------------------------ tools
    def list_tools(self) -> list[ToolSpec]:
        self._ensure_initialized()
        specs: list[ToolSpec] = []
        cursor: str | None = None
        for _ in range(50):  # pagination bound
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {}) or {}
            for t in result.get("tools", []):
                full = str(t.get("name", ""))
                bare = full.split(TOOL_NAME_SEPARATOR)[-1]
                if not bare:
                    continue
                self._names[bare] = full
                specs.append(ToolSpec(name=bare, description=str(t.get("description") or bare), input_schema=dict(t.get("inputSchema") or {"type": "object"})))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return specs

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self._ensure_initialized()
        if not self._names:
            self.list_tools()
        full = self._names.get(name)
        if full is None:
            return ToolOutcome(tool=name, ok=False, error={"code": "NOT_FOUND", "message": f"tool {name} is not offered by the Gateway", "retryable": False, "details": {}})
        try:
            result = self._rpc("tools/call", {"name": full, "arguments": arguments}) or {}
        except AgentError as exc:
            return ToolOutcome(tool=name, ok=False, error={"code": exc.code, "message": exc.message, "retryable": bool(exc.retryable), "details": exc.details})
        size = len(json.dumps(result, default=str))
        doc: Any = result.get("structuredContent")
        if doc is None:
            texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
            joined = "".join(texts)
            try:
                doc = json.loads(joined) if joined else None
            except json.JSONDecodeError:
                doc = {"text": joined}
        if is_error_envelope(doc):
            return ToolOutcome(tool=name, ok=False, error=doc, size_bytes=size)
        if result.get("isError"):
            return ToolOutcome(tool=name, ok=False, error={"code": "DEPENDENCY_UNAVAILABLE", "message": "the tool call failed at the Gateway", "retryable": True, "details": {}}, size_bytes=size)
        return ToolOutcome(tool=name, ok=True, result=doc, size_bytes=size)
