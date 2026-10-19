"""Deterministic ETF news-input research; no claim that features improve returns."""
import re
from ..providers.base import ToolCall
from .recommendations import text_of

TOOL='get_etf_research_inputs'
SYMBOLS=('SPY','VOO','VTI','QQQ','IWM','DIA','AGG','BND','TLT','GLD','VEA','VWO','GOOGL')


def etf_news_request(text):
    has_etf = re.search(r'\b(?:etfs?|google|alphabet)\b',text,re.I) or any(re.search(r'\b'+s+r'\b',text,re.I) for s in SYMBOLS)
    return bool(has_etf and re.search(r'\b(?:news|information|state|features?|inputs?|models?|ppo|llm|optimization|optimizer|enrich\w*|research|recommend\w*)\b',text,re.I))


def _session_has_recommendation(state):
    from ..tools.portfolio import RECOMMENDATION_TOOLS
    return any(b.get('tool_result',{}).get('name') in RECOMMENDATION_TOOLS for m in state.get('messages',[]) for b in m.get('content',[]) if isinstance(b,dict))


def etf_news_plan(state, session_id):
    user=next((m for m in reversed(state.get('messages',[])) if m.get('role')=='user' and any('text' in b for b in m.get('content',[]))),{})
    text=text_of(user)
    explicit=next((b['tool_request'] for b in user.get('content',[]) if isinstance(b.get('tool_request'),dict) and b['tool_request'].get('name')==TOOL),None)
    if explicit is None and any(isinstance(b.get('tool_request'),dict) for b in user.get('content',[])):
        return None  # an explicit request for another tool runs exactly that tool
    # An identified paper-book recommendation must use its actual holdings/profile,
    # not the standalone feature/sensitivity tool's illustrative equal weights.
    if explicit is None and re.search(r'\bpf_[A-Z0-9]{26}\b',text) and re.search(r'\brecommend\w*\b',text,re.I):
        return None
    # A recommendation that names a model or an instrument list runs that model tool
    # (generate_portfolio_recommendation_*), unless it asks about news/feature inputs.
    # A why/explain follow-up about a recommendation in this session explains that recommendation.
    if explicit is None and re.search(r'\b(?:why|explain\w*|reason\w*|rationale)\b',text,re.I) and _session_has_recommendation(state):
        return None
    if explicit is None and not re.search(r'\b(?:news|information|features?|inputs?|enrich\w*|research)\b',text,re.I):
        from .model_tools import requested_instruments, requested_model
        if requested_model(text) or len(requested_instruments(text))>=2:
            return None
    symbols=[s for s in SYMBOLS if re.search(r'\b'+s+r'\b',text,re.I)]
    if 'GOOGL' not in symbols and re.search(r'\b(?:google|alphabet)\b',text,re.I):symbols.append('GOOGL')
    if explicit is None and not etf_news_request(text):
        return None
    workflow={'kind':'etf_news','mode':'etf_news'}
    if any(r.get('tool')==TOOL for r in state.get('tool_results',[])):
        return {'workflow':workflow,'calls':[]}
    if explicit is not None:
        return {'workflow':workflow,'calls':[ToolCall(id=f'etf-news-{state.get("turn",1)}',name=TOOL,arguments=dict(explicit.get('arguments') or {}))]}
    args={'symbols':symbols or ['SPY','VOO']}
    baseline=re.search(r'\bca_[0-9a-f]{32}\b',text)
    if baseline:args['baseline_analysis_id']=baseline.group()
    snapshot=re.search(r'\binput_snapshot_id\s*[:=]\s*["\']?([A-Za-z0-9_-]+)',text)
    if snapshot and not baseline:args['input_snapshot_id']=snapshot.group(1)
    timestamp=re.search(r'\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b',text)
    if timestamp:args['decision_at']=timestamp.group()
    if re.search(r'\bcvar\b',text,re.I):args['algorithm']='cvar'
    elif re.search(r'\bmin(?:imum)?[ -]?variance\b',text,re.I):args['algorithm']='min_variance'
    elif re.search(r'\bmean[ -]?variance\b',text,re.I):args['algorithm']='mean_variance'
    return {'workflow':workflow,'calls':[ToolCall(id=f'etf-news-{state.get("turn",1)}',name=TOOL,arguments=args)]}


def render_etf_news(state):
    row=next((r for r in reversed(state.get('tool_results',[])) if r.get('tool')==TOOL),{})
    if not row.get('ok'):
        return 'ETF research inputs are unavailable: '+str((row.get('error') or {}).get('message','required evidence was not retrieved'))+'. No alternative crypto or equity policy was substituted.'
    doc=row.get('result') or {}
    lines=['ETF news research inputs as of '+str(doc.get('decision_at','unavailable'))+'. Protocol: '+str(doc.get('feature_protocol','unavailable'))+'.']
    for symbol in doc.get('symbols',[]):
        feature=doc.get('features',{}).get(symbol,{})
        if feature.get('missing',1):
            lines.append(symbol+': eligible news features are missing; absence is not neutral sentiment.')
        else:
            lines.append(symbol+': sentiment '+str(feature.get('sentiment'))+'; event count '+str(feature.get('event_count'))+'; uncertainty '+str(feature.get('uncertainty'))+'; age in hours '+str(feature.get('age_hours'))+'.')
    models=doc.get('models',{});comparison=models.get('optimization',{}).get('comparison',{})
    if comparison.get('status')=='computed':
        lines.append('Matched optimizer sensitivity was computed under declared sentiment and uncertainty assumptions. This measures changed allocations, not improved accuracy or returns.')
        for variant in ('price_only','news_enriched'):
            solution=comparison.get(variant,{})
            allocations=', '.join(symbol+' weight '+str(weight) for symbol,weight in zip(comparison.get('instruments', doc.get('symbols',[])),solution.get('weights',[])))
            lines.append(variant+': '+allocations+'; cash weight '+str(solution.get('cash_weight'))+'.')
    else:
        lines.append('Matched optimizer comparison is unavailable: '+str(comparison.get('reason','no matched baseline evidence'))+'.')
    ppo=models.get('ppo',{})
    lines.append('Crypto policies are not substituted. ETF PPO eligibility: '+str(ppo.get('eligible',False))+'. '+str(ppo.get('reason','no verified compatible policy'))+'.')
    llm=models.get('llm',{})
    lines.append('LLM research context eligible: '+str(llm.get('research_context_eligible',False))+'; trading policy eligible: '+str(llm.get('trading_policy_eligible',False))+'.')
    lines.append('Source provenance is retained in the research evidence. Historical coverage is limited to collection availability; no accuracy improvement or investment result has been established. This research does not place orders or promote a production policy.')
    return '\n\n'.join(lines)
