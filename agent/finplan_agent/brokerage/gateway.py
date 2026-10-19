"""Separate fixed read-only Gateway; never merges brokerage into the planning catalog."""
from ..tools.mcp_client import GatewayMcpClient


class BrokerageUnavailable(Exception):
    """The read-only brokerage status could not be established; ``reason`` names why."""

    def __init__(self, message, *, reason, code=None, status=None):
        super().__init__(message)
        self.reason, self.code, self.status = reason, code, status


class BrokerageToolMissing(BrokerageUnavailable, ValueError):
    pass


class BrokerageNotAuthenticated(BrokerageUnavailable, PermissionError):
    pass


def _reason(result):
    if not result.ok:
        err = result.error or {}
        details = err.get('details') or {}
        return str(details.get('reason') or details.get('stage') or err.get('code') or 'tool_error'), err.get('code')
    doc = result.result if isinstance(result.result, dict) else {}
    return str(doc.get('reason') or doc.get('stage') or doc.get('failure_stage') or ('not_authenticated' if doc.get('authenticated') is not True else 'unknown')), None


class BrokerageGatewayReader:
    def __init__(self,url,token_provider,*,transport=None):
        self.client=GatewayMcpClient(url,token_provider,forward_identity=True,transport=transport)

    def capabilities(self):
        tools=self.client.list_tools()
        if not any(t.name=='get_etf_connection_status' for t in tools):
            raise BrokerageToolMissing('read-only brokerage status tool is unavailable', reason='tool_not_offered')
        result=self.client.call_tool('get_etf_connection_status',{})
        if not result.ok or not isinstance(result.result,dict) or result.result.get('authenticated') is not True:
            reason, code = _reason(result)
            status = result.result.get('status') if result.ok and isinstance(result.result, dict) else None
            raise BrokerageNotAuthenticated('owner-authenticated brokerage status is unavailable', reason=reason, code=code, status=status)
        return result.result
