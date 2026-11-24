import io
import json
import urllib.error
from dataclasses import replace

import pytest

from finplan_agent.config.provider import ProviderConfig, Rates
from finplan_agent.core.errors import AgentError
from finplan_agent.providers.base import GenerateRequest, ToolSpec
from finplan_agent.providers.openai_responses import MAX_WIRE_BYTES, OpenAIResponsesProvider, responses_input


def config(**kwargs):
    return replace(ProviderConfig(environment="beta", kind="openai", model_id="configured-openai-model", max_tokens_invocation=8192, max_tokens_turn=16384, max_tokens_session=65536, max_tool_calls_turn=6, max_cost_session_usd=3, rates=Rates(input_per_1k_usd=0.008, output_per_1k_usd=0.04, cache_read_per_1k_usd=0.0004, source="configured", retrieved_at="2026-11-23")), **kwargs)


def request(**kwargs):
    return replace(GenerateRequest(purpose="narrate", system="Trusted system", stable_instructions=("Packaged skill",), messages=[{"role": "user", "content": [{"text": "Explain this synthetic result"}]}], max_tokens=8192), **kwargs)


def response(text="Hello", **kwargs):
    return {"id": "response-fixture", "status": "completed", "output": [{"type": "reasoning", "summary": [{"text": "private reasoning must not escape"}], "encrypted_content": "private opaque content"}, {"type": "message", "content": [{"type": "output_text", "text": text}]}], "usage": {"input_tokens": 1000, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 200}, "output_tokens_details": {"reasoning_tokens": 30}}, **kwargs}


class Http(io.BytesIO):
    status = 200


def provider(payload=None, **kwargs):
    calls = []
    reads = []

    def secret(arn):
        reads.append(arn)
        return {"OPENAI_API_KEY": "sk-" + "x" * 48, "OPENAI_PROJECT": "proj_fixture"}

    def transport(body, headers, streaming):
        calls.append((body, headers, streaming))
        return Http(json.dumps(payload if payload is not None else response()).encode())

    p = OpenAIResponsesProvider(config(), secret_reader=secret, transport=transport, env={"FINPLAN_OPENAI_SECRET_ARN": "configured-secret-reference"}, **kwargs)
    return p, calls, reads


def sse(*events):
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


def test_lazy_credentials_cache_and_cost_do_not_double_count_cached_input():
    p, calls, reads = provider()
    assert reads == [] and p.estimate_input_tokens(request()) > 0
    first = p.generate(request())
    p.generate(request())
    assert len(reads) == 1 and len(calls) == 2
    assert first.usage.input_tokens == 800 and first.usage.cache_read_tokens == 200
    assert first.usage.output_tokens == 50 and first.usage.total == 1050
    assert first.estimated_cost_usd == pytest.approx(0.00848)
    assert first.extra["reasoning_tokens"] == 30
    assert "private" not in first.text and first.text == "Hello"
    assert calls[0][0]["store"] is False
    assert calls[0][1]["OpenAI-Project"] == "proj_fixture"


def test_input_translation_preserves_tool_order_ids_and_error_evidence():
    messages = [{"role": "user", "content": [{"tool_request": {"name": "read_plan", "arguments": {"id": 7}}}]}, {"role": "assistant", "content": [{"text": "Checking"}, {"tool_use": {"id": "call-7", "name": "read_plan", "input": {"id": 7}}}]}, {"role": "user", "content": [{"tool_result": {"id": "call-7", "name": "read_plan", "status": "error", "content": {"code": "NOT_FOUND"}}}]}]
    items = responses_input(messages)
    assert items[1] == {"role": "assistant", "content": "Checking"}
    assert items[2]["type"] == "function_call" and items[2]["call_id"] == "call-7"
    assert json.loads(items[2]["arguments"]) == {"id": 7}
    assert items[3]["call_id"] == "call-7" and json.loads(items[3]["output"])["status"] == "error"


def test_function_call_response_requires_offered_tool_and_object_arguments():
    tool = ToolSpec(name="read_plan", description="Read", input_schema={"type": "object", "properties": {"id": {"type": "integer"}}})
    output = [{"type": "function_call", "call_id": "call-7", "name": "read_plan", "arguments": '{"id":7}'}]
    p, calls, _ = provider(response(output=output))
    result = p.generate(request(purpose="plan", tools=(tool,)))
    assert result.tool_calls[0].id == "call-7" and result.tool_calls[0].arguments == {"id": 7}
    assert result.stop_reason == "tool_use"
    assert calls[0][0]["tools"][0]["strict"] is False
    assert calls[0][0]["tools"][0]["parameters"] == tool.input_schema
    with pytest.raises(AgentError) as error:
        p.generate(request(purpose="narrate", tools=(tool,)))
    assert error.value.code == "INTERNAL"


def test_narration_adds_evidence_and_never_offers_tools_or_temperature():
    p, calls, _ = provider()
    p.config = replace(p.config, temperature=0.7, extra={"reasoning_effort": "high"})
    p.generate(request(evidence=({"value": 12},), tools=(ToolSpec(name="change_state", description="", input_schema={}),)))
    body = calls[0][0]
    assert body["instructions"] == "Trusted system\n\nPackaged skill"
    assert "Evidence summary" in body["input"][-1]["content"]
    assert "tools" not in body and "temperature" not in body
    assert body["reasoning"] == {"effort": "high"}


def test_true_sse_deltas_arrive_before_terminal_and_reasoning_is_filtered():
    p, _, _ = provider()
    tokens = []
    events = [{"type": "response.reasoning_summary_text.delta", "delta": "private native reasoning"}, {"type": "response.output_text.delta", "delta": "Hi<ana"}, {"type": "response.output_text.delta", "delta": "lysis>private tagged reasoning</analysis>"}, {"type": "response.completed", "response": response("Hi<analysis>private tagged reasoning</analysis>")}]

    class Incremental(Http):
        def readline(self, size=-1):
            line = super().readline(size)
            if b'"type": "response.completed"' in line:
                assert tokens == ["Hi"]
            return line

    stream = Incremental(sse(*events))
    p._transport = lambda body, headers, streaming: stream
    result = p.generate(request(), on_token=tokens.append)
    assert result.text == "Hi" and tokens == ["Hi"] and stream.closed
    assert result.usage.output_tokens == 50


def test_stream_function_arguments_only_execute_after_completed_snapshot():
    p, _, _ = provider()
    output = [{"type": "function_call", "call_id": "call-1", "name": "read_plan", "arguments": '{"id":1}'}]
    stream = Http(sse({"type": "response.function_call_arguments.delta", "item_id": "item-1", "output_index": 0, "delta": '{"id":'}, {"type": "response.function_call_arguments.done", "item_id": "item-1", "arguments": '{"id":1}'}, {"type": "response.completed", "response": response(output=output)}))
    p._transport = lambda body, headers, streaming: stream
    result = p.generate(request(purpose="plan", tools=(ToolSpec(name="read_plan", description="Read", input_schema={"type": "object"}),)), on_token=lambda text: None)
    assert result.tool_calls[0].arguments == {"id": 1} and stream.closed


@pytest.mark.parametrize("status,code", [(401, "DEPENDENCY_UNAVAILABLE"), (403, "DEPENDENCY_UNAVAILABLE"), (429, "RATE_LIMITED"), (500, "DEPENDENCY_UNAVAILABLE"), (400, "INTERNAL")])
def test_http_errors_are_safe_and_never_retried(status, code, caplog):
    p, _, _ = provider()
    count = []

    def fail(*args):
        count.append(1)
        raise urllib.error.HTTPError("https://sensitive.invalid/secret", status, "private response body", {}, None)

    p._transport = fail
    with pytest.raises(AgentError) as error:
        p.generate(request())
    assert error.value.code == code and len(count) == 1
    assert "private" not in str(error.value) and "sensitive" not in str(error.value) and not caplog.text


def test_refusal_is_visible_but_never_becomes_tool_execution():
    p, _, _ = provider(response(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "I cannot execute that trade."}]}]))
    result = p.generate(request())
    assert result.stop_reason == "refusal" and result.text == "I cannot execute that trade." and not result.tool_calls


@pytest.mark.parametrize("events", [[], [{"type": "response.output_text.delta", "delta": "Partial"}], [{"type": "error", "message": "private upstream detail"}], [{"type": "response.failed", "response": {"error": "private"}}]])
def test_stream_without_successful_terminal_never_returns_success(events):
    p, _, _ = provider()
    stream = Http(sse(*events))
    p._transport = lambda body, headers, streaming: stream
    with pytest.raises(AgentError) as error:
        p.generate(request(), on_token=lambda token: None)
    assert error.value.code == "DEPENDENCY_UNAVAILABLE" and stream.closed
    assert "private" not in str(error.value)


def test_incomplete_response_and_invalid_tool_json_fail_closed():
    p, _, _ = provider(response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
    with pytest.raises(AgentError) as error:
        p.generate(request())
    assert error.value.code == "BUDGET_EXCEEDED"
    p, _, _ = provider(response(output=[{"type": "function_call", "call_id": "call-1", "name": "read_plan", "arguments": "{partial"}]))
    with pytest.raises(AgentError) as error:
        p.generate(request(purpose="plan", tools=(ToolSpec(name="read_plan", description="Read", input_schema={}),)))
    assert error.value.code == "INTERNAL"


def test_caps_and_secret_failure_do_not_call_transport_or_print_credentials():
    p, calls, reads = provider()
    with pytest.raises(AgentError):
        p.generate(request(max_tokens=8193))
    assert reads == calls == []
    p._secret_reader = lambda arn: (_ for _ in ()).throw(ValueError("private credential must not escape"))
    with pytest.raises(AgentError) as error:
        p.generate(request())
    assert error.value.code == "DEPENDENCY_UNAVAILABLE" and "private" not in str(error.value)
    assert calls == []


def test_oversized_body_and_invalid_usage_are_rejected():
    p, _, _ = provider()
    stream = Http(b"x" * (MAX_WIRE_BYTES + 1))
    p._transport = lambda body, headers, streaming: stream
    with pytest.raises(AgentError):
        p.generate(request())
    assert stream.closed
    p, _, _ = provider(response(usage={"input_tokens": 1, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2}}))
    with pytest.raises(AgentError):
        p.generate(request())


def test_official_cache_read_write_breakdown_is_disjoint_and_totals_match():
    p, _, _ = provider(response(usage={"input_tokens": 12000, "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 7000, "cache_write_tokens_details": {"ttl_30m_tokens": 7000, "ttl_12h_tokens": 0}}, "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 12}, "total_tokens": 12020}))
    p.config = replace(p.config, rates=replace(p.config.rates, cache_write_per_1k_usd=0.01))
    result = p.generate(request())
    assert result.usage.input_tokens == 1000
    assert result.usage.cache_read_tokens == 4000 and result.usage.cache_write_tokens == 7000
    assert result.usage.output_tokens == 20 and result.usage.total == 12020
    assert result.estimated_cost_usd == pytest.approx(0.0804)
    # The real Astra zero-cache response keeps all 14 input tokens in the plain bucket.
    p, _, _ = provider(response(usage={"input_tokens": 14, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 21}))
    result = p.generate(request())
    assert result.usage.input_tokens == 14 and result.usage.total == 21


def test_missing_priced_cache_write_accounting_is_not_assumed_free():
    p, _, _ = provider()
    p.config = replace(p.config, rates=replace(p.config.rates, cache_write_per_1k_usd=0.01))
    with pytest.raises(AgentError):
        p.generate(request())


def test_observed_cache_write_without_verified_rate_is_rejected():
    p, _, _ = provider(response(usage={"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 3}, "output_tokens": 1, "total_tokens": 11}))
    with pytest.raises(AgentError):
        p.generate(request())


def test_standard_service_and_short_cache_lifetime_bound_verified_prices():
    p, calls, reads = provider()
    p.generate(request())
    assert calls[0][0]["service_tier"] == "default"
    assert calls[0][0]["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    with pytest.raises(AgentError):
        p.generate(request(system="x" * 250000))
    assert len(calls) == 1 and len(reads) == 1
    p, _, _ = provider(response(service_tier="priority"))
    with pytest.raises(AgentError):
        p.generate(request())
    p, _, _ = provider(response(usage={"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 3, "cache_write_tokens_details": {"ttl_12h_tokens": 3}}, "output_tokens": 1, "total_tokens": 11}))
    with pytest.raises(AgentError):
        p.generate(request())


@pytest.mark.parametrize("enabled,cache_rates", [(False, True), (True, False), (True, True)])
def test_request_cache_policy_matches_describe(enabled, cache_rates):
    payload = response(usage={"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens": 1})
    p, calls, _ = provider(payload)
    rates = replace(p.config.rates, cache_write_per_1k_usd=0.01) if cache_rates else p.config.rates
    p.config = replace(p.config, prompt_caching="enabled" if enabled else "disabled", rates=rates)
    p.generate(request())
    expected = {"mode": "implicit" if enabled and cache_rates else "explicit", "ttl": "30m"}
    assert calls[0][0]["prompt_cache_options"] == expected
    assert p.config.describe()["prompt_cache_options"] == expected
    assert p.config.describe()["prompt_caching"] == ("enabled" if enabled and cache_rates else "disabled")
