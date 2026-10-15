from finplan_agent.graph.policy import classify_request
from finplan_agent.graph.brokerage import connection_plan
from tests.fakes.agent import SESSION, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider


def test_connection_setup_is_truthful_and_live_actions_still_refused():
    for prompt in ('What is my Coinbase brokerage connection status?','Show my Coinbase holdings','Is Coinbase ETF setup ready?'):
        assert classify_request(prompt) is None
        plan=connection_plan({'messages':[{'role':'user','content':[{'text':prompt}]}]})
        evidence=plan['tool_results'][0]['result']
        assert not evidence['authenticated'] and evidence['status']=='setup_required'
    for prompt in ('Buy 1 SPY ETF in Coinbase','Create my Coinbase portfolio','Sell my ETF shares','Transfer funds to Coinbase'):
        assert classify_request(prompt) is not None


def test_hosted_connection_status_without_model_or_remote_call():
    service,_=make_service(provider=NoProvider())
    result=service.handle({'prompt':'What is my Coinbase brokerage connection status?','stream':False},auth(),runtime_session_id=SESSION)
    assert result['status']=='completed',result
    assert 'not connected' in result['answer']['narrative']
    assert result['answer']['brokerage_connection_status']['status']=='setup_required'
    assert result['answer']['claim_check']['passed'],result
