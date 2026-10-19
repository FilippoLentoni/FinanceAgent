"""Readiness conversation; no order payload is inferred from conversation text."""
import re
from ..brokerage.coinbase import readiness
from .recommendations import text_of


def connection_request(text):
    return bool(re.search(r'\b(?:coinbase|brokerage)\b',text,re.I) and
                re.search(r'\b(?:connect\w*|status|ready|readiness|setup|set up|capabilit\w*|holdings|account|portfolio)\b',text,re.I) and
                not re.search(r'\b(?:buy|sell|submit|execute|cancel|transfer|fund|create|recommend\w*|allocation|synthetic|paper|simulat\w*|news|research)\b|^(?:please\s+)?connect\b',text,re.I))


def connection_plan(state, client=None):
    user=next((m for m in reversed(state.get('messages',[])) if m.get('role')=='user' and any('text' in b for b in m.get('content',[]))),{})
    if not connection_request(text_of(user)):
        return None
    try:
        result=readiness(client)
    except Exception as exc:  # noqa: BLE001 - the typed reason is reported, never a stack trace
        reason=getattr(exc,'reason',None) or type(exc).__name__
        result={'status':'unavailable','authenticated':False,'live_execution_enabled':False,'reason':str(reason)[:120],
                **({'error_code':exc.code} if getattr(exc,'code',None) else {}),
                'message':'Coinbase brokerage authentication or capability discovery failed (reason: '+str(reason)[:120]+'). Reconnect the independently authorized brokerage client; only the brokerage owner can read its status. No account holdings were inferred.'}
    return {'portfolio_workflow':{'kind':'etf_connection','mode':'read_only'},'draft_text':result['message'],
            'tool_results':[{'id':'etf-connection-readiness','tool':'get_etf_connection_status','ok':True,'gateway':'brokerage','result':result,'summary':{'status':result['status']},'error':None}],
            'pending_calls':[]}
