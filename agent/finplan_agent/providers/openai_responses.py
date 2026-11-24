"""Direct OpenAI Responses adapter with bounded JSON/SSE I/O and role-held secrets.

Only function tools supplied by the graph are exposed. Provider state, reasoning
items, authorization headers and API response/error bodies are never logged.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from ..config.provider import ProviderConfig
from ..core.errors import AgentError
from .base import GenerateRequest, GenerateResult, PaidResponseError, ToolCall, Usage, estimate_tokens
from .visible_text import VisibleText, visible_text

MAX_WIRE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 250000
MAX_EVENT_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 300
SECRET_REFRESH_SECONDS = 300
Transport = Callable[[dict[str, Any], Mapping[str, str], bool], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> Any:
        raise AgentError.dependency("the OpenAI explanation endpoint refused a redirect", provider_kind="openai")


def https_transport(body: dict[str, Any], headers: Mapping[str, str], stream: bool) -> Any:
    request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), headers=dict(headers), method="POST")
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=HTTP_TIMEOUT_SECONDS)


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral history, preserving call IDs, ordering and tool errors."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = "assistant" if message.get("role") == "assistant" else "user"
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str) and block["text"]:
                items.append({"role": role, "content": block["text"]})
            elif isinstance(block.get("tool_request"), dict):
                items.append({"role": role, "content": "Requested tool call: " + json.dumps(block["tool_request"], sort_keys=True)})
            elif isinstance(block.get("tool_use"), dict):
                call = block["tool_use"]
                items.append({"type": "function_call", "call_id": call["id"], "name": call["name"], "arguments": json.dumps(call.get("input", {}), sort_keys=True)})
            elif isinstance(block.get("tool_result"), dict):
                result = block["tool_result"]
                content = {"status": "error" if result.get("status") == "error" else "success", "content": result.get("content")}
                items.append({"type": "function_call_output", "call_id": result["id"], "output": json.dumps(content, sort_keys=True, default=str)})
    return items


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid usage count")
    return value


class OpenAIResponsesProvider:
    kind = "openai"

    def __init__(self, config: ProviderConfig, secret_reader: Callable[[str], Any] | None = None, transport: Transport | None = None, env: Mapping[str, str] | None = None) -> None:
        if config.kind != self.kind or not config.model_id or config.rates is None:
            raise ValueError("OpenAIResponsesProvider needs a validated OpenAI model and rates")
        self.config, self.model_id = config, config.model_id
        self._env = dict(os.environ if env is None else env)
        self._secret_reader = secret_reader or self._read_secret
        self._transport = transport or https_transport
        self._secret: dict[str, str] | None = None
        self._loaded_at = float("-inf")
        self._lock = threading.Lock()
        self.calls = 0

    @staticmethod
    def _read_secret(arn: str) -> str:
        from ..core.aws_clients import client

        result = client("secretsmanager", connect_timeout=3, read_timeout=5, max_attempts=1).get_secret_value(SecretId=arn)
        return result["SecretString"]

    def _credentials(self) -> dict[str, str]:
        with self._lock:
            if self._secret is not None and time.monotonic() - self._loaded_at < SECRET_REFRESH_SECONDS:
                return self._secret
            arn = self._env.get("FINPLAN_OPENAI_SECRET_ARN")
            if not arn:
                raise AgentError.dependency("the OpenAI project credential reference is not configured", provider_kind=self.kind)
            try:
                raw = self._secret_reader(arn)
                if isinstance(raw, str):
                    if len(raw) > 16384:
                        raise ValueError("oversized secret")
                    raw = json.loads(raw)
                if not isinstance(raw, Mapping):
                    raise ValueError("invalid secret shape")
                key = raw.get("OPENAI_API_KEY")
                project = raw.get("OPENAI_PROJECT")
                if not isinstance(key, str) or not key.startswith("sk-") or len(key) < 20 or any(c.isspace() for c in key):
                    raise ValueError("invalid key")
                if project is not None and (not isinstance(project, str) or not project.startswith("proj_") or not all(c.isalnum() or c in "_-" for c in project)):
                    raise ValueError("invalid project")
                self._secret = {"OPENAI_API_KEY": key, **({"OPENAI_PROJECT": project} if project else {})}
                self._loaded_at = time.monotonic()
                return self._secret
            except Exception:
                raise AgentError.dependency("the OpenAI project credential could not be loaded", provider_kind=self.kind) from None

    def estimate_input_tokens(self, request: GenerateRequest) -> int:
        return estimate_tokens(request.prompt_chars())

    def build_request(self, request: GenerateRequest, *, stream: bool = False) -> dict[str, Any]:
        if request.max_tokens <= 0 or request.max_tokens > self.config.max_tokens_invocation:
            raise AgentError.internal("model output-token request exceeds its configured cap")
        inputs = responses_input(request.messages)
        if request.purpose == "narrate" and request.evidence:
            inputs.append({"role": "user", "content": "Evidence summary (cite only these values):\n" + json.dumps(list(request.evidence), sort_keys=True, default=str)})
        body: dict[str, Any] = {"model": self.model_id, "instructions": "\n\n".join([request.system, *request.stable_instructions]), "input": inputs, "max_output_tokens": request.max_tokens, "store": False, "stream": stream, "service_tier": "default", "prompt_cache_options": {"mode": "implicit" if self.config.caching_effective else "explicit", "ttl": "30m"}}
        effort = self.config.extra.get("reasoning_effort")
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        # Reasoning-model temperature support differs; omit it rather than silently
        # changing model or retrying a request with different inference semantics.
        if request.purpose == "plan" and request.tools:
            body["tools"] = [{"type": "function", "name": tool.name, "description": tool.description[:1000] or tool.name, "parameters": tool.input_schema or {"type": "object"}, "strict": False} for tool in request.tools]
            body["parallel_tool_calls"] = False
        if len(json.dumps(body).encode()) > MAX_REQUEST_BYTES:
            raise AgentError.budget("the model input exceeds its bounded wire-size limit", provider_kind=self.kind)
        return body

    def _usage(self, response: dict[str, Any]) -> Usage:
        raw = response.get("usage")
        if not isinstance(raw, dict) or not {"input_tokens", "output_tokens"} <= set(raw):
            raise ValueError("missing usage accounting")
        details = raw.get("input_tokens_details") or {}
        if not isinstance(details, dict):
            raise ValueError("invalid input-token details")
        total = _count(raw["input_tokens"])
        output = _count(raw["output_tokens"])
        cached = _count(details.get("cached_tokens", 0))
        written = _count(details.get("cache_write_tokens", 0))
        write_lifetimes = details.get("cache_write_tokens_details") or {}
        if not isinstance(write_lifetimes, dict) or _count(write_lifetimes.get("ttl_12h_tokens", 0)):
            raise ValueError("unexpected cache-write lifetime price")
        if self.config.rates and self.config.rates.cache_write_per_1k_usd is not None and "cache_write_tokens" not in details:
            raise ValueError("missing separately priced cache-write usage")
        if written and self.config.rates and self.config.rates.cache_write_per_1k_usd is None:
            raise ValueError("cache-write usage needs a verified rate")
        if cached + written > total:
            raise ValueError("cache input exceeds total input")
        if "total_tokens" in raw and _count(raw["total_tokens"]) != total + output:
            raise ValueError("usage total differs from input and output")
        return Usage(total - cached - written, output, cached, written)

    def _result(self, response: dict[str, Any], request: GenerateRequest) -> GenerateResult:
        if response.get("service_tier") not in (None, "default"):
            raise ValueError("unexpected service tier price")
        if response.get("status") != "completed":
            error = AgentError.budget("the OpenAI response reached its output-token cap before completion", provider_kind=self.kind) if response.get("status") == "incomplete" and (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else AgentError.dependency("the OpenAI response did not complete", provider_kind=self.kind)
            try:
                usage = self._usage(response)
                if usage.total > 10_000_000 or self.config.rates is None:
                    raise ValueError("unbounded or unpriced usage")
                cost = self.config.rates.cost(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens)
                accounting = GenerateResult("", (), usage, "incomplete", self.kind, self.model_id, cost)
            except (ValueError, TypeError, KeyError):
                error.details["usage_known"] = False
                raise error from None
            raise PaidResponseError(error, accounting)
        offered = {tool.name for tool in request.tools} if request.purpose == "plan" else set()
        texts: list[str] = []
        refusals: list[str] = []
        calls: list[ToolCall] = []
        ids: set[str] = set()
        output = response.get("output")
        if not isinstance(output, list):
            raise ValueError("missing output items")
        for item in output:
            if not isinstance(item, dict):
                raise ValueError("invalid output item")
            if item.get("type") == "reasoning":
                continue
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                        texts.append(block["text"])
                    elif block.get("type") == "refusal" and isinstance(block.get("refusal"), str):
                        refusals.append(block["refusal"])
            elif item.get("type") == "function_call":
                call_id, name, arguments = item.get("call_id"), item.get("name"), item.get("arguments")
                if not isinstance(call_id, str) or not call_id or call_id in ids or name not in offered or not isinstance(arguments, str):
                    raise ValueError("invalid or unoffered function call")
                args = json.loads(arguments)
                if not isinstance(args, dict):
                    raise ValueError("function arguments must be an object")
                ids.add(call_id)
                calls.append(ToolCall(id=call_id, name=name, arguments=args))
            else:
                raise ValueError("unsupported output item")
        if refusals and calls:
            raise ValueError("a refused response cannot execute a tool")
        usage = self._usage(response)
        rates = self.config.rates
        cost = rates.cost(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) if rates else 0.0
        return GenerateResult(text=visible_text("".join(refusals or texts)), tool_calls=tuple(calls), usage=usage, stop_reason="refusal" if refusals else "tool_use" if calls else "end_turn", provider_kind=self.kind, model_id=self.model_id, estimated_cost_usd=cost, extra={"response_id": response.get("id"), "resolved_model": response.get("model"), "reasoning_tokens": _count(((response.get("usage") or {}).get("output_tokens_details") or {}).get("reasoning_tokens", 0))})

    @staticmethod
    def _events(response: Any) -> Any:
        data: list[bytes] = []
        event_bytes = total = 0
        deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("stream deadline exceeded")
            line = response.readline(MAX_EVENT_BYTES + 1)
            total += len(line)
            if total > MAX_WIRE_BYTES or len(line) > MAX_EVENT_BYTES:
                raise ValueError("oversized stream")
            if not line or line in (b"\n", b"\r\n"):
                if data:
                    raw = b"\n".join(data)
                    data, event_bytes = [], 0
                    if raw != b"[DONE]":
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise ValueError("invalid event")
                        yield event
                if not line:
                    return
            elif line.startswith(b"data:"):
                fragment = line[5:].lstrip().rstrip(b"\r\n")
                event_bytes += len(fragment)
                if event_bytes > MAX_EVENT_BYTES:
                    raise ValueError("oversized event")
                data.append(fragment)

    def _stream(self, response: Any, request: GenerateRequest, on_token: Callable[[str], None]) -> GenerateResult:
        visible, emitted = VisibleText(), []
        for event in self._events(response):
            kind = event.get("type")
            if kind in ("response.output_text.delta", "response.refusal.delta"):
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise ValueError("invalid text delta")
                text = visible.feed(delta)
                if text:
                    emitted.append(text)
                    on_token(text)
            elif kind == "response.completed":
                visible.finish()
                result = self._result(event["response"], request)
                prefix = "".join(emitted)
                if not prefix and result.text:
                    on_token(result.text)
                elif prefix != result.text:
                    raise ValueError("stream text differs from completed output")
                return result
            elif kind in ("error", "response.failed", "response.incomplete"):
                if kind in ("response.incomplete", "response.failed") and isinstance(event.get("response"), dict):
                    return self._result(event.get("response", {}), request)
                raise AgentError.dependency("the OpenAI response stream failed", provider_kind=self.kind, usage_known=False)
            # Function-call deltas are intentionally not executable. Only the fully
            # completed response's JSON arguments become provider-neutral ToolCalls.
        raise AgentError.dependency("the OpenAI stream ended before completion", provider_kind=self.kind, usage_known=False)

    def generate(self, request: GenerateRequest, on_token: Callable[[str], None] | None = None) -> GenerateResult:
        body = self.build_request(request, stream=on_token is not None)
        credentials = self._credentials()
        headers = {"Authorization": "Bearer " + credentials["OPENAI_API_KEY"], "Content-Type": "application/json", "Accept": "text/event-stream" if on_token is not None else "application/json"}
        if credentials.get("OPENAI_PROJECT"):
            headers["OpenAI-Project"] = credentials["OPENAI_PROJECT"]
        from ..modeling.capture import capture_provider_wire

        captured = capture_provider_wire(body)
        response = None
        try:
            self.calls += 1
            response = self._transport(body, headers, on_token is not None)
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise urllib.error.HTTPError("", status, "", {}, None)
            if on_token is not None:
                result = self._stream(response, request, on_token)
            else:
                raw = response.read(MAX_WIRE_BYTES + 1)
                if len(raw) > MAX_WIRE_BYTES:
                    raise ValueError("oversized response")
                result = self._result(json.loads(raw), request)
            if captured is not None:
                captured["status"] = "completed"
            return result
        except AgentError:
            if captured is not None:
                captured.update(status="failed", error_code="AgentError")
            raise
        except urllib.error.HTTPError as error:
            if captured is not None:
                captured.update(status="failed", error_code="HttpError")
            code = "RATE_LIMITED" if error.code == 429 else "DEPENDENCY_UNAVAILABLE" if error.code in (401, 403, 404) or error.code >= 500 else "INTERNAL"
            raise AgentError(code, "the OpenAI explanation request failed", {"provider_kind": self.kind, "http_status": error.code}) from None
        except (OSError, TimeoutError):
            if captured is not None:
                captured.update(status="failed", error_code="TransportError")
            raise AgentError.dependency("the OpenAI explanation endpoint is unavailable", provider_kind=self.kind, usage_known=False) from None
        except Exception:
            if captured is not None:
                captured.update(status="failed", error_code="InvalidResponse")
            raise AgentError.internal("the OpenAI explanation response was invalid") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
