"""Saved-book recommendations must invoke MCP and preserve complete producer evidence."""
from copy import deepcopy

import pytest

from finplan_agent.graph.claim_check import claim_check
from finplan_agent.graph.nodes import narrate
from finplan_agent.graph.recommendations import recommendation_arguments, render_recommendation, target_cash_value
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from tests.fakes.agent import SESSION, FakeToolClient, auth, make_service


@pytest.fixture
def recommendation():
    instruments = ['AAPL', 'GOOGL', 'NFLX', 'NVDA', 'VOO']
    prices = [200., 250., 100., 100., 500.]
    targets = [.04, .065, .02, .045, .035]
    return {
        'mode':'advisory_paper', 'strategy':'ppo', 'policy_source_run_id':'run_01JABCDEFGHJKMNPQRSTVWXY01',
        'export_run_id':'run_01JABCDEFGHJKMNPQRSTVWXY02', 'configuration_id':'cfg_'+'a'*64,
        'input_snapshot_id':'snap_01JABCDEFGHJKMNPQRSTVWXY03', 'as_of':'2026-10-08',
        'snapshot_checksum':'sha256:'+'b'*64, 'policy_artifact_checksum':'sha256:'+'c'*64,
        'policy_seeds':[0,1,2,3,4], 'aggregation':'mean_target_weights',
        'decision_timing':'after_completed_close_for_next_session',
        'forecast':{'status':'not_available','reason':'No calibrated forecast'}, 'limitations':['research_only','no_trade_execution'],
        'target_weights':[{'instrument_id':i,'weight':w} for i,w in zip(instruments,targets,strict=True)], 'cash_weight':.795,
        'decisions':[{'instrument_id':i,'action':'sell','delta_weight':w-.2,'indicative_notional':(w-.2)*10000,
                      'current_quantity':2000/p,'target_quantity':w*10000/p,'delta_quantity':(w-.2)*10000/p,
                      'reference_price':p,'price_as_of':'2026-10-08'} for i,p,w in zip(instruments,prices,targets,strict=True)],
        'estimated_turnover':.795,'constraint_outcome':{'action':'accepted','violations':[]},'solution_status':'feasible','diagnostics':{},
        'portfolio_state':{'source':'saved_paper','portfolio_id':'pf_01KDVDNAZ83BAMMYCEGWF33DPM','revision':1,
                           'as_of':'2026-10-08','valuation_as_of':'2026-10-08','portfolio_value':10000.,'high_watermark':10000.,'current_cash':0.,'base_currency':'USD'}
    }


class NoProvider:
    kind = 'fixture'
    model_id = None
    def generate(self, *args, **kwargs):
        raise AssertionError('A default recommendation must use MCP, without an invented provider answer')


def service(rec, *, errors=None, provider=None):
    tools = FakeToolClient({'recommend_portfolio':lambda _: {'recommendation':deepcopy(rec),'synthetic':True}}, errors=errors, extra_tools=['recommend_portfolio'])
    svc, _ = make_service(tools=tools, provider=provider or NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry('recommend_portfolio',False,'reader')],environment='beta')
    return svc, tools


@pytest.mark.parametrize('prompt', [
    'How should I invest today?', 'What does the policy recommend today?',
    'Give me a portfolio planning recommendation', 'Should I buy more Google or sell?',
    'Should I buy Nvidia stocks today?', 'Recommend a portfolio allocation',
])
def test_default_question_calls_mcp_once_and_reports_shares_and_cash(recommendation, prompt):
    svc, tools = service(recommendation)
    result = svc.handle({'prompt':prompt,'stream':False},auth(),runtime_session_id=SESSION)
    assert result['status'] == 'completed'
    assert tools.calls == [('recommend_portfolio',{})]
    assert result['answer']['recommendation'] == recommendation
    text = result['answer']['narrative']
    assert 'Saved paper portfolio' in text and 'GOOGL | sell | 8.0000 | 2.6000 | -5.4000 | -1,350.00 | 6.50%' in text
    assert 'target 7,950.00 USD / 79.50%' in text
    for i in ['AAPL','GOOGL','NFLX','NVDA','VOO']:
        assert '| '+i+' |' in text
    assert 'No calibrated return forecast' in text and 'not executions' in text and '<thinking>' not in text
    assert result['answer']['claim_check']['passed'], result['answer']['claim_check']
    assert result['usage']['tool_calls'] == 1


def test_repeated_question_reads_again_and_never_treats_targets_as_executions(recommendation):
    svc, tools = service(recommendation)
    a=svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    b=svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    assert tools.calls == [('recommend_portfolio',{}),('recommend_portfolio',{})]
    assert a['answer']['recommendation'] == b['answer']['recommendation'] == recommendation
    assert '8.0000 | 2.6000 | -5.4000' in b['answer']['narrative']


def test_explicit_request_reaches_tool_unchanged(recommendation):
    svc, tools = service(recommendation)
    args={'input_snapshot_id':recommendation['input_snapshot_id'],'as_of':'2026-10-08',
          'holdings':{'weights':[{'instrument_id':'GOOGL','weight':1.0}], 'cash_weight':0.,'portfolio_value':12345.,'high_watermark':15000.}}
    result=svc.handle({'recommendation':args,'stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='completed' and tools.calls==[('recommend_portfolio',args)]


@pytest.mark.parametrize('text', [
    'How should I invest my $5,000 actual portfolio?',
    'Should I buy Google? I hold 10 shares.',
    'Recommend a portfolio with 20% GOOGL and 80% cash',
    'Recommend investments for my actual portfolio',
])
def test_explicit_actual_state_is_not_replaced_by_default(text):
    assert recommendation_arguments([{'role':'user','content':[{'text':text}]}]) is None


def test_failed_inference_is_not_retried_or_reported_as_a_recommendation(recommendation):
    svc,tools=service(recommendation,errors={'recommend_portfolio':{'code':'PRECONDITION_FAILED','message':'no saved portfolio','retryable':False}})
    result=svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    assert tools.calls==[('recommend_portfolio',{})]
    assert result['answer']['recommendation'] is None and 'PRECONDITION_FAILED' in result['answer']['narrative']
    assert 'sell |' not in result['answer']['narrative']


def test_provider_false_draft_cannot_override_complete_recommendation(recommendation, monkeypatch):
    from types import SimpleNamespace

    import finplan_agent.graph.nodes as nodes
    monkeypatch.setattr(nodes,'_emit',lambda *_:None)
    state={'draft_text':'<thinking>secret</thinking>The entire portfolio is invested because Google earnings will rise.',
           'tool_results':[{'tool':'recommend_portfolio','ok':True,'result':{'recommendation':recommendation}}]}
    result=narrate(state,SimpleNamespace(context=SimpleNamespace(provider=NoProvider())))
    assert 'Cash:' in result['narrative'] and '79.50%' in result['narrative']
    assert '<thinking>' not in result['narrative'] and 'earnings will rise' not in result['narrative'] and 'entire portfolio is invested' not in result['narrative']


def test_supplied_response_is_labeled_and_does_not_claim_saved_holdings(recommendation):
    rec=deepcopy(recommendation)
    rec['portfolio_state']['source']='supplied'
    text=render_recommendation(rec)
    assert 'Supplied portfolio state' in text and 'has not been saved as holdings' in text


def test_cash_is_verified_deterministic_arithmetic(recommendation):
    text=render_recommendation(recommendation)
    assert target_cash_value(recommendation)==7950.
    assert claim_check(text,[recommendation,{'computed_target_cash':target_cash_value(recommendation)}]).passed


def test_provider_cannot_substitute_saved_book_for_explicit_actual_state(recommendation):
    from finplan_agent.providers.base import GenerateResult, ToolCall, Usage
    class WrongDefaultProvider:
        kind='fixture'
        model_id=None
        def generate(self,request,**kwargs):
            return GenerateResult(text='',tool_calls=(ToolCall(id='wrong',name='recommend_portfolio',arguments={}),),usage=Usage(),stop_reason='tool_use',provider_kind='fixture',model_id=None)
    svc,tools=service(recommendation,provider=WrongDefaultProvider())
    result=svc.handle({'prompt':'Should I buy Google? I hold 10 shares.','stream':False},auth(),runtime_session_id=SESSION)
    assert not tools.calls and result['answer']['recommendation'] is None
    assert 'complete current holdings' in result['answer']['narrative']


def explanation_service(rec):
    from finplan_agent.skills import load_skills
    svc, tools = service(rec)
    tools.results['query_market_data'] = lambda _: {'snapshot':{'input_snapshot_id':rec['input_snapshot_id'],'status':'approved'},'partial':False}
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry('recommend_portfolio',False,'reader'),CatalogEntry('query_market_data',False,'reader')],environment='beta')
    svc.deps.skills = load_skills()[0]
    return svc, tools


@pytest.mark.parametrize('prompt',['Why is this your recommendation?','Explain the recommendation you just gave me.','Why?'])
def test_plain_followup_replays_original_context_and_reads_market_evidence(recommendation,prompt):
    svc,tools=explanation_service(recommendation)
    first=svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    second=svc.handle({'prompt':prompt,'stream':False},auth(),runtime_session_id=SESSION)
    assert tools.calls==[('recommend_portfolio',{}),('recommend_portfolio',{'portfolio_id':recommendation['portfolio_state']['portfolio_id'],'input_snapshot_id':recommendation['input_snapshot_id'],'as_of':recommendation['as_of']}),('query_market_data',{'input_snapshot_id':recommendation['input_snapshot_id'],'start_date':recommendation['as_of'],'end_date':recommendation['as_of']})]
    assert second['status']=='completed' and second['usage']['tool_calls']==2
    assert second['usage']['invocations']==0 and second['answer']['recommendation']==first['answer']['recommendation']
    assert 'original completed session' in second['answer']['narrative'] and 'Feature-level attribution' in second['answer']['narrative']
    assert second['answer']['claim_check']['passed'] and not second['answer']['claim_check']['removed_figures']
    assert second['answer']['skills_used'][0]['name']=='recommend-portfolio'
    assert second['answer']['skills_used'][0]['version']=='0.2.0'


def test_explanation_retains_explicit_original_holdings(recommendation):
    rec=deepcopy(recommendation)
    rec['portfolio_state']['source']='supplied'
    svc,tools=explanation_service(rec)
    args={'input_snapshot_id':rec['input_snapshot_id'],'as_of':rec['as_of'],'holdings':{'weights':[{'instrument_id':'GOOGL','weight':1.0}],'cash_weight':0.,'portfolio_value':10000.,'high_watermark':10000.}}
    svc.handle({'recommendation':args,'stream':False},auth(),runtime_session_id=SESSION)
    result=svc.handle({'prompt':'Why is this your recommendation?','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='completed' and tools.calls[1]==('recommend_portfolio',args)


@pytest.mark.parametrize('change',[('portfolio_state','revision',2),('policy_artifact_checksum',None,'sha256:'+'d'*64)])
def test_changed_state_or_policy_is_not_explained_as_the_original(recommendation,change):
    svc,tools=explanation_service(recommendation)
    svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    changed=deepcopy(recommendation)
    key,nested,value=change
    if nested:
        changed[key][nested]=value
    else:
        changed[key]=value
    tools.results['recommend_portfolio']=lambda _: {'recommendation':changed}
    result=svc.handle({'prompt':'Why is this your recommendation?','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='failed' and result['error']['details']['reason']=='recommendation_replay_mismatch'
    assert result['answer']['recommendation'] is None and 'cannot present the new result' in result['answer']['narrative']


def test_missing_market_evidence_does_not_fall_back_to_conversation(recommendation):
    svc,tools=explanation_service(recommendation)
    svc.handle({'prompt':'How should I invest today?','stream':False},auth(),runtime_session_id=SESSION)
    tools.errors['query_market_data']={'code':'NOT_FOUND','message':'snapshot unavailable','retryable':False}
    result=svc.handle({'prompt':'Why is this your recommendation?','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='failed' and result['error']['code']=='NOT_FOUND'
    assert result['answer']['recommendation'] is None
    assert len(tools.calls)==3 and result['usage']['invocations']==0


def test_why_without_prior_tool_evidence_requests_context(recommendation):
    svc,tools=explanation_service(recommendation)
    result=svc.handle({'prompt':'Why is this your recommendation?','stream':False},auth(),runtime_session_id=SESSION)
    assert not tools.calls and result['usage']['invocations']==0
    assert 'need a successful portfolio recommendation' in result['answer']['narrative']


def test_remote_tool_description_exports_exact_hosted_skill():
    from pathlib import Path

    from finplan_agent.skills import load_skills
    from infra.stacks.tool_schemas import tool_definition
    root=Path(__file__).resolve().parents[2]
    skill=next(s for s in load_skills(root/'skills')[0] if s['name']=='recommend-portfolio')
    desc=tool_definition('recommend_portfolio').description
    assert (root/'skills/recommend-portfolio/SKILL.md').read_text() in desc
    assert skill['instructions_checksum'] in desc and 'recommend-portfolio@0.2.0' in desc
