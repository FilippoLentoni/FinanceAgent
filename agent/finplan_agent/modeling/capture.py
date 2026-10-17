"""Per-invocation public input receipts, kept outside graph checkpoints.

No transport headers, credentials or hidden model reasoning are captured. Capture
fails closed before execution when a payload contains credential-shaped content.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from ..core.errors import AgentError

_KEYS = re.compile(r"^(authorization|access_token|refresh_token|id_token|client_secret|api_key|secret_access_key|session_token|password)$", re.I)
_VALUE = re.compile(r"Bearer\s+[A-Za-z0-9._~-]{16,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|X-Amz-(?:Credential|Signature)=", re.I)
_WIRE_CAPTURE = ContextVar("modeling_provider_wire_capture", default=None)


def capture_provider_wire(body):
    """Called by the actual adapter after transformation, before transport."""
    callback = _WIRE_CAPTURE.get()
    return callback(body) if callback is not None else None


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def check_public(value: Any) -> None:
    if isinstance(value, dict):
        for key, val in value.items():
            if _KEYS.fullmatch(str(key)):
                raise AgentError.validation("Credential-shaped evidence is prohibited", pointer="/capture_inputs")
            check_public(val)
    elif isinstance(value, (list, tuple)):
        for val in value:
            check_public(val)
    elif isinstance(value, str) and _VALUE.search(value):
        raise AgentError.validation("Credential-shaped evidence is prohibited", pointer="/capture_inputs")


class InputCapture:
    def __init__(self, sink=None, *, max_tool_calls=6) -> None:
        self.entries: list[dict[str, Any]] = []
        self.sink = sink
        self.max_tool_calls = max_tool_calls
        self.tool_calls = 0

    def take_tool_call(self):
        if self.tool_calls >= self.max_tool_calls:
            raise AgentError("BUDGET_EXCEEDED", "Captured workflow exhausted the existing tool call cap")
        self.tool_calls += 1

    def before(self, boundary: str, supplied: dict[str, Any]) -> dict[str, Any]:
        check_public(supplied)
        frozen = json.loads(canonical(supplied))
        item = {"boundary": boundary, "captured_at": datetime.now(UTC).isoformat(),
                "supplied": frozen, "checksum": "sha256:" + hashlib.sha256(canonical(frozen)).hexdigest(),
                "status": "started"}
        self.entries.append(item)
        if self.sink is not None:
            self.take_tool_call()
            # Synchronous immutable activity create must finish before inference.
            # A refused archive prevents execution instead of losing its input.
            try:
                item["durable_receipt"] = self.sink(copy.deepcopy(item))
            except Exception as exc:
                item.update(status="capture_failed", error_code=getattr(exc, "code", type(exc).__name__))
                raise
        return item

    def provider(self, provider: Any) -> Any:
        capture = self

        class CapturedProvider:
            kind = provider.kind
            model_id = provider.model_id

            def estimate_input_tokens(self, request):
                return provider.estimate_input_tokens(request)

            def generate(self, request, on_token=None):
                item = capture.before("provider.generate", asdict(request))
                token = _WIRE_CAPTURE.set(lambda body: capture.before("bedrock.converse", body))
                try:
                    result = provider.generate(request, on_token=on_token)
                except Exception as exc:
                    item.update(status="failed", error_code=getattr(exc, "code", type(exc).__name__))
                    raise
                finally:
                    _WIRE_CAPTURE.reset(token)
                # GenerateResult.text is the adapter's visible final text. Never raw provider reasoning.
                output = {"text": result.text, "tool_calls": [asdict(c) for c in result.tool_calls],
                          "usage": result.usage_record(), "stop_reason": result.stop_reason}
                try:
                    check_public(output)
                except AgentError:
                    item.update(status="failed", error_code="CREDENTIAL_OUTPUT_REJECTED")
                    raise
                item.update(status="completed", output=copy.deepcopy(output))
                return result

        return CapturedProvider()

    def tools(self, tools: Any) -> Any:
        capture = self

        class CapturedTools:
            def list_tools(self):
                return tools.list_tools()

            def call_tool(self, name, arguments):
                if name == "record_agent_activity":
                    capture.take_tool_call()
                    return tools.call_tool(name, arguments)
                item = capture.before("tool.call_tool", {"name": name, "arguments": arguments})
                capture.take_tool_call()
                try:
                    result = tools.call_tool(name, arguments)
                except Exception as exc:
                    item.update(status="failed", error_code=getattr(exc, "code", type(exc).__name__))
                    raise
                output = {"ok": result.ok, "result": result.result, "error": result.error}
                try:
                    check_public(output)
                except AgentError:
                    item.update(status="failed", error_code="CREDENTIAL_OUTPUT_REJECTED")
                    raise
                item.update(status="completed" if result.ok else "failed", output=copy.deepcopy(output))
                return result

        return CapturedTools()
