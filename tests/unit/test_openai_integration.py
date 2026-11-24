import json

import pytest

from finplan_agent.budget.guard import BudgetGuard
from finplan_agent.config.provider import ProviderConfigError, build_provider_config, load_provider_config
from finplan_agent.config.settings import load_repo_config
from finplan_agent.core.errors import AgentError
from finplan_agent.providers import make_provider
from scripts.release import bedrock_grant


def guards():
    return {
        "max_tokens_invocation": 8192,
        "max_tokens_turn": 120000,
        "max_tokens_session": 480000,
        "max_tool_calls_turn": 6,
        "max_cost_session_usd": 3.0,
        "reasoning_effort": "high",
        "rates": {"input_per_1k_usd": 0.008, "output_per_1k_usd": 0.04, "cache_write_per_1k_usd": 0.010, "cache_read_per_1k_usd": 0.0004, "source": "configured", "retrieved_at": "2026-11-23"},
    }


def test_openai_config_factory_and_shared_budget_are_guarded_without_secret_reads():
    cfg = build_provider_config("beta", "openai", "gpt-test", guards(), allowed_kinds=load_repo_config("beta")["provider_kind_allowed"])
    assert cfg.extra["reasoning_effort"] == "high" and cfg.describe()["model_id"] == "gpt-test"

    def forbidden(*args, **kwargs):
        raise AssertionError("secret/network before generation")

    assert make_provider(cfg, openai_secret_reader=forbidden).kind == "openai"
    guard = BudgetGuard(cfg)
    worst = guard.worst_case(1000, 100)
    assert worst.usd == pytest.approx(0.014)  # input may all be billed cache writes
    with pytest.raises(AgentError) as error:
        guard.preflight(worst=worst, turn_tokens=0, session_tokens=0, session_spent_usd=2.999, correlation_id="test-correlation")
    assert error.value.code == "BUDGET_EXCEEDED"


def test_openai_model_loader_keeps_model_identity_and_reasoning_effort():
    class FakeSsm:
        def get_parameters(self, Names):
            values = {"explanation-provider": "openai", "explanation-model-id": "gpt-test", "explanation-guards": json.dumps(guards())}
            return {"Parameters": [{"Name": name, "Value": values[name.rsplit("/", 1)[-1]]} for name in Names]}

    cfg = load_provider_config("gamma", FakeSsm())
    assert cfg.kind == "openai" and cfg.model_id == "gpt-test" and cfg.extra["reasoning_effort"] == "high"


def test_gpt_models_never_create_bedrock_grants_or_run_as_bedrock():
    config = {"environment": "beta", "explanation": {"provider": "openai", "model_id": "gpt-test"}, "provider_kind_allowed": ["fixture", "bedrock", "openai"]}
    assert bedrock_grant(config, object(), region="us-east-2") == []
    with pytest.raises(ProviderConfigError):
        build_provider_config("beta", "bedrock", "gpt-test", guards())
    with pytest.raises(ProviderConfigError):
        build_provider_config("beta", "openai", "bedrock-model", guards())
    with pytest.raises(ProviderConfigError):
        build_provider_config("prod", "openai", "gpt-test", guards(), allowed_kinds=load_repo_config("prod")["provider_kind_allowed"])


@pytest.mark.parametrize("streaming", [False, True])
def test_paid_incomplete_usage_is_charged_once_and_retries_hit_session_cap(streaming):
    import io
    from types import SimpleNamespace

    from finplan_agent.graph.nodes import _failure_usage, _invoke_provider
    from finplan_agent.providers.base import GenerateRequest, PaidResponseError
    from finplan_agent.providers.openai_responses import OpenAIResponsesProvider

    cfg = build_provider_config("beta", "openai", "gpt-test", guards())
    payload = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [{"type": "function_call", "name": "NEVER_EXECUTE", "arguments": "{}"}], "usage": {"input_tokens": 1000, "output_tokens": 8192, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "total_tokens": 9192}}
    calls, spends = [], []

    def transport(body, headers, stream):
        calls.append(1)
        raw = ("data: " + json.dumps({"type": "response.incomplete", "response": payload}) + "\n\n") if stream else json.dumps(payload)
        return io.BytesIO(raw.encode())

    provider = OpenAIResponsesProvider(cfg, secret_reader=lambda arn: {"OPENAI_API_KEY": "sk-" + "x" * 48}, transport=transport, env={"FINPLAN_OPENAI_SECRET_ARN": "fixture-reference"})
    ctx = SimpleNamespace(provider=provider, guard=BudgetGuard(cfg), on_spend=spends.append)
    request = GenerateRequest("narrate", "system", [], 8192)
    state = {}
    for _ in range(8):
        with pytest.raises(PaidResponseError) as caught:
            _invoke_provider(state, ctx, request, stream_tokens=streaming)
        assert caught.value.accounting.text == "" and caught.value.accounting.tool_calls == ()
        state.update(_failure_usage(caught.value))
    assert len(calls) == len(spends) == 8
    assert state["session_spent_usd"] == pytest.approx(2.68544)
    assert state["turn_usage"]["invocations"] == 8
    assert state["session_tokens"] == 73536
    with pytest.raises(AgentError) as refused:
        _invoke_provider(state, ctx, request, stream_tokens=streaming)
    assert refused.value.code == "BUDGET_EXCEEDED"
    assert len(calls) == len(spends) == 8


def test_incomplete_without_valid_usage_discloses_unknown_without_invented_charge():
    import io
    from types import SimpleNamespace

    from finplan_agent.graph.nodes import _failure_usage, _invoke_provider
    from finplan_agent.providers.base import GenerateRequest
    from finplan_agent.providers.openai_responses import OpenAIResponsesProvider

    cfg = build_provider_config("beta", "openai", "gpt-test", guards())
    provider = OpenAIResponsesProvider(cfg, secret_reader=lambda arn: {"OPENAI_API_KEY": "sk-" + "x" * 48}, transport=lambda *args: io.BytesIO(b'{"status":"incomplete"}'), env={"FINPLAN_OPENAI_SECRET_ARN": "fixture-reference"})
    spends = []
    ctx = SimpleNamespace(provider=provider, guard=BudgetGuard(cfg), on_spend=spends.append)
    with pytest.raises(AgentError) as caught:
        _invoke_provider({}, ctx, GenerateRequest("narrate", "system", [], 10), stream_tokens=False)
    assert caught.value.details["usage_known"] is False
    assert _failure_usage(caught.value) == {} and spends == []
