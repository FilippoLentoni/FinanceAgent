"""Per-model recommendation tools (contracts 1.14.0): routing, instrument universes, explanations."""
from copy import deepcopy

import pytest

from finplan_agent.graph.model_tools import requested_instruments, requested_model
from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS, CVAR_TOOL, LIFECYCLE_TOOLS, MIN_VARIANCE_TOOL, PPO_TOOL
from tests.fakes.agent import SESSION, FakeToolClient, auth, make_service, saved_history
from tests.unit.test_classical_workflows import A, B, doc
from tests.unit.test_saved_portfolio_recommendations import NoProvider
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation

EIGHT = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "VOO"]
PROMPT = "Give me a portfolio recommendation for AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA, VOO using model {}"
SNAP = "snap_01JABCDEFGHJKMNPQRSTVWXY03"


def scenario(instruments=EIGHT):
    return {"input_snapshot_id": SNAP, "as_of": "2026-10-08",
            "holdings": {"weights": [{"instrument_id": i, "weight": 0} for i in instruments], "cash_weight": 1, "portfolio_value": 100000, "high_watermark": 100000}}


def service(rec, *, history=None):
    classical = doc(rec)

    def classical_for(strategy):
        def run(args):
            out = deepcopy(classical)
            out["recommendation"]["strategy"] = strategy
            return out
        return run

    ppo = deepcopy(rec)
    ppo["portfolio_state"]["source"] = "supplied"
    results = {PPO_TOOL: lambda _: {"recommendation": deepcopy(ppo), "synthetic": True},
               MIN_VARIANCE_TOOL: classical_for("min_variance"), CVAR_TOOL: classical_for("cvar"),
               "get_portfolio_history": lambda _: history if history is not None else saved_history([t["instrument_id"] for t in rec["target_weights"]]),
               "explain_classical_recommendation": lambda _: dict(deepcopy(classical), analysis_id=B, analysis_kind="explanation"),
               "query_market_data": lambda _: {"snapshot": {"input_snapshot_id": rec["input_snapshot_id"], "status": "approved"}, "partial": False}}
    names = CLASSICAL_TOOLS | LIFECYCLE_TOOLS | {PPO_TOOL, "query_market_data"}
    tools = FakeToolClient(results, extra_tools=list(names))
    svc, _ = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry(n, n in {"resolve_portfolio_decision", "run_portfolio_research"}, "reader") for n in names], environment="beta")
    svc.deps.skills, svc.deps.stable_instructions = load_skills()
    return svc, tools


def ask(svc, payload):
    return svc.handle({**payload, "stream": False}, auth(), runtime_session_id=SESSION)


def test_model_words_and_explicit_universe_are_parsed():
    assert requested_instruments(PROMPT.format("cvar")) == EIGHT
    assert [requested_model(PROMPT.format(m)) for m in ("cvar", "min_variance", "min variance", "minimum-variance", "ppo", "CVaR")] == ["cvar", "min_variance", "min_variance", "min_variance", "ppo", "cvar"]
    assert requested_instruments("Should I buy GOOGL today?") == []  # one symbol in a question is not a universe
    assert requested_instruments("How should I invest today?") == []


@pytest.mark.parametrize("model,tool", [("cvar", CVAR_TOOL), ("min_variance", MIN_VARIANCE_TOOL), ("min variance", MIN_VARIANCE_TOOL), ("ppo", PPO_TOOL)])
def test_free_text_routes_to_the_named_model_with_the_listed_instruments(recommendation, model, tool):
    svc, tools = service(recommendation)
    final = ask(svc, {"prompt": PROMPT.format(model)})
    assert final["status"] == "completed", final
    assert tools.calls == [(tool, {"instruments": EIGHT})]
    assert [e["tool"] for e in final["answer"]["evidence"]] == [tool]
    assert final["answer"]["recommendation"] and final["answer"]["narrative"]
    assert final["answer"]["claim_check"]["passed"], final["answer"]["claim_check"]


@pytest.mark.parametrize("tool", [MIN_VARIANCE_TOOL, CVAR_TOOL, PPO_TOOL])
def test_runner_tool_request_runs_exactly_that_tool_once(recommendation, tool):
    svc, tools = service(recommendation)
    arguments = {**scenario(), "instruments": EIGHT}
    final = ask(svc, {"prompt": "Give me a portfolio recommendation using model x.", "tool_request": {"name": tool, "arguments": arguments}})
    assert final["status"] == "completed", final
    assert tools.calls == [(tool, arguments)]
    assert [e["tool"] for e in final["answer"]["evidence"]] == [tool]


def test_tool_request_without_instruments_uses_the_scenario_holdings(recommendation):
    svc, tools = service(recommendation)
    ask(svc, {"tool_request": {"name": CVAR_TOOL, "arguments": scenario(["AAPL", "VOO"])}})
    assert tools.calls == [(CVAR_TOOL, {**scenario(["AAPL", "VOO"]), "instruments": ["AAPL", "VOO"]})]


def test_structured_recommendation_without_model_runs_ppo_on_the_saved_book_universe(recommendation):
    svc, tools = service(recommendation)
    final = ask(svc, {"recommendation": {}})
    assert final["status"] == "completed", final
    assert tools.calls == [("get_portfolio_history", {"limit": 1}), (PPO_TOOL, {"instruments": ["AAPL", "GOOGL", "NFLX", "NVDA", "VOO"]})]
    # The universe lookup feeds the model call; the model output is what the answer presents.
    assert final["answer"]["recommendation"]["strategy"] == "ppo"


def test_structured_recommendation_model_selects_the_tool(recommendation):
    svc, tools = service(recommendation)
    final = ask(svc, {"recommendation": {"model": "cvar", "instruments": ["AAPL", "VOO"]}})
    assert final["status"] == "completed" and tools.calls == [(CVAR_TOOL, {"instruments": ["AAPL", "VOO"]})]
    bad = ask(svc, {"recommendation": {"model": "mean_variance"}})
    assert bad["status"] == "failed" and bad["error"]["code"] == "VALIDATION_FAILED" and bad["error"]["details"]["pointer"] == "/recommendation/model"


def test_missing_universe_is_a_clear_validation_error(recommendation):
    svc, tools = service(recommendation, history={"portfolio_id": "pf_01KDVDNAZ83BAMMYCEGWF33DPM", "history": [], "next_token": None, "synthetic": True, "contract_version": "1.14.0"})
    final = ask(svc, {"recommendation": {}})
    assert final["status"] == "failed" and final["error"]["code"] == "VALIDATION_FAILED"
    assert "instruments" in final["error"]["message"] and "instruments" in final["answer"]["narrative"]
    assert [name for name, _ in tools.calls] == ["get_portfolio_history"]


def test_mean_variance_is_refused_without_substitution(recommendation):
    svc, tools = service(recommendation)
    final = ask(svc, {"prompt": PROMPT.format("mean variance")})
    assert not tools.calls and "no longer served" in final["answer"]["narrative"]


@pytest.mark.parametrize("model,tool", [("cvar", CVAR_TOOL), ("min_variance", MIN_VARIANCE_TOOL)])
@pytest.mark.parametrize("followup", ["Why did you recommend this?", "Explain the risk", "Explain the main drivers"])
def test_classical_followups_reread_the_stored_analysis(recommendation, model, tool, followup):
    svc, tools = service(recommendation)
    assert ask(svc, {"prompt": PROMPT.format(model)})["status"] == "completed"
    final = ask(svc, {"prompt": followup})
    assert final["status"] == "completed", final
    assert tools.calls[-1] == ("explain_classical_recommendation", {"analysis_id": A})
    assert final["answer"]["claim_check"]["passed"], final["answer"]["claim_check"]
    assert final["answer"]["skills_used"][0]["name"] == "explain-classical-recommendation"


@pytest.mark.parametrize("followup", ["Why did you recommend this?", "Explain the risk", "Explain the main drivers"])
def test_ppo_followups_recall_pinned_to_the_original_scenario_and_universe(recommendation, followup):
    svc, tools = service(recommendation)
    arguments = {**scenario(["AAPL", "GOOGL", "NFLX", "NVDA", "VOO"]), "input_snapshot_id": recommendation["input_snapshot_id"], "instruments": ["AAPL", "GOOGL", "NFLX", "NVDA", "VOO"]}
    assert ask(svc, {"tool_request": {"name": PPO_TOOL, "arguments": arguments}})["status"] == "completed"
    final = ask(svc, {"prompt": followup})
    assert final["status"] == "completed", final
    assert tools.calls[1:] == [(PPO_TOOL, arguments), ("query_market_data", {"input_snapshot_id": recommendation["input_snapshot_id"], "start_date": recommendation["as_of"], "end_date": recommendation["as_of"]})]
    assert "original completed session" in final["answer"]["narrative"]
    assert final["answer"]["claim_check"]["passed"] and not final["answer"]["claim_check"]["removed_figures"]
