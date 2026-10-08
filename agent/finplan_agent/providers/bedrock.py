"""The Amazon Bedrock adapter: Converse / ConverseStream through the configured model or inference
profile, authenticated with the Runtime execution role only (design D4; tasks 3.3, 3.11).

* No secret is read. The client comes from the default credential chain (the Runtime role).
* ``modelId`` is the configured ID from ``/finplan/<env>/financeagent/config/explanation-model-id``.
* EVERY request carries ``inferenceConfig.maxTokens`` = the request's ``max_tokens``, which the graph
  sets to ``max_tokens_invocation`` (never above it).
* Prompt caching (when ``ProviderConfig.caching_effective``): one ``cachePoint`` after the tools, one
  after the system prompt plus the stable skill instructions; the request-specific part follows.
  Usage records ``cacheReadInputTokens`` / ``cacheWriteInputTokens``. If Bedrock rejects the cache
  points (model without caching support), the adapter retries ONCE uncached and stays uncached.
* Errors map to contract codes, without fallback to another provider:

  ===================================================  =========================================
  AccessDeniedException                                DEPENDENCY_UNAVAILABLE (+ model-access hint)
  ResourceNotFoundException, ModelNotReadyException,   DEPENDENCY_UNAVAILABLE
  ServiceUnavailableException, ModelTimeoutException,
  InternalServerException, ModelErrorException
  ThrottlingException, ServiceQuotaExceededException   RATE_LIMITED
  anything else (ValidationException, ...)             INTERNAL (message without request content)
  ===================================================  =========================================

Responses and prompts are never logged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..config.provider import ProviderConfig
from ..core.errors import AgentError
from .base import GenerateRequest, GenerateResult, ToolCall, Usage, estimate_tokens

__all__ = ["BedrockProvider", "to_converse_messages", "to_tool_config", "map_bedrock_error"]

_DEPENDENCY = {"ResourceNotFoundException", "ModelNotReadyException", "ServiceUnavailableException", "ModelTimeoutException", "InternalServerException", "ModelErrorException", "ModelStreamErrorException"}
_RATE = {"ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException"}


def map_bedrock_error(exc: Exception, model_id: str | None) -> AgentError:
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = str(resp.get("Error", {}).get("Code", ""))
    if code == "AccessDeniedException":
        return AgentError.dependency(
            "Amazon Bedrock denied the explanation model invocation; model access for the configured model may not be enabled in this account (enable it in the Bedrock console during bootstrap)",
            provider_kind="bedrock",
            model_id=model_id,
            hint="enable_model_access",
        )
    if code in _DEPENDENCY:
        return AgentError.dependency("the Amazon Bedrock explanation model is unavailable", provider_kind="bedrock", model_id=model_id, bedrock_error=code)
    if code in _RATE:
        return AgentError("RATE_LIMITED", "Amazon Bedrock throttled the explanation model invocation", {"provider_kind": "bedrock", "bedrock_error": code})
    return AgentError("INTERNAL", "the explanation model invocation failed", {"provider_kind": "bedrock", "bedrock_error": code or type(exc).__name__})


def _json_doc(value: Any) -> Any:
    """Converse ``json`` documents must be JSON objects; wrap anything else."""
    return value if isinstance(value, dict) else {"value": value}


def to_converse_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        blocks: list[dict[str, Any]] = []
        for b in m.get("content", []):
            if "text" in b and b["text"]:
                blocks.append({"text": b["text"]})
            elif "tool_request" in b:
                blocks.append({"text": "Requested tool call: " + json.dumps(b["tool_request"], sort_keys=True)})
            elif "tool_use" in b:
                tu = b["tool_use"]
                blocks.append({"toolUse": {"toolUseId": tu["id"], "name": tu["name"], "input": _json_doc(tu.get("input", {}))}})
            elif "tool_result" in b:
                tr = b["tool_result"]
                blocks.append({"toolResult": {"toolUseId": tr["id"], "content": [{"json": _json_doc(tr.get("content"))}], "status": "error" if tr.get("status") == "error" else "success"}})
        if not blocks:
            continue
        role = "assistant" if m.get("role") == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)  # Converse requires alternating roles
        else:
            out.append({"role": role, "content": blocks})
    return out


def to_tool_config(request: GenerateRequest, cache: bool) -> dict[str, Any] | None:
    if not request.tools:
        return None
    tools: list[dict[str, Any]] = [{"toolSpec": {"name": t.name, "description": t.description[:1000] or t.name, "inputSchema": {"json": t.input_schema or {"type": "object"}}}} for t in request.tools]
    if cache:
        tools.append({"cachePoint": {"type": "default"}})
    return {"tools": tools}


class BedrockProvider:
    kind = "bedrock"

    def __init__(self, config: ProviderConfig, client: Any) -> None:
        if config.kind != "bedrock" or not config.model_id:
            raise ValueError("BedrockProvider needs a validated bedrock ProviderConfig")
        self.config = config
        self.model_id: str | None = config.model_id
        self._client = client
        self._cache = config.caching_effective
        self.calls = 0

    # ------------------------------------------------------------------ request building
    def build_request(self, request: GenerateRequest, *, cache: bool | None = None) -> dict[str, Any]:
        cache = self._cache if cache is None else cache
        if request.max_tokens > self.config.max_tokens_invocation:
            raise AgentError("INTERNAL", "max_tokens above max_tokens_invocation", {"max_tokens": request.max_tokens})
        system: list[dict[str, Any]] = [{"text": request.system}] + [{"text": s} for s in request.stable_instructions if s]
        if cache:
            system.append({"cachePoint": {"type": "default"}})
        messages = to_converse_messages(request.messages)
        if request.purpose == "narrate" and request.evidence:
            messages.append({"role": "user", "content": [{"text": "Evidence summary (cite only these values):\n" + json.dumps(list(request.evidence), sort_keys=True, default=str)}]})
            messages = to_converse_messages(messages)
        inference: dict[str, Any] = {"maxTokens": int(request.max_tokens)}
        temperature = request.temperature if request.temperature is not None else self.config.temperature
        if temperature is not None:
            inference["temperature"] = temperature
        body: dict[str, Any] = {"modelId": self.model_id, "messages": messages, "system": system, "inferenceConfig": inference}
        tool_config = to_tool_config(request, cache)
        if tool_config is not None and request.purpose == "plan":
            body["toolConfig"] = tool_config
        return body

    def estimate_input_tokens(self, request: GenerateRequest) -> int:
        return estimate_tokens(request.prompt_chars())

    # ------------------------------------------------------------------ invocation
    def generate(self, request: GenerateRequest, on_token: Callable[[str], None] | None = None) -> GenerateResult:
        try:
            return self._invoke(request, on_token, self._cache)
        except AgentError:
            raise
        except Exception as exc:
            if self._cache and _is_cache_rejection(exc):
                self._cache = False  # the model does not support cache points: run uncached
                try:
                    return self._invoke(request, on_token, False)
                except AgentError:
                    raise
                except Exception as exc2:
                    raise map_bedrock_error(exc2, self.model_id) from None
            raise map_bedrock_error(exc, self.model_id) from None

    def _invoke(self, request: GenerateRequest, on_token: Callable[[str], None] | None, cache: bool) -> GenerateResult:
        body = self.build_request(request, cache=cache)
        self.calls += 1
        if on_token is None:
            resp = self._client.converse(**body)
            return self._from_converse(resp)
        resp = self._client.converse_stream(**body)
        return self._from_stream(resp.get("stream", []), on_token)

    def _usage(self, u: dict[str, Any] | None) -> Usage:
        u = u or {}
        return Usage(int(u.get("inputTokens", 0)), int(u.get("outputTokens", 0)), int(u.get("cacheReadInputTokens", 0)), int(u.get("cacheWriteInputTokens", 0)))

    def _result(self, text: str, calls: list[ToolCall], usage: Usage, stop: str) -> GenerateResult:
        rates = self.config.rates
        cost = rates.cost(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) if rates else 0.0
        return GenerateResult(text=text, tool_calls=tuple(calls), usage=usage, stop_reason=stop, provider_kind=self.kind, model_id=self.model_id, estimated_cost_usd=cost)

    def _from_converse(self, resp: dict[str, Any]) -> GenerateResult:
        content = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in content if "text" in b)
        calls = [ToolCall(id=b["toolUse"]["toolUseId"], name=b["toolUse"]["name"], arguments=dict(b["toolUse"].get("input") or {})) for b in content if "toolUse" in b]
        return self._result(text, calls, self._usage(resp.get("usage")), str(resp.get("stopReason", "end_turn")))

    def _from_stream(self, stream: Any, on_token: Callable[[str], None]) -> GenerateResult:
        text: list[str] = []
        calls: list[ToolCall] = []
        current: dict[str, Any] | None = None
        usage = Usage()
        stop = "end_turn"
        for event in stream:
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    current = {"id": start["toolUse"]["toolUseId"], "name": start["toolUse"]["name"], "input": ""}
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    text.append(delta["text"])
                    on_token(delta["text"])
                elif "toolUse" in delta and current is not None:
                    current["input"] += delta["toolUse"].get("input", "")
            elif "contentBlockStop" in event:
                if current is not None:
                    args = json.loads(current["input"]) if current["input"] else {}
                    calls.append(ToolCall(id=current["id"], name=current["name"], arguments=args if isinstance(args, dict) else {}))
                    current = None
            elif "messageStop" in event:
                stop = str(event["messageStop"].get("stopReason", stop))
            elif "metadata" in event:
                usage = self._usage(event["metadata"].get("usage"))
            else:
                for err in ("internalServerException", "modelStreamErrorException", "throttlingException", "validationException", "serviceUnavailableException"):
                    if err in event:
                        name = err[0].upper() + err[1:]
                        raise map_bedrock_error(_StreamError(name), self.model_id)
        return self._result("".join(text), calls, usage, stop)


class _StreamError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def _is_cache_rejection(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return False
    err = resp.get("Error", {})
    return err.get("Code") == "ValidationException" and "cach" in str(err.get("Message", "")).lower()
