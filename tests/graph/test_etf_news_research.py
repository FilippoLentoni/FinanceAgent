import pytest
from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS
from finplan_agent.graph.etf_news import etf_news_plan, TOOL
from tests.fakes.agent import SESSION, FakeToolClient, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider


def evidence(computed=False):
    comparison={'status':'ineligible','reason':'baseline_analysis_id_required'}
    if computed:
        comparison={'status':'computed','price_only':{'weights':[.45,.45],'cash_weight':.1},'news_enriched':{'weights':[.5,.4],'cash_weight':.1}}
    return {'decision_at':'2026-10-15T02:00:00Z','symbols':['SPY','VOO'],'feature_protocol':'etf-search-metadata/1',
            'features':{s:{'sentiment':.2,'event_count':2.,'uncertainty':.1,'age_hours':.5,'missing':0.} for s in ('SPY','VOO')},
            'contract_version':'1.8.0','provenance':[], 'models':{'optimization':{'eligible':True,'comparison':comparison},
                'ppo':{'eligible':False,'reason':'no_compatible_trained_etf_news_policy_or_historical_corpus'},
                'llm':{'research_context_eligible':True,'trading_policy_eligible':False}},'limitations':['accuracy_improvement_not_measured']}


@pytest.mark.parametrize('computed',[False,True])
def test_etf_news_hosted_evidence_no_fallback(computed):
    tools=FakeToolClient({TOOL:lambda _:evidence(computed)},extra_tools=[TOOL])
    service,_=make_service(tools=tools,provider=NoProvider())
    service.deps.catalog_loader=lambda:ToolCatalog([CatalogEntry(TOOL,False,'reader')],environment='beta')
    service.deps.skills,service.deps.stable_instructions=load_skills()
    result=service.handle({'prompt':'Show Coinbase ETF model inputs for SPY and VOO','stream':False},auth(),runtime_session_id=SESSION)
    assert tools.calls==[(TOOL,{'symbols':['SPY','VOO']})]
    assert result['status']=='completed',result
    assert result['answer']['claim_check']['passed'],result
    assert 'no accuracy improvement' in result['answer']['narrative']
    assert any(s['name']=='etf-news-research' for s in result['answer']['skills_used'])
    assert result['answer']['etf_research_inputs']==[evidence(computed)]
    assert TOOL in CLASSICAL_TOOLS
    if computed:assert 'news_enriched' in result['answer']['narrative']
    else:assert 'baseline_analysis_id_required' in result['answer']['narrative']


def test_explicit_baseline_and_decision_are_forwarded():
    baseline='ca_'+'a'*32
    plan=etf_news_plan({'messages':[{'role':'user','content':[{'text':'Compare ETF news features for SPY and VOO at 2026-10-15T02:00:00Z baseline '+baseline}]}]},SESSION)
    assert plan['calls'][0].arguments=={'symbols':['SPY','VOO'],'baseline_analysis_id':baseline,'decision_at':'2026-10-15T02:00:00Z'}
    assert etf_news_plan({'messages':[{'role':'user','content':[{'text':'What is my Coinbase brokerage connection status?'}]}]},SESSION) is None


def test_missing_etf_tool_does_not_substitute_crypto_or_equity():
    service,tools=make_service(provider=NoProvider())
    result=service.handle({'prompt':'Show ETF news state features','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='failed',result
    assert tools.calls==[]


def test_malformed_response_is_not_narrated_or_exposed():
    tools=FakeToolClient({TOOL:lambda _:{'features':{}}},extra_tools=[TOOL])
    service,_=make_service(tools=tools,provider=NoProvider())
    service.deps.catalog_loader=lambda:ToolCatalog([CatalogEntry(TOOL,False,'reader')],environment='beta')
    result=service.handle({'prompt':'ETF news inputs','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='failed'
    assert result['answer']['etf_research_inputs']==[]
    assert 'does not validate' in result['answer']['narrative']


def test_structured_tool_request_keeps_etf_renderer():
    args={'symbols':['SPY'],'input_snapshot_id':'approved-snapshot'}
    state={'messages':[{'role':'user','content':[{'text':'Run the tool get_etf_research_inputs.'},{'tool_request':{'name':TOOL,'arguments':args}}]}]}
    plan=etf_news_plan(state,SESSION)
    assert plan['workflow']['kind']=='etf_news'
    assert plan['calls'][0].arguments==args
