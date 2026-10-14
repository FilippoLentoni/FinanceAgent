from finplan_agent.graph.crypto import crypto_plan
from finplan_agent.graph.policy import classify_request


def state(text):return {'messages':[{'role':'user','content':[{'text':text}]}],'turn':1}


def test_crypto_uses_separate_tools_and_stops_after_result():
    s=state('How is the synthetic crypto portfolio performing?')
    assert crypto_plan(s,'s')['calls'][0].name=='get_crypto_experiment'
    s['tool_results']=[{'tool':'get_crypto_experiment'}]
    assert crypto_plan(s,'s')['calls']==[]
    assert crypto_plan(state('recommend an equity portfolio'),'s') is None


def test_crypto_eligibility_history_discovery_and_live_denial():
    assert crypto_plan(state('Recommend crypto with PPO'),'s')['calls'][0].arguments=={'algorithm':'ppo'}
    assert crypto_plan(state('crypto portfolio evolution'),'s')['calls'][0].name=='list_crypto_history'
    assert crypto_plan(state('discover crypto news'),'s')['calls'][0].name=='discover_research_data'
    assert classify_request('Buy $100 of bitcoin in my Coinbase account')['kind']=='live_financial_action'


def test_coinbase_simulation_is_a_paper_read_but_live_orders_remain_denied():
    prompt='Simulate a $100 Coinbase portfolio'
    assert classify_request(prompt) is None
    assert crypto_plan(state(prompt),'s')['calls'][0].name=='get_crypto_experiment'
    assert classify_request('Please buy $100 of bitcoin in my Coinbase account')['kind']=='live_financial_action'
    assert classify_request('Simulate a portfolio then execute a live order')['kind']=='live_financial_action'
