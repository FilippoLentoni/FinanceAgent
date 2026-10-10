"""Exercise complete hosted MCP orchestration, approval interrupts and durable evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS, LIFECYCLE_TOOLS, PortfolioMcpClient
from tests.fakes.agent import SESSION, SESSION_B, FakeToolClient, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation

PD = "pd_01JA2B3C4D5E6F7G8H9JKMNPQR"
OLD = "pd_01JA2B3C4D5E6F7G8H9JKMNPRS"
ANALYSIS = "ca_" + "d" * 32


def setup(rec, *, durable=False):
    state = {"revision": 1, "status": "issued"}
    portfolio_id = rec["portfolio_state"]["portfolio_id"]

    def recommendation(_):
        value = deepcopy(rec)
        value["portfolio_state"]["revision"] = state["revision"]
        return {"decision_id": PD, "portfolio_id": portfolio_id, "recommendation": value}

    def decision(_):
        return {"decision": {"decision_id": PD, "portfolio_id": portfolio_id,
                             "portfolio_revision": 1, "status": state["status"],
                             "reference_date": rec["as_of"], "algorithm": "ppo",
                             "input_snapshot_id": rec["input_snapshot_id"],
                             "recommendation": deepcopy(rec)}}

    def resolve(args):
        assert args["confirmed_by_user"] is True
        state["status"] = "accepted" if args["action"] == "accept" else "rejected"
        state["revision"] = 2 if args["action"] == "accept" else 1
        return {"decision_id": PD, "portfolio_id": portfolio_id, "status": state["status"],
                "before_revision": 1, "after_revision": state["revision"], "paper_execution": True}

    results = {"recommend_portfolio": recommendation, "get_portfolio_decision": decision,
               "resolve_portfolio_decision": resolve,
               "record_agent_activity": lambda _: {"activity_event_id": "stored-turn"},
               "list_portfolio_decisions": lambda _: {"decisions": []},
               "get_portfolio_history": lambda _: {"history": []},
               "list_market_snapshots": lambda _: {"snapshots": []}}
    tools = FakeToolClient(results, extra_tools=list(LIFECYCLE_TOOLS | CLASSICAL_TOOLS | {"recommend_portfolio"}))
    svc, _ = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([
        CatalogEntry(n, n in {"resolve_portfolio_decision", "run_portfolio_research"}, "plan-writer" if n == "resolve_portfolio_decision" else "reader")
        for n in LIFECYCLE_TOOLS | CLASSICAL_TOOLS | {"recommend_portfolio"}
    ], environment="beta")
    svc.deps.skills = load_skills()[0]
    svc.deps.durable_activity = durable
    return svc, tools, state


def ask(svc, prompt, session=SESSION):
    return svc.handle({"prompt": prompt, "stream": False}, auth(), runtime_session_id=session)


def confirm(svc, approve=True):
    return svc.handle({"action": "confirm", "approve": approve, "stream": False}, auth(), runtime_session_id=SESSION)


@pytest.mark.parametrize("action,final_revision", [("accept", 2), ("reject", 1)])
def test_exact_issued_plan_is_reviewed_before_human_resolution(recommendation, action, final_revision):
    svc, tools, state = setup(recommendation, durable=True)
    assert ask(svc, "How should I invest today?")["status"] == "completed"
    pending = ask(svc, action.capitalize() + " this recommendation")
    assert pending["status"] == "awaiting_confirmation"
    assert state["revision"] == 1 and state["status"] == "issued"
    review = pending["confirmation"]
    assert review["paper_decision"]["recommendation"] == recommendation
    args = review["calls"][0]["arguments"]
    assert args["decision_id"] == PD and args["expected_revision"] == 1
    assert args["confirmed_by_user"] is False and args["idempotency_key"]
    assert not any(name == "resolve_portfolio_decision" for name, _ in tools.calls)
    done = confirm(svc)
    assert done["status"] == "completed", done
    resolves = [args for name, args in tools.calls if name == "resolve_portfolio_decision"]
    assert len(resolves) == 1 and resolves[0] == {**args, "confirmed_by_user": True}
    assert state["revision"] == final_revision
    assert done["answer"]["claim_check"]["passed"]
    assert done["answer"]["activity_receipt"]["activity_event_id"]
    archive = tools.calls[-1][1]
    assert tools.calls[-1][0] == "record_agent_activity"
    assert archive["decision_id"] == PD and archive["portfolio_id"] == recommendation["portfolio_state"]["portfolio_id"]
    assert archive["payload"]["confirmation"]["decision"] == "approved"
    assert archive["payload"]["skills_used"][0]["name"] == "paper-portfolio-lifecycle"
    assert archive["payload"]["skills_used"][0]["instructions_checksum"].startswith("sha256:")
    # A new session asks the producer for current authoritative state; it does not reuse targets.
    next_day = ask(svc, "What does the policy recommend today?", SESSION_B)
    assert next_day["answer"]["recommendation"]["portfolio_state"]["revision"] == final_revision


def test_explicit_decision_id_accepts_only_after_review_and_declined_confirmation_is_archived(recommendation):
    svc, tools, state = setup(recommendation, durable=True)
    pending = ask(svc, "Accept " + PD)
    assert pending["status"] == "awaiting_confirmation", pending
    done = confirm(svc, False)
    assert done["status"] == "completed" and state["revision"] == 1
    assert not any(name == "resolve_portfolio_decision" for name, _ in tools.calls)
    assert tools.calls[-1][1]["payload"]["confirmation"]["decision"] == "declined"


def test_stale_revision_conflict_is_reported_without_retry_or_claim_of_acceptance(recommendation):
    svc, tools, state = setup(recommendation)
    ask(svc, "Accept " + PD)
    tools.errors["resolve_portfolio_decision"] = {"code": "CONFLICT", "message": "holdings revision changed", "retryable": False}
    done = confirm(svc)
    assert done["status"] == "failed" and done["error"]["code"] == "CONFLICT", done
    assert state["revision"] == 1
    assert len([1 for name, _ in tools.calls if name == "resolve_portfolio_decision"]) == 1
    assert "Paper decision " + PD + ": accepted" not in done["answer"]["narrative"]


def test_latest_three_reads_all_joinable_history_and_archives_completed_turn(recommendation):
    svc, tools, _ = setup(recommendation, durable=True)
    result = ask(svc, "Retrieve the last three portfolios and market snapshots")
    assert [name for name, _ in tools.calls] == ["get_portfolio_history", "list_portfolio_decisions", "list_market_snapshots", "record_agent_activity"]
    assert all(args == {"limit": 3} for _, args in tools.calls[:3])
    assert result["status"] == "completed" and result["usage"]["invocations"] == 0


def test_why_reads_frozen_decision_and_nested_policy_evidence_without_recommending_again(recommendation):
    svc, tools, _ = setup(recommendation)
    tools.results["explain_portfolio_decision"] = lambda _: {
        "analysis_id": ANALYSIS, "analysis_kind": "explanation", "summary": "Frozen policy explanation",
        "source_decision_ref": {"decision_id": PD}, "decision_status": "issued", "recommendation": deepcopy(recommendation),
        "explanation": {"policy_replay": {"status": "verified", "maximum_weight_error": 0.0},
                        "attribution": {"status": "not_available", "reason": "No causal feature attribution"}}}
    ask(svc, "PPO portfolio recommendation today")
    result = ask(svc, "Why should I sell Google?")
    assert tools.calls[-2:] == [("get_portfolio_decision", {"decision_id": PD}), ("explain_portfolio_decision", {"portfolio_id": recommendation["portfolio_state"]["portfolio_id"], "decision_id": PD, "instrument_id": "GOOGL"})]
    assert result["status"] == "completed" and "No causal feature attribution" in result["answer"]["narrative"]
    assert result["answer"]["claim_check"]["passed"]


def test_generic_comparison_preserves_descriptive_changes_without_missing_shapley_failure(recommendation):
    svc, tools, _ = setup(recommendation)
    tools.results["compare_portfolio_decisions"] = lambda _: {
        "analysis_id": ANALYSIS, "analysis_kind": "comparison", "summary": "Frozen decision comparison",
        "previous_decision_ref": {"decision_id": OLD}, "current_decision_ref": {"decision_id": PD},
        "alignment": {"previous_algorithm": "ppo", "current_algorithm": "ppo", "previous_as_of": "2026-10-07", "current_as_of": "2026-10-08"},
        "changes": [{"instrument_id": "GOOGL", "previous_action": "sell", "current_action": "hold", "previous_target_weight": .2, "current_target_weight": .21}],
        "attribution": {"status": "not_available", "reason": "Descriptive changes only"}}
    result = ask(svc, f"Compare {OLD} with {PD}")
    assert [name for name, _ in tools.calls] == ["get_portfolio_decision", "compare_portfolio_decisions"]
    assert tools.calls[-1][1]["previous_decision_id"] == OLD
    assert result["status"] == "completed" and "GOOGL: sell → hold" in result["answer"]["narrative"]
    assert "Descriptive changes only" in result["answer"]["narrative"]


def test_failed_fresh_recommendation_never_resolves_an_older_success(recommendation):
    svc, tools, _ = setup(recommendation)
    ask(svc, "PPO portfolio recommendation today")
    tools.errors["recommend_portfolio"] = {"code": "PRECONDITION_FAILED", "message": "no approved market data"}
    ask(svc, "PPO portfolio recommendation today")
    count = len(tools.calls)
    result = ask(svc, "Why?")
    assert len(tools.calls) == count and "latest recommendation failed" in result["answer"]["narrative"]


def test_completed_turn_fails_explicitly_if_durable_archive_is_unavailable(recommendation):
    svc, tools, _ = setup(recommendation, durable=True)
    tools.errors["record_agent_activity"] = {"code": "DEPENDENCY_UNAVAILABLE", "message": "archive unavailable"}
    result = ask(svc, "How should I invest today?")
    assert result["status"] == "failed" and result["answer"]["recommendation"] is None
    assert result["answer"]["activity_receipt"] is None


def test_shared_lifecycle_tools_can_use_either_independent_gateway():
    results = {"get_portfolio_history": lambda _: {"history": []}}
    for primary_offers in (True, False):
        primary = FakeToolClient(results, extra_tools=["get_portfolio_history"] if primary_offers else [])
        classical = FakeToolClient(results, extra_tools=["get_portfolio_history"])
        client = PortfolioMcpClient(primary, classical)
        result = client.call_tool("get_portfolio_history", {"limit": 3})
        assert result.ok and result.extra["gateway"] == ("primary" if primary_offers else "classical")


@pytest.mark.parametrize('prompt,expected',[('PPO portfolio recommendation today','recommend_portfolio'),('Traditional portfolio recommendation today','recommend_classical_portfolio')])
def test_natural_recommendation_preserves_explicit_saved_book_identity(recommendation,prompt,expected):
    svc,tools,_=setup(recommendation)
    tools.results['recommend_classical_portfolio']=tools.results['recommend_portfolio']
    pid=recommendation['portfolio_state']['portfolio_id']
    result=ask(svc,prompt+' for '+pid)
    assert result['status']=='completed'
    assert tools.calls[0]==(expected,{'portfolio_id':pid,**({'algorithm':'min_variance'} if expected=='recommend_classical_portfolio' else {})})


def test_dated_comparison_discovers_only_compatible_stored_dates(recommendation):
    svc,tools,_=setup(recommendation)
    pid=recommendation['portfolio_state']['portfolio_id']
    tools.results['list_portfolio_decisions']=lambda _:{'decisions':[
        {'decision_id':PD,'portfolio_id':pid,'algorithm':'ppo','recommendation':{'as_of':'2026-10-08'}},
        {'decision_id':'pd_01JA2B3C4D5E6F7G8H9JKMNPRT','portfolio_id':pid,'algorithm':'min_variance','recommendation':{'as_of':'2026-10-07'}},
        {'decision_id':OLD,'portfolio_id':pid,'algorithm':'ppo','recommendation':{'as_of':'2026-10-07'}}]}
    tools.results['compare_portfolio_decisions']=lambda _:{'analysis_id':ANALYSIS,'analysis_kind':'comparison','summary':'Same family comparison','changes':[]}
    result=ask(svc,'Compare stored PPO recommendations yesterday and today')
    assert tools.calls[-1]==('compare_portfolio_decisions',{'portfolio_id':pid,'previous_decision_id':OLD,'current_decision_id':PD})
    assert result['status']=='completed'


def test_performance_for_previous_date_reads_previous_issued_decision_then_dated_context(recommendation):
    svc,tools,_=setup(recommendation)
    pid=recommendation['portfolio_state']['portfolio_id']
    tools.results['list_portfolio_decisions']=lambda _:{'decisions':[
        {'decision_id':PD,'portfolio_id':pid,'algorithm':'ppo','recommendation':{'as_of':'2026-10-08'}},
        {'decision_id':OLD,'portfolio_id':pid,'algorithm':'ppo','recommendation':{'as_of':'2026-10-07'}}]}
    original=tools.results['get_portfolio_decision']
    tools.results['get_portfolio_decision']=lambda args:{'decision':{**original(args)['decision'],'decision_id':args['decision_id']}}
    tools.results['evaluate_portfolio_decision']=lambda _:{'analysis_id':ANALYSIS,'analysis_kind':'performance','summary':'Observed outcome','trend':'not_available','reason':'no_forward_observations'}
    tools.results['research_market_events']=lambda _:{'analysis_id':ANALYSIS,'analysis_kind':'market_events','summary':'News unavailable','sources':[]}
    result=ask(svc,"Why is yesterday's stored PPO recommendation trending red?")
    assert [name for name,_ in tools.calls]==['list_portfolio_decisions','get_portfolio_decision','evaluate_portfolio_decision','research_market_events']
    assert tools.calls[1][1]=={'decision_id':OLD}
    assert tools.calls[2][1]['decision_id']==OLD
    assert result['status']=='completed' and result['usage']['invocations']==0


def test_repeated_hosted_activity_reads_keep_bounded_narratives_and_reconstructable_references(recommendation):
    svc,tools,_=setup(recommendation,durable=True)
    pid=recommendation['portfolio_state']['portfolio_id']
    events=[]
    sizes=[]

    def archive(args):
        event=deepcopy(args)
        event.pop('idempotency_key')
        event.update(activity_event_id='act_'+str(len(events)+1).zfill(26),recorded_at='2026-10-10T10:00:00Z',contract_version='1.5.0')
        event['checksum']='sha256:'+hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()
        sizes.append(len(json.dumps(event)))
        events.append(event)
        return {'activity_event_id':event['activity_event_id'],'checksum':event['checksum']}

    tools.results['record_agent_activity']=archive
    tools.results['list_agent_activity']=lambda _:{'events':deepcopy(list(reversed(events[-3:]))),'next_token':None,'contract_version':'1.5.0'}
    initial=ask(svc,'PPO portfolio recommendation today')
    original=deepcopy(events[0])
    assert original['payload']['narrative']==initial['answer']['narrative']
    assert original['payload']['tool_results'][0]['result']['recommendation']==recommendation
    for _ in range(25):
        result=ask(svc,'Show the last three agent interactions for '+pid)
        assert result['status']=='completed',result
        assert result['answer']['claim_check']['passed'],result['answer']['claim_check']
        assert 'original financial evidence' not in result['answer']['narrative']
        saved=events[-1]['payload']
        assert saved['narrative']==result['answer']['narrative']
        refs=saved['tool_results'][0]['result']
        assert refs['evidence_representation']=='immutable_activity_references'
        assert all('payload' not in row for row in refs['events'])
        originals={event['activity_event_id']:event for event in events[:-1]}
        assert all(row['checksum']==originals[row['activity_event_id']]['checksum'] for row in refs['events'])
        assert all(row['event_kind']=='agent_turn' and row['summary']['status']=='completed' for row in refs['events'])
        assert len(saved['narrative'])<7000 and sizes[-1]<14000
    assert events[0]==original
    assert max(sizes[-10:])-min(sizes[-10:])<200
