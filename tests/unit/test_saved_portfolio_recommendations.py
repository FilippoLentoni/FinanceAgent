"""Saved-book recommendations must invoke MCP and preserve complete producer evidence."""
from copy import deepcopy

import pytest

from finplan_agent.graph.claim_check import claim_check
from finplan_agent.graph.nodes import narrate
from finplan_agent.graph.recommendations import recommendation_arguments, render_recommendation, target_cash_value
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from tests.fakes.agent import FakeToolClient, SESSION, auth, make_service


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
        'target_weights':[{'instrument_id':i,'weight':w} for i,w in zip(instruments,targets)], 'cash_weight':.795,
        'decisions':[{'instrument_id':i,'action':'sell','delta_weight':w-.2,'indicative_notional':(w-.2)*10000,
                      'current_quantity':2000/p,'target_quantity':w*10000/p,'delta_quantity':(w-.2)*10000/p,
                      'reference_price':p,'price_as_of':'2026-10-08'} for i,p,w in zip(instruments,prices,targets)],
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
    for i in ['AAPL','GOOGL','NFLX','NVDA','VOO']: assert '| '+i+' |' in text
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
    rec=deepcopy(recommendation);rec['portfolio_state']['source']='supplied'
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
