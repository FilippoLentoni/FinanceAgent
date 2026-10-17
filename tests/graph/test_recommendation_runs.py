"""Run-log listing route: "latest N recommendations/runs/policies" answered from the run table only."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finplan_agent.graph.claim_check import REMOVED
from finplan_agent.graph.lifecycle import lifecycle_plan
from finplan_agent.graph.runs_query import MAX_LIMIT, RunLogReader, latest_runs, parse_runs_request, render_runs
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS, LIFECYCLE_TOOLS
from tests.fakes.agent import SESSION, FakeToolClient, auth, make_service
from tests.fakes.run_log import PF, PF_B, ClientError, FakeRunTable, method, run_item
from tests.unit.test_saved_portfolio_recommendations import NoProvider

NOW = datetime(2026, 10, 17, 18, 30, tzinfo=timezone.utc)  # 14:30 New York


def service(table=None, *, reader="table"):
    tools = FakeToolClient({"list_portfolio_decisions": lambda _: {"decisions": []}, "get_portfolio_history": lambda _: {"history": []},
                            "list_market_snapshots": lambda _: {"snapshots": []}, "list_agent_activity": lambda _: {"events": [], "next_token": None}},
                           extra_tools=list(LIFECYCLE_TOOLS | CLASSICAL_TOOLS | {"recommend_portfolio"}))
    svc, _ = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry(n, False, "reader") for n in LIFECYCLE_TOOLS | CLASSICAL_TOOLS | {"recommend_portfolio"}], environment="beta")
    if reader == "table":
        svc.deps.run_log = RunLogReader("beta", lambda: table)
    else:
        svc.deps.run_log = reader
    return svc, tools


def ask(svc, prompt):
    return svc.handle({"prompt": prompt, "stream": False}, auth(), runtime_session_id=SESSION)


def history(n=8):
    return [run_item(h) for h in range(9, 9 + n)]


# ------------------------------------------------------------------------------------------- routing
@pytest.mark.parametrize("prompt,limit,methods", [
    ("show me the latest 5 recommendations", 5, None),
    ("Show me the latest five recommendations", 5, None),
    ("last 3 runs", 3, None),
    ("latest ten policies", 10, None),
    ("latest PPO recommendations", 5, ["ppo"]),
    ("What are the last 2 min variance recommendations?", 2, ["min_variance"]),
    ("latest 4 mean-variance and CVaR runs", 4, ["mean_variance", "cvar"]),
    ("latest mean variance and CVaR runs", 5, ["mean_variance", "cvar"]),
    ("What were the last 3 policies?", 3, None),
    ("latest runs for pf_01JABCDEFGHJKMNPQRSTVWXYZ4", 5, None),
    ("most recent traditional recommendations", 5, ["min_variance", "mean_variance", "cvar"]),
    ("latest twenty recommendations", 20, None),
    ("latest 50 runs", MAX_LIMIT, None),
    ("show the recommendation run log", 5, None),
])
def test_listing_phrases_parse(prompt, limit, methods):
    request = parse_runs_request(prompt, now=NOW)
    assert request is not None and request["limit"] == limit and request["methods"] == methods


@pytest.mark.parametrize("prompt,since,until", [
    ("recommendations since 2026-10-15", "2026-10-15T04:00:00Z", None),
    ("show runs since today", "2026-10-17T04:00:00Z", None),
    ("show me today's recommendations", "2026-10-17T04:00:00Z", "2026-10-18T04:00:00Z"),
    ("which recommendations were issued yesterday", "2026-10-16T04:00:00Z", "2026-10-17T04:00:00Z"),
])
def test_date_phrases_use_new_york_days(prompt, since, until):
    request = parse_runs_request(prompt, now=NOW)
    assert request["since"] == since and request["until"] == until and request["limit"] == MAX_LIMIT


@pytest.mark.parametrize("prompt", [
    # Existing lifecycle history, comparison, explanation, action and new-recommendation routes.
    "Retrieve the last three portfolios and market snapshots",
    "Show the latest three recommendations",
    "last 3 recommendations",
    "Show my recommendation history",
    "Show the latest three holdings revisions",
    "Show the last three agent interactions",
    "Compare stored PPO recommendations yesterday and today",
    "Compare PPO and traditional portfolio planning recommendations today",
    "Explain the latest PPO recommendation",
    "Why did the latest recommendations sell VTI?",
    "Accept the latest recommendation",
    "Give me PPO recommendations for today",
    "PPO portfolio recommendation today",
    "What does the policy recommend today?",
    "Evaluate the latest PPO recommendations",
    "Generate new recommendations",
    "Show the last 3 experiment runs",
    "latest crypto recommendations",
    "Explain pd_01JA2B3C4D5E6F7G8H9JKMNPQR",
    "How are my recent recommendations doing?",
    "What returns did the last 5 recommendations produce?",
])
def test_existing_routes_are_not_taken(prompt):
    assert parse_runs_request(prompt, now=NOW) is None


@pytest.mark.parametrize("prompt,expected", [
    ("Retrieve the last three portfolios and market snapshots", ["get_portfolio_history", "list_portfolio_decisions", "list_market_snapshots"]),
    ("Show the latest three recommendations", ["list_portfolio_decisions"]),
    ("Show the last three agent interactions", ["list_agent_activity"]),
    ("Show the latest three holdings revisions", ["get_portfolio_history", "list_portfolio_decisions", "list_market_snapshots"]),
])
def test_history_routes_still_call_their_tools(prompt, expected):
    table = FakeRunTable(history())
    svc, tools = service(table)
    result = ask(svc, prompt)
    assert [name for name, _ in tools.calls] == expected
    assert table.calls == [] and "run log" not in result["answer"]["narrative"]
    plan = lifecycle_plan({"messages": [{"role": "user", "content": [{"text": prompt}]}]}, "s")
    assert [c.name for c in plan["calls"]] == expected


# ------------------------------------------------------------------------------------------- answers
def test_latest_five_is_newest_first_deterministic_and_claim_checked():
    table = FakeRunTable(history(8))
    svc, tools = service(table)
    result = ask(svc, "show me the latest 5 recommendations")
    answer = result["answer"]
    text = answer["narrative"]
    assert result["status"] == "completed" and result["usage"]["invocations"] == 0 and tools.calls == []
    assert table.calls[0]["ScanIndexForward"] is False and table.calls[0]["Limit"] == 5 and table.calls[0]["IndexName"] == "recent"
    runs = [line for line in text.splitlines() if line[:1].isdigit()]
    assert len(runs) == 5
    assert [r.split()[2] for r in runs] == [f"sched-2026-10-17T{h:02d}:00:00Z" for h in (16, 15, 14, 13, 12)]
    assert "started 2026-10-17T16:00:05Z, trigger scheduled, status completed" in text
    assert "completed session 2026-10-16" in text and "formation 2026-10-16T13:00:00Z" in text
    assert "targets VTI 40.00%, BND 31.25%, GLD 10.00%, VEA 8.75% and 1 more" in text
    assert text.count("decision pd_") == 20
    assert "Older run records may exist" in text
    assert "Ask to explain, compare or evaluate a listed id" in text
    assert answer["claim_check"]["passed"] and answer["claim_check"]["removed_figures"] == [] and REMOVED not in text
    assert answer["claim_check"]["checked_figures"] > 20


def test_method_filter_is_applied_after_query_and_pages_until_enough():
    items = [run_item(h, methods={"ppo": method(n=h)} if h % 3 == 0 else {"cvar": method(n=h)}) for h in range(0, 24)]
    table = FakeRunTable(items)
    svc, _ = service(table)
    text = ask(svc, "latest 4 PPO runs")["answer"]["narrative"]
    runs = [line for line in text.splitlines() if line[:1].isdigit()]
    assert [r.split()[2] for r in runs] == [f"sched-2026-10-17T{h:02d}:00:00Z" for h in (21, 18, 15, 12)]
    assert "(cvar)" not in text and "PPO (ppo): issued" in text


def test_paging_bounds_and_more_flag():
    table = FakeRunTable([run_item(h) for h in range(0, 24)])
    page = latest_runs(table, "beta", limit=3, methods=["mean_variance"])
    assert len(page.items) == 3 and page.more and len(table.calls) == 1 and table.calls[0]["Limit"] == 12
    table = FakeRunTable([run_item(h, methods={"ppo": method(n=h)}) for h in range(0, 24)])
    page = latest_runs(table, "beta", limit=3, methods=["cvar"], max_pages=2)
    assert page.items == [] and len(table.calls) == 2
    assert latest_runs(FakeRunTable([run_item(h) for h in range(3)]), "beta", limit=99).more is False


def test_n_is_capped_and_cap_items_and_other_envs_are_excluded():
    items = history(24) + [run_item(1, env="gamma")]
    items.append({"run_id": "cap#2026-10-17", "portfolio_id": "cap", "env": "beta", "started_key": "9999"})
    table = FakeRunTable(items)
    svc, _ = service(table)
    result = ask(svc, "latest 50 runs")
    text = result["answer"]["narrative"]
    assert len([line for line in text.splitlines() if line[:1].isdigit()]) == MAX_LIMIT
    assert "at most 20" in text and "cap#" not in text and result["answer"]["claim_check"]["passed"]


def test_date_filter_uses_key_condition():
    table = FakeRunTable([run_item(10, day=15), run_item(10, day=16), run_item(10, day=17)])
    request = parse_runs_request("which recommendations were issued yesterday", now=NOW)
    page = latest_runs(table, "beta", limit=request["limit"], since=request["since"], until=request["until"])
    assert [i["run_id"] for i in page.items] == ["sched-2026-10-16T10:00:00Z"]
    assert "BETWEEN" in table.calls[0]["KeyConditionExpression"]
    text, _ = render_runs(page.items, request)
    assert "on 2026-10-16" in text


def test_portfolio_and_failed_methods_render_without_invention():
    items = [run_item(12, portfolio=PF_B, status="partial", methods={"ppo": method(status="not_due", error_code="NEWS_NOT_DUE"), "cvar": method(n=3)}),
             run_item(11, portfolio=PF, trigger="on_demand", status="failed", methods={"ppo": method(status="failed", error_code="AUTH_FAILED")})]
    items[1]["error_code"] = "AUTH_FAILED"
    table = FakeRunTable(items)
    svc, _ = service(table)
    result = ask(svc, f"latest runs for {PF_B}")
    text = result["answer"]["narrative"]
    assert PF_B in text and PF + ":" not in text
    assert "PPO (ppo): not_due; error NEWS_NOT_DUE" in text and result["answer"]["claim_check"]["passed"]
    text = ask(svc, "last 2 runs")["answer"]["narrative"]
    assert "trigger on_demand, status failed. Error AUTH_FAILED." in text


def test_empty_log():
    svc, _ = service(FakeRunTable([]))
    result = ask(svc, "show me the latest 5 recommendations")
    assert result["status"] == "completed" and "No recommendation runs were found" in result["answer"]["narrative"]


@pytest.mark.parametrize("reader,code", [
    (None, "NOT_CONFIGURED"),
    (RunLogReader("beta", lambda: None), "NOT_CONFIGURED"),
    (RunLogReader("beta", lambda: FakeRunTable(error=ClientError("AccessDeniedException"))), "AccessDeniedException"),
    (RunLogReader("beta", lambda: (_ for _ in ()).throw(ClientError("ParameterNotFound"))), "ParameterNotFound"),
])
def test_unavailable_log_is_explicit(reader, code):
    svc, tools = service(reader=reader)
    result = ask(svc, "show me the latest 5 recommendations")
    text = result["answer"]["narrative"]
    assert f"run log is unavailable ({code})" in text and "pd_" not in text and tools.calls == []
    assert result["degraded"] == "run_log_unavailable"


def test_ssm_resolver_maps_missing_parameter_to_not_configured():
    from finplan_agent.graph.runs_query import RunLogUnavailable, ssm_table_resolver

    class Ssm:
        def __init__(self, value=None, error=None):
            self.value, self.error, self.names = value, error, []

        def get_parameter(self, Name):
            self.names.append(Name)
            if self.error:
                raise self.error
            return {"Parameter": {"Value": self.value}}

    from finplan_agent.config.settings import SsmNames
    name = SsmNames("beta").recommendation_runs_table_ref
    assert name == "/finplan/beta/financeagent/config/recommendation-runs-table"
    ssm = Ssm("finplan-beta-financeagent-recommendation-runs")
    assert ssm_table_resolver(ssm, name, lambda n: ("table", n))() == ("table", "finplan-beta-financeagent-recommendation-runs") and ssm.names == [name]
    with pytest.raises(RunLogUnavailable) as err:
        ssm_table_resolver(Ssm(error=ClientError("ParameterNotFound")), name, lambda n: n)()
    assert err.value.code == "NOT_CONFIGURED"
    with pytest.raises(RunLogUnavailable) as err:
        ssm_table_resolver(Ssm("bad name; drop"), name, lambda n: n)()
    assert err.value.code == "INVALID_TABLE_SETTING"


def test_follow_up_by_listed_id_uses_existing_explanation_path():
    table = FakeRunTable(history(2))
    svc, tools = service(table)
    text = ask(svc, "latest 1 PPO recommendations")["answer"]["narrative"]
    listed = text.split("decision ")[1].split(";")[0]
    request = parse_runs_request(f"explain {listed}", now=NOW)
    assert request is None
    plan = lifecycle_plan({"messages": [{"role": "user", "content": [{"text": f"explain {listed}"}]}]}, "s")
    assert [c.name for c in plan["calls"]] == ["get_portfolio_decision"] and plan["calls"][0].arguments == {"decision_id": listed}


def test_runtime_reader_resolves_lazily_and_reports_missing_parameter():
    from finplan_agent.config.settings import Settings, load_repo_config
    from finplan_agent.runtime.service import _run_log_reader

    class Ssm:
        names: list = []

        def get_parameter(self, Name):
            self.names.append(Name)
            raise ClientError("ParameterNotFound")

    ssm = Ssm()
    settings = Settings(environment="beta", region="us-east-2", release_id=None, memory_id=None, gateway_url=None, repo_config=load_repo_config("beta"))
    reader = _run_log_reader(settings, ssm)
    assert ssm.names == []  # no startup dependency
    svc, _ = service(reader=reader)
    text = ask(svc, "last 3 runs")["answer"]["narrative"]
    assert "run log is unavailable (NOT_CONFIGURED)" in text
    assert ssm.names == ["/finplan/beta/financeagent/config/recommendation-runs-table"]
