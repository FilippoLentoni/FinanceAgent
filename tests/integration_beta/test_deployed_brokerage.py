"""Authenticated nonproduction operator metadata checks; never any live order."""
import pytest
from finplan_agent.core.ids import new_session_id
from finplan_agent.tools.mcp_client import GatewayMcpClient
from tests.deployed import deployed,requires_deployed

pytestmark=requires_deployed


def test_brokerage_gateway_and_hosted_reader_are_authenticated_view_only():
    env=deployed()
    if env.env!='beta':pytest.skip('brokerage read integration is beta only')
    stack=env.session.client('cloudformation').describe_stacks(StackName='finplan-beta-financeagent-agent')['Stacks'][0]
    outputs={o['OutputKey']:o['OutputValue'] for o in stack['Outputs']}
    gateway=GatewayMcpClient(outputs['BrokerageGatewayUrl'],env.ci_token,forward_identity=True)
    assert [t.name for t in gateway.list_tools()]==['get_etf_connection_status']
    result=gateway.call_tool('get_etf_connection_status',{})
    assert result.ok and result.result['authenticated'] is True,result.error or result.result
    assert result.result['live_execution_enabled'] is False
    assert result.result['order_permission_verified'] is False
    assert result.result['etf_catalog'][0]['ticker']=='SPY'
    status,final=env.invoke_agent({'prompt':'What is my Coinbase brokerage connection status?','stream':False},new_session_id())
    assert status==200 and final['status']=='completed',final
    connection=final['answer']['brokerage_connection_status']
    assert connection['authenticated'] is True and connection['etf_eligibility_verified'] is True,connection
    assert connection['interface']=='agentcore_gateway_advanced_trade_rest'
    assert final['answer']['claim_check']['passed']
    assert not any(field in connection for field in ('holdings','available_balance','privateKey','access_token'))


def test_brokerage_gateway_rejects_invalid_propagated_identity():
    from finplan_agent.tools.mcp_client import urllib_transport
    env=deployed()
    if env.env!='beta':pytest.skip('brokerage read integration is beta only')
    stack=env.session.client('cloudformation').describe_stacks(StackName='finplan-beta-financeagent-agent')['Stacks'][0]
    outputs={o['OutputKey']:o['OutputValue'] for o in stack['Outputs']}
    def forged(url,headers,body,timeout):
        headers={**headers,'X-Finplan-User-Token':'not-a-valid-token'}
        return urllib_transport(url,headers,body,timeout)
    gateway=GatewayMcpClient(outputs['BrokerageGatewayUrl'],env.ci_token,forward_identity=True,transport=forged)
    result=gateway.call_tool('get_etf_connection_status',{})
    assert result.ok and result.result['authenticated'] is False
    assert result.result['live_execution_enabled'] is False
