from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import CatalogEntry,ToolCatalog
from tests.fakes.agent import SESSION,FakeToolClient,auth,make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider
from finplan_agent.graph.crypto import TOOLS


def test_hosted_graph_crypto_recommendation_is_not_equity_inference():
    response={'analysis_id':'ca_'+'a'*32,'analysis_kind':'crypto_recommendation','recommendation':{'algorithm':'min_variance','eligible':True,'as_of':'2026-10-14T04:00:00Z','weights':{'BTC-USD':.45,'ETH-USD':.45},'cash_weight':.1,'model_assumptions':'Historical hourly returns; synthetic targets only.'}}
    tools=FakeToolClient({'recommend_crypto_portfolio':lambda _:response},extra_tools=list(TOOLS))
    service,_=make_service(tools=tools,provider=NoProvider())
    service.deps.catalog_loader=lambda:ToolCatalog([CatalogEntry(t,False,'reader') for t in TOOLS],environment='beta')
    service.deps.skills,service.deps.stable_instructions=load_skills()
    result=service.handle({'prompt':'Recommend a synthetic crypto portfolio','stream':False},auth(),runtime_session_id=SESSION)
    assert tools.calls==[('recommend_crypto_portfolio',{'algorithm':'min_variance'})]
    assert result['status']=='completed',result
    assert '45.0000%' in result['answer']['narrative']
    assert result['answer']['claim_check']['passed'],result
    assert result['answer']['portfolio_analyses'][0]['analysis_id']==response['analysis_id']
    assert any(s['name']=='crypto-paper-research' for s in result['answer']['skills_used'])
