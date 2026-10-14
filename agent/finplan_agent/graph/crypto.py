"""Deterministic crypto paper workflow, distinct from the saved equity recommendation."""
import re
from ..providers.base import ToolCall
from .recommendations import text_of

TOOLS = {"get_crypto_experiment", "recommend_crypto_portfolio", "list_crypto_history", "discover_research_data"}


def crypto_plan(state, session_id):
    messages=state.get("messages", [])
    user=next((m for m in reversed(messages) if m.get("role")=="user" and any('text' in b for b in m.get('content',[]))),{})
    text=text_of(user)
    if not re.search(r"\b(?:crypto|bitcoin|ethereum|btc|eth)\b",text,re.I):return None
    if any(r.get("tool") in TOOLS for r in state.get("tool_results",[])):
        return {"workflow":{"kind":"crypto_paper","mode":"crypto_paper"},"calls":[]}
    name="get_crypto_experiment";args={}
    if re.search(r"\b(?:news|discover|additional data|research data|x402)\b",text,re.I):
        name="discover_research_data";args={"query":text[:2000]}
    elif re.search(r"\b(?:history|evolution|over time)\b",text,re.I):name="list_crypto_history"
    elif re.search(r"\b(?:recommend|allocation|ppo|llm|cvar|variance)\b",text,re.I):
        name="recommend_crypto_portfolio"
        args={"algorithm":next((a for a in ("ppo","llm","cvar") if re.search(r"\b"+a+r"\b",text,re.I)),"mean_variance" if re.search(r"mean.?variance",text,re.I) else "min_variance")}
    return {"workflow":{"kind":"crypto_paper","mode":"crypto_paper"},"calls":[ToolCall(id=f"crypto-{state.get('turn',1)}",name=name,arguments=args)]}


def render_crypto(state):
    results=state.get('tool_results') or []
    if not results:return 'No crypto experiment evidence was retrieved.'
    result=results[-1]
    if not result.get('ok'):
        return 'Crypto research evidence is unavailable: '+str((result.get('error') or {}).get('message','dependency unavailable'))
    doc=result.get('result') or {}
    if 'experiment' in doc:
        e=doc['experiment']
        lines=[f"Synthetic crypto experiment as of {e['as_of']}; initial capital ${e['initial_capital_usd']:.2f} per independent portfolio."]
        for name,b in e['books'].items():
            lines.append(f"{name}: value ${b['value']:.4f}; net return {b['net_return']*100:.4f}%; maximum drawdown {b['max_drawdown']*100:.4f}%; cumulative fees ${b['cumulative_fees']:.4f}.")
        lines.extend(f"{name}: unavailable — {m['reason']}." for name,m in e['models'].items() if not m['eligible'])
        lines.append('These are forward paper observations with modeled costs, not live investment results or evidence of future profitability. Supplemental data is currently free-market-data baseline only.')
        return '\n\n'.join(lines)
    if 'recommendation' in doc:
        r=doc['recommendation']
        if not r.get('eligible'):return f"{r['algorithm']} is unavailable for hourly crypto: {r['reason']}. No substitute policy or live order was used."
        allocation=', '.join(f"{p}: {w*100:.4f}%" for p,w in r['weights'].items())
        return f"Synthetic {r['algorithm']} recommendation as of {r['as_of']}: {allocation}; cash {r['cash_weight']*100:.4f}%. {r['model_assumptions']}. This proposes paper targets; fills require a subsequent observed open."
    if 'history' in doc:
        return '\n\n'.join(f"{row['as_of']}: "+'; '.join(f"{name} ${b['value']:.4f}" for name,b in row['books'].items()) for row in doc['history']) or 'No crypto paper history is available yet.'
    if 'discovery' in doc:
        d=doc['discovery']
        return 'Research-data discovery completed. '+str(d.get('note',''))+'\n\n'+str(d.get('catalog_summary','No verified provider summary is available.'))
    return doc.get('summary','No crypto experiment evidence is available.')
