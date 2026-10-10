"""Independent MCP routing and evidence-first classical workflows; no AWS or model calls."""
import json
from copy import deepcopy

from finplan_agent.core.errors import AgentError
from finplan_agent.graph.claim_check import claim_check
from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS, PortfolioMcpClient
from tests.fakes.agent import SESSION, FakeToolClient, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation

A = 'ca_' + 'a'*32
B = 'ca_' + 'b'*32
C = 'ca_' + 'c'*32


def doc(rec, aid=A, kind='recommendation'):
    rec = deepcopy(rec)
    rec['strategy'] = 'min_variance'
    rec['settings'] = {'lookback_days':60, 'horizon_sessions':21, 'cash_weight':.795}
    return {'analysis_id':aid, 'analysis_kind':kind, 'created_at':'2026-10-10T09:00:00Z',
            'portfolio_id':rec['portfolio_state']['portfolio_id'], 'summary':'Numerical model evidence',
            'recommendation':rec, 'analysis_ref':{'checksum':'sha256:'+'a'*64},
            'explanation':{'objective_definition':'negative variance minus turnover cost', 'hold_objective':-.03,
                           'optimized_objective':-.02, 'objective_gain':.01, 'objective_units':'variance',
                           'force_keep':[{'instrument_id':'GOOGL','status':'optimal','objective_loss_from_keep':.004}],
                           'binding_constraints':['fixed_cash_weight'],
                           'shapley':{'game':'coalition trade objective', 'coalition_count':32,
                                      'contributions':[{'group':'GOOGL','value':.01}],
                                      'reconciliation_residual':1.23e-17, 'tolerance':1e-8}}}


def service(document, *, errors=None):
    results = {'recommend_classical_portfolio':lambda _:deepcopy(document),
               'get_classical_analysis':lambda _:deepcopy(document),
               'explain_classical_recommendation':lambda _:dict(deepcopy(document), analysis_id=B,analysis_kind='explanation')}
    tools = FakeToolClient(results, errors=errors, extra_tools=list(CLASSICAL_TOOLS)+['recommend_portfolio'])
    svc, _ = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader=lambda:ToolCatalog([CatalogEntry(n,n=='run_portfolio_research','researcher' if n=='run_portfolio_research' else 'reader') for n in CLASSICAL_TOOLS|{'recommend_portfolio'}],environment='beta')
    svc.deps.skills=load_skills()[0]
    return svc,tools


def ask(svc,prompt):
    return svc.handle({'prompt':prompt,'stream':False},auth(),runtime_session_id=SESSION)


def test_classical_recommendation_and_why_retrieve_exact_issued_id(recommendation):
    svc,tools=service(doc(recommendation))
    a=ask(svc,'Give me the traditional portfolio recommendation today')
    b=ask(svc,'Why should I sell Google?')
    assert tools.calls==[('recommend_classical_portfolio',{'algorithm':'min_variance'}),('explain_classical_recommendation',{'analysis_id':A,'instrument_id':'GOOGL'})]
    for result in (a,b):
        assert result['status']=='completed',result
        assert result['answer']['claim_check']['passed'],result['answer']['claim_check']
        assert result['usage']['invocations']==0
        assert result['answer']['portfolio_analyses']
        assert 'GOOGL | sell |' in result['answer']['narrative']
    assert 'modeled objective loss from keeping it: 0.004000' in b['answer']['narrative']
    assert b['answer']['skills_used'][0]['name']=='explain-classical-recommendation'


def test_both_strategies_are_rendered_and_preserved(recommendation):
    svc,tools=service(doc(recommendation))
    tools.results['recommend_portfolio']=lambda _:{'recommendation':deepcopy(recommendation)}
    result=ask(svc,'Compare PPO and traditional portfolio planning recommendations today')
    assert [c[0] for c in tools.calls]==['recommend_portfolio','recommend_classical_portfolio']
    assert result['status']=='completed' and result['answer']['claim_check']['passed'],result
    assert result['answer']['recommendation']['strategy']=='ppo'
    assert result['answer']['portfolio_analyses'][0]['recommendation']['strategy']=='min_variance'
    assert {s['name'] for s in result['answer']['skills_used']}=={'recommend-portfolio','recommend-classical-portfolio'}


def test_two_dates_lookup_then_compare_uses_stored_evidence(recommendation):
    svc,tools=service(doc(recommendation))
    old=doc(recommendation)
    old['recommendation']['as_of']='2026-10-07'
    current=doc(recommendation,B)
    tools.results['list_classical_analyses']=lambda _:{'analyses':[current,old]}
    tools.results['compare_classical_plans']=lambda _:{'analysis_id':C,'analysis_kind':'comparison','summary':'No target change','changes':[], 'alignment':{},'shapley':{'coalition_count':16,'reconciliation_residual':[0.]}}
    result=ask(svc,'Why did the traditional recommendation change since yesterday?')
    assert [c[0] for c in tools.calls]==['list_classical_analyses','compare_classical_plans']
    assert tools.calls[1][1]=={'previous_analysis_id':A,'current_analysis_id':B}
    assert result['status']=='completed' and result['answer']['claim_check']['passed'],result
    assert any(s['name']=='compare-classical-plans' for s in result['answer']['skills_used'])


def test_comparison_narrative_uses_decision_context_and_preserves_raw_implementation(recommendation):
    comparison = {
        'analysis_id': C, 'analysis_kind': 'comparison',
        'summary': 'Exact four-group attribution of target allocation changes between immutable issued plans',
        'previous_analysis_id': A, 'current_analysis_id': B,
        'alignment': {
            'algorithm': 'min_variance', 'previous_as_of': '2026-10-06',
            'current_as_of': '2026-10-07', 'horizon_sessions': 21,
            'instruments': ['GOOGL'],
            'implementation': {'version': 'finplan-classical/1', 'scipy_version': '1.18.1',
                               'solver_source_checksum': 'sha256:' + 'a'*64},
        },
        'changes': [{
            'instrument_id': 'GOOGL', 'previous_action': 'sell', 'current_action': 'hold',
            'previous_target_weight': .19, 'current_target_weight': .2,
            'target_weight_change': .01,
            'attribution': [{'group': 'expected_returns', 'value': 0},
                            {'group': 'risk_inputs', 'value': .01},
                            {'group': 'portfolio_state', 'value': 0},
                            {'group': 'configuration', 'value': 0}],
        }],
        'shapley': {
            'method': 'exact_shapley', 'coalition_count': 16,
            'baseline': [.19], 'final': [.2], 'reconciliation_residual': [0.],
            'tolerance': 1e-8,
        },
        'reproduction': {'status': 'verified'},
        'analysis_ref': {'checksum': 'sha256:' + 'b'*64},
    }
    svc, tools = service(doc(recommendation))
    tools.results['compare_classical_plans'] = lambda _: deepcopy(comparison)
    result = ask(svc, f'Compare these traditional plans and explain why Google changed: {A} then {B}')
    assert tools.calls == [('compare_classical_plans', {'previous_analysis_id': A, 'current_analysis_id': B})]
    assert result['status'] == 'completed'
    assert result['answer']['claim_check']['passed'], result['answer']['claim_check']
    narrative = result['answer']['narrative']
    alignment_text = narrative.split('Comparison alignment: ', 1)[1].split('\n\n', 1)[0]
    assert json.loads(alignment_text) == {
        'algorithm': 'min_variance', 'previous_as_of': '2026-10-06',
        'current_as_of': '2026-10-07', 'horizon_sessions': 21, 'instruments': ['GOOGL'],
    }
    assert 'GOOGL: sell → hold; target weight 0.190000 → 0.200000' in narrative
    assert 'implementation' not in narrative and '1.18.1' not in narrative
    assert result['answer']['portfolio_analyses'] == [comparison]
    assert result['answer']['portfolio_analyses'][0]['alignment']['implementation']['scipy_version'] == '1.18.1'


def test_missing_dated_plans_does_not_invent_yesterday(recommendation):
    svc,tools=service(doc(recommendation))
    tools.results['list_classical_analyses']=lambda _:{'analyses':[]}
    result=ask(svc,'Compare traditional plans yesterday and today')
    assert len(tools.calls)==1 and "will not invent yesterday" in result['answer']['narrative']


def test_performance_deep_dive_fetches_dated_context_after_numeric_report(recommendation):
    svc,tools=service(doc(recommendation))
    tools.results['evaluate_classical_performance']=lambda _:{'analysis_id':B,'analysis_kind':'performance','summary':'No observations','trend':'not_available','actual_source':'saved_paper','reason':'no_forward_observations'}
    tools.results['research_market_events']=lambda _:{'analysis_id':C,'analysis_kind':'market_events','summary':'Dated context unavailable','source_status':'unavailable','sources':[]}
    ask(svc,'Traditional portfolio recommendation today')
    result=ask(svc,'Are we trending green or red, and why?')
    assert tools.calls[-2:]==[('evaluate_classical_performance',{'analysis_id':A}),('research_market_events',{'analysis_id':B})]
    assert 'not_available' in result['answer']['narrative'] and result['usage']['invocations']==0
    assert result['status']=='completed'


def test_failed_new_recommendation_is_never_explained_as_old_one(recommendation):
    svc,tools=service(doc(recommendation))
    ask(svc,'Traditional portfolio recommendation today')
    tools.errors['recommend_classical_portfolio']={'code':'PRECONDITION_FAILED','message':'snapshot missing'}
    failed=ask(svc,'Traditional portfolio recommendation today')
    assert failed['status']=='failed' and failed['answer']['recommendation'] is None
    count=len(tools.calls)
    result=ask(svc,'Why?')
    assert len(tools.calls)==count and 'need an issued traditional' in result['answer']['narrative']


def test_readonly_research_dry_run_needs_no_paid_confirmation(recommendation):
    svc,tools=service(doc(recommendation))
    tools.results['run_portfolio_research']=lambda _:{'analysis_id':B,'analysis_kind':'research_run','summary':'Budget estimate','dry_run':True,'estimated_cost_usd':.05}
    result=svc.handle({'tool_request':{'name':'run_portfolio_research','arguments':{'review_id':A,'dry_run':True}},'stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='completed' and tools.calls==[('run_portfolio_research',{'review_id':A,'dry_run':True})]
    paid=svc.handle({'tool_request':{'name':'run_portfolio_research','arguments':{'review_id':A,'dry_run':False}},'stream':False},auth(),runtime_session_id=SESSION)
    assert paid['status']=='awaiting_confirmation' and len(tools.calls)==1


def test_gateway_families_route_without_cross_family_fallback():
    primary=FakeToolClient({'recommend_portfolio':lambda _:{'ppo':True}},extra_tools=['recommend_portfolio','recommend_classical_portfolio'])
    classical=FakeToolClient({'recommend_classical_portfolio':lambda _:{'classical':True}},extra_tools=['recommend_classical_portfolio'])
    client=PortfolioMcpClient(primary,classical)
    assert client.call_tool('recommend_portfolio',{}).extra['gateway']=='primary'
    assert client.call_tool('recommend_classical_portfolio',{}).extra['gateway']=='classical'
    assert primary.calls==[('recommend_portfolio',{})]
    class Down(FakeToolClient):
        def list_tools(self): raise AgentError.dependency('unavailable')
    partial=PortfolioMcpClient(primary,Down())
    assert not partial.call_tool('recommend_classical_portfolio',{}).ok
    assert partial.call_tool('recommend_portfolio',{}).ok


def test_scientific_attribution_figures_are_checked_as_complete_numbers():
    assert claim_check('residual 1.23e-17; tolerance 1e-08',[{'residual':1.23e-17,'tolerance':1e-8}]).passed
    assert not claim_check('residual 2.23e-17',[{'residual':1.23e-17}]).passed


def test_hosted_prompt_uses_packaged_skills_once_without_changing_remote_discovery():
    from finplan_agent.providers.base import ToolSpec
    from finplan_agent.skills import classical_mcp_description, provider_tool_specs
    remote=classical_mcp_description('recommend_classical_portfolio','Traditional recommendation')
    assert 'instructions_checksum' in remote and 'SKILL' not in remote
    offered=ToolSpec(name='recommend_classical_portfolio',description=remote,input_schema={'type':'object'})
    assert provider_tool_specs([offered])[0].description=='Traditional recommendation'
    assert offered.description==remote


def test_dated_source_titles_preserve_figures_only_when_quoted_verbatim():
    source='Portfolio Optimization Review 2024'
    assert claim_check(source,[{'title':source}]).passed
    assert not claim_check('Expected portfolio return 2024',[{'title':source}]).passed


def test_natural_research_estimate_then_explicit_run_requires_confirmation(recommendation):
    svc,tools=service(doc(recommendation))
    tools.results['research_portfolio_models']=lambda _:{'analysis_id':A,'analysis_kind':'research','summary':'Research proposal'}
    tools.results['run_portfolio_research']=lambda _:{'analysis_id':B,'analysis_kind':'research_run','review_id':A,'summary':'Estimate','dry_run':True}
    assert ask(svc,'Review traditional portfolio research')['status']=='completed'
    assert ask(svc,'Run the sandbox experiment')['status']=='completed'
    assert tools.calls[-1]==('run_portfolio_research',{'review_id':A,'dry_run':True})
    count=len(tools.calls)
    pending=ask(svc,'Run the sandbox experiment')
    assert pending['status']=='awaiting_confirmation' and len(tools.calls)==count
    args=pending['confirmation']['calls'][0]['arguments']
    assert args['dry_run'] is False and args['confirmed_by_user'] is True and args['idempotency_key']
