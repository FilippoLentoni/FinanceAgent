"""Provider adapters (FA-PRV-03, FA-PRV-04, FA-PRV-06, FA-PRV-13, FA-PRV-14; tasks 3.2, 3.3, 3.11).

The Bedrock adapter runs against a stubbed client object only (no botocore, no network)."""

from __future__ import annotations

import pytest

from finplan_agent.core.errors import AgentError
from finplan_agent.providers import FixtureProvider, make_provider
from finplan_agent.providers.base import ExplanationProvider, GenerateRequest, ToolSpec
from finplan_agent.providers.bedrock import BedrockProvider
from tests.fakes.agent import PV, ClientError, StubBedrockClient, bedrock_config, fixture_provider_config, text_response, tool_use_response
from tests.harness import BEDROCK_ATTEMPTS

TOOLS = (ToolSpec("get_plan_version", "Read one plan version", {"type": "object", "properties": {"plan_version_id": {"type": "string"}}}, False),)


def _plan_req(**kw):
    return GenerateRequest(purpose="plan", system="sys", messages=[{"role": "user", "content": [{"text": f"show {PV}"}]}], max_tokens=512, tools=TOOLS, stable_instructions=("skill",), **kw)


def _narrate_req():
    return GenerateRequest(
        purpose="narrate", system="sys", messages=[{"role": "user", "content": [{"text": "explain"}]}], max_tokens=512, evidence=({"tool": "get_plan_version", "summary": {"plan_version_id": PV, "expected_return": 0.0612}},)
    )


def _providers():
    stub = StubBedrockClient([tool_use_response("get_plan_version", {"plan_version_id": PV}), text_response(f"Plan {PV} expects 0.0612.")])
    return [FixtureProvider(), BedrockProvider(bedrock_config(), stub)]


# ------------------------------------------------------------------ FA-PRV-06 conformance
@pytest.mark.parametrize("provider", _providers(), ids=["fixture", "bedrock"])
def test_adapter_conformance(provider):
    assert isinstance(provider, ExplanationProvider)
    planned = provider.generate(_plan_req())
    assert [c.name for c in planned.tool_calls] == ["get_plan_version"] and planned.tool_calls[0].arguments == {"plan_version_id": PV}
    narrated = provider.generate(_narrate_req())
    assert PV in narrated.text and not narrated.tool_calls
    rec = narrated.usage_record()
    assert set(rec) >= {"provider_kind", "model_id", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "estimated_cost_usd", "budget_category"}
    assert rec["provider_kind"] == provider.kind and rec["budget_category"] == "bedrock_explanations"
    assert provider.estimate_input_tokens(_narrate_req()) > 0


def test_fixture_is_deterministic_and_free():
    a, b = FixtureProvider().generate(_narrate_req()), FixtureProvider().generate(_narrate_req())
    assert a.text == b.text and a.estimated_cost_usd == 0.0
    streamed = []
    FixtureProvider().generate(_narrate_req(), on_token=streamed.append)
    assert "".join(streamed) == a.text


def test_make_provider_selects_by_kind():
    assert make_provider(fixture_provider_config()).kind == "fixture"
    assert make_provider(bedrock_config(), bedrock_client=StubBedrockClient()).kind == "bedrock"


# ------------------------------------------------------------------ FA-PRV-04 / FA-PRV-13
def test_every_request_carries_max_tokens_and_cache_points():
    stub = StubBedrockClient([text_response("ok")])
    p = BedrockProvider(bedrock_config(), stub)
    p.generate(_plan_req())
    req = stub.requests[0]
    assert req["inferenceConfig"]["maxTokens"] == 512
    assert req["modelId"] == bedrock_config().model_id
    assert req["system"][-1] == {"cachePoint": {"type": "default"}} and req["system"][1] == {"text": "skill"}
    assert req["toolConfig"]["tools"][-1] == {"cachePoint": {"type": "default"}}


def test_max_tokens_above_invocation_cap_never_sent():
    stub = StubBedrockClient([text_response("ok")])
    p = BedrockProvider(bedrock_config(), stub)
    with pytest.raises(AgentError):
        p.generate(GenerateRequest(purpose="narrate", system="s", messages=[], max_tokens=10_000))
    assert stub.requests == []


def test_cache_usage_and_cost_recorded():
    stub = StubBedrockClient([text_response("ok", inp=10, out=1000, cache_read=1000, cache_write=1000)])
    r = BedrockProvider(bedrock_config(), stub).generate(_narrate_req())
    assert r.usage.cache_read_tokens == 1000 and r.usage.cache_write_tokens == 1000
    assert r.estimated_cost_usd == pytest.approx(10 / 1000 * 0.01 + 0.05 + 0.001 + 0.0125)


def test_caching_disabled_sends_no_cache_points():
    stub = StubBedrockClient([text_response("ok")])
    BedrockProvider(bedrock_config(prompt_caching="disabled"), stub).generate(_plan_req())
    assert "cachePoint" not in repr(stub.requests[0])


def test_cache_rejection_falls_back_to_uncached_once():
    stub = StubBedrockClient([text_response("ok")], error=ClientError("ValidationException", "This model does not support prompt caching"))
    p = BedrockProvider(bedrock_config(), stub)
    assert p.generate(_plan_req()).text == "ok"
    assert "cachePoint" in repr(stub.requests[0]) and "cachePoint" not in repr(stub.requests[1])


# ------------------------------------------------------------------ FA-PRV-03 error mapping
@pytest.mark.parametrize(
    "code,expected",
    [("AccessDeniedException", "DEPENDENCY_UNAVAILABLE"), ("ResourceNotFoundException", "DEPENDENCY_UNAVAILABLE"), ("ThrottlingException", "RATE_LIMITED"), ("ValidationException", "INTERNAL")],
)
def test_error_mapping_without_fallback(code, expected):
    stub = StubBedrockClient(error=ClientError(code, "secret prompt text"))
    with pytest.raises(AgentError) as e:
        BedrockProvider(bedrock_config(prompt_caching="disabled"), stub).generate(_narrate_req())
    assert e.value.code == expected and "secret prompt text" not in str(e.value.to_envelope("corr-12345678"))
    if code == "AccessDeniedException":
        assert e.value.details["hint"] == "enable_model_access"


def test_streaming_tokens_and_tool_use():
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": "Plan "}}},
        {"contentBlockDelta": {"delta": {"text": "ok."}}},
        {"contentBlockStop": {}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "get_plan_version"}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"plan_version_id": '}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": f'"{PV}"}}'}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 50, "outputTokens": 7}}},
    ]
    stub = StubBedrockClient(stream_events=[events])
    tokens = []
    r = BedrockProvider(bedrock_config(), stub).generate(_plan_req(), on_token=tokens.append)
    assert "".join(tokens) == "Plan ok." and r.tool_calls[0].arguments == {"plan_version_id": PV}
    assert r.usage.input_tokens == 50 and r.stop_reason == "tool_use"


def test_stream_error_event_maps_to_contract_code():
    stub = StubBedrockClient(stream_events=[[{"throttlingException": {"message": "x"}}]])
    with pytest.raises(AgentError) as e:
        BedrockProvider(bedrock_config(), stub).generate(_narrate_req(), on_token=lambda t: None)
    assert e.value.code == "RATE_LIMITED"


def test_no_secret_reads_and_no_real_bedrock_calls():
    import inspect

    import finplan_agent.providers.bedrock as mod

    src = inspect.getsource(mod)
    assert "secretsmanager" not in src and "get_secret_value" not in src and "api_key" not in src.lower()
    assert BEDROCK_ATTEMPTS == []


def test_harness_blocks_real_bedrock_runtime():
    import boto3

    from tests.harness import AwsCallBlocked

    client = boto3.client("bedrock-runtime", region_name="us-east-2")
    with pytest.raises(AwsCallBlocked):
        client.converse(modelId="x", messages=[])
    BEDROCK_ATTEMPTS.clear()
