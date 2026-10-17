"""Separate fixed read-only Gateway; never merges brokerage into the planning catalog."""
from ..tools.mcp_client import GatewayMcpClient


class BrokerageGatewayReader:
    def __init__(self,url,token_provider,*,transport=None):
        self.client=GatewayMcpClient(url,token_provider,forward_identity=True,transport=transport)

    def capabilities(self):
        tools=self.client.list_tools()
        if not any(t.name=='get_etf_connection_status' for t in tools):
            raise ValueError('read-only brokerage status tool is unavailable')
        result=self.client.call_tool('get_etf_connection_status',{})
        if not result.ok or not isinstance(result.result,dict) or result.result.get('authenticated') is not True:
            raise PermissionError('owner-authenticated brokerage status is unavailable')
        return result.result
