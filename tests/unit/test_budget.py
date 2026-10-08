"""Budget pre-check and guards (FA-PRV-04, FA-PRV-07, FA-PRV-12; tasks 3.4, 3.6) with mocked SSM and
metrics; graph degradation when the pre-check refuses (no Bedrock call is made)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from finplan_agent.budget.guard import BudgetGuard, BudgetSnapshot, SsmBudgetReader, StaticSpendSource
from finplan_agent.budget.metrics import COST_METRIC, NAMESPACE, CloudWatchSpendSource, emf_record, emit_usage, put_usage
from finplan_agent.core.errors import AgentError
from finplan_agent.providers.bedrock import BedrockProvider
from tests.fakes.agent import PV, StubBedrockClient, bedrock_config, make_service, run, text_response, tool_use_response

ALLOC = {"platform_infra": 8, "cpu_research": 7, "bedrock_explanations": 5, "gpu": 25, "reserve": 5}


class Reader:
    def __init__(self, allocation=ALLOC, state=None, ceiling=50.0):
        self.snap = BudgetSnapshot(allocation, state, ceiling)

    def read(self):
        return self.snap


def guard(spent=0.0, **kw):
    return BudgetGuard(bedrock_config(), spend=StaticSpendSource(spent), budget_reader=Reader(**kw))


def test_worst_case_uses_input_plus_max_tokens_at_configured_rates():
    g = guard()
    w = g.worst_case(1000)
    # caching effective: input priced at max(input, cache_write) = 0.0125; output 512 tokens at 0.05
    assert w.output_tokens == 512 and w.usd == pytest.approx(1000 / 1000 * 0.0125 + 512 / 1000 * 0.05)


def test_under_budget_proceeds_with_record():
    rec = guard(spent=1.0).preflight(worst=guard().worst_case(1000), turn_tokens=0, session_tokens=0, session_spent_usd=0.0, correlation_id="corr-12345678")
    assert rec["remaining_allocation_usd"] == pytest.approx(4.0) and rec["rates_retrieved_at"] == "2026-10-08"


def test_allocation_exhausted_refuses_with_details():
    with pytest.raises(AgentError) as e:
        guard(spent=4.999).preflight(worst=guard().worst_case(1000), turn_tokens=0, session_tokens=0, session_spent_usd=0.0, correlation_id="corr-12345678")
    d = e.value.details
    assert e.value.code == "BUDGET_EXCEEDED" and d["budget_category"] == "bedrock_explanations"
    assert d["allocation_usd"] == 5 and d["month_to_date_usd"] == pytest.approx(4.999) and d["rates_retrieved_at"] == "2026-10-08" and "estimated_usd_upper_bound" in d


def test_budget_state_flag_refuses():
    with pytest.raises(AgentError) as e:
        guard(state=json.dumps({"state": "enforced"})).preflight(worst=guard().worst_case(10), turn_tokens=0, session_tokens=0, session_spent_usd=0.0, correlation_id="corr-12345678")
    assert e.value.code == "BUDGET_EXCEEDED" and e.value.details.get("budget_state") == "enforced"


def test_session_remainder_insufficient_refuses():
    with pytest.raises(AgentError) as e:
        guard().preflight(worst=guard().worst_case(1000), turn_tokens=0, session_tokens=0, session_spent_usd=0.24, correlation_id="corr-12345678")
    assert e.value.details["limit"] == "max_cost_session_usd" and e.value.details["session_remaining_usd"] == pytest.approx(0.01)


def test_turn_and_session_token_caps():
    g = guard()
    with pytest.raises(AgentError) as e:
        g.preflight(worst=g.worst_case(4000), turn_tokens=0, session_tokens=0, session_spent_usd=0, correlation_id="corr-12345678")
    assert e.value.details["limit"] == "max_tokens_turn"
    with pytest.raises(AgentError) as e:
        g.preflight(worst=g.worst_case(100), turn_tokens=0, session_tokens=19800, session_spent_usd=0, correlation_id="corr-12345678")
    assert e.value.details["limit"] == "max_tokens_session"


def test_invalid_allocation_refuses_paid_work():
    with pytest.raises(AgentError) as e:
        guard(allocation={**ALLOC, "gpu": 100}).preflight(worst=guard().worst_case(10), turn_tokens=0, session_tokens=0, session_spent_usd=0, correlation_id="corr-12345678")
    assert e.value.code == "BUDGET_EXCEEDED" and "allocation_problem" in e.value.details


@mock_aws
def test_ssm_budget_reader():
    ssm = boto3.client("ssm", region_name="us-east-2")
    ssm.put_parameter(Name=SsmBudgetReader.ALLOCATION, Value=json.dumps(ALLOC), Type="String")
    ssm.put_parameter(Name=SsmBudgetReader.STATE, Value='{"state": "normal"}', Type="String")
    snap = SsmBudgetReader(ssm).read()
    assert snap.allocation["bedrock_explanations"] == 5 and snap.ceiling_usd is None


@mock_aws
def test_month_to_date_from_usage_metrics_across_environments():
    cw = boto3.client("cloudwatch", region_name="us-east-2")
    for env, usd in (("beta", 0.0), ("gamma", 0.25), ("prod", 0.5)):
        put_usage(cw, emf_record(environment=env, release_id=None, provider_kind="bedrock", model_id="m", usage={"estimated_cost_usd": usd, "input_tokens": 10}, tool_calls=1))
    src = CloudWatchSpendSource(cw, clock=lambda: datetime.now(UTC))
    assert src.month_to_date_usd() == pytest.approx(0.75)
    src.record_local(0.1)
    assert src.month_to_date_usd() == pytest.approx(0.85)


def test_emf_record_shape():
    rec = emf_record(environment="gamma", release_id="rel_01JABCDEFGHJKMNPQRSTVWXYZ0", provider_kind="bedrock", model_id="m", usage={"estimated_cost_usd": 0.01, "input_tokens": 5, "output_tokens": 6}, tool_calls=2, now_ms=0)
    spec = rec["_aws"]["CloudWatchMetrics"][0]
    assert spec["Namespace"] == NAMESPACE and ["BudgetCategory"] in spec["Dimensions"]
    assert rec[COST_METRIC] == 0.01 and rec["ToolCalls"] == 2 and rec["Environment"] == "gamma"
    buf = io.StringIO()
    emit_usage(rec, buf)
    assert json.loads(buf.getvalue())["BudgetCategory"] == "bedrock_explanations"


# ------------------------------------------------------------------ graph degradation
def test_exhausted_allocation_degrades_to_tool_only_without_bedrock_call():
    stub = StubBedrockClient([tool_use_response("get_plan_version", {"plan_version_id": PV})])
    cfg = bedrock_config()
    svc, tools = make_service(environment="gamma", provider=BedrockProvider(cfg, stub), provider_config=cfg, spend=StaticSpendSource(4.9999), budget_reader=Reader())
    final = run(svc, {"prompt": f"Show me plan version {PV}", "stream": False})
    assert stub.requests == []  # no Bedrock call at all
    assert tools.calls == [("get_plan_version", {"plan_version_id": PV})]  # deterministic tool-only path
    assert final["error"]["code"] == "BUDGET_EXCEEDED" and final["degraded"] == "tool_only"
    assert final["answer"]["narrative_status"] == "budget_exceeded" and final["answer"]["evidence"][0]["ok"]


def test_under_budget_bedrock_turn_records_usage_and_session_spend():
    stub = StubBedrockClient(
        [tool_use_response("get_plan_version", {"plan_version_id": PV}), text_response("", inp=10, out=5)],
        stream_events=[[{"contentBlockDelta": {"delta": {"text": f"Plan {PV} expects 0.0612."}}}, {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 10}}}]],
    )
    cfg = bedrock_config()
    usage = []
    svc, _ = make_service(environment="gamma", provider=BedrockProvider(cfg, stub), provider_config=cfg, budget_reader=Reader(), usage=usage)
    events = run(svc, {"prompt": f"Show me plan version {PV}"})
    final = events[-1]
    assert final["status"] == "completed" and final["answer"]["narrative"] == f"Plan {PV} expects 0.0612."
    assert final["answer"]["claim_check"]["passed"]
    assert all(r["inferenceConfig"]["maxTokens"] == 512 for r in stub.requests)
    assert usage[0]["invocations"] == 3 and usage[0]["estimated_cost_usd"] > 0 and usage[0]["model_id"] == cfg.model_id
