"""Deterministic issued-decision review, paper approval and historical MCP orchestration."""
from __future__ import annotations

import json
import re

from ..providers.base import ToolCall, text_of
from ..tools.portfolio import LIFECYCLE_TOOLS

_PD = re.compile(r"\bpd_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_PF = re.compile(r"\bpf_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_WHY = re.compile(r"\b(?:why|explain|rationale|reasoning)\b", re.I)
_COMPARE = re.compile(r"\b(?:compare|comparison|yesterday|changed|changes|previous day)\b", re.I)
_PERFORMANCE = re.compile(r"\b(?:green|red|performance|actuals?|realized|realised|discrepancy|trending|below.*expect|return gap|postmortem|post.mortem)\b", re.I)
_ACCEPT = re.compile(r"\b(?:accept|approve|apply)\b.{0,60}\b(?:recommendation|decision|plan|paper|proposal|it|this|that)\b|^(?:accept|approve|apply)[.!\s]*$", re.I)
_REJECT = re.compile(r"\b(?:reject|decline)\b.{0,60}\b(?:recommendation|decision|plan|proposal|it|this|that)\b|^(?:reject|decline)[.!\s]*$", re.I)
_HISTORY = re.compile(r"\b(?:history|latest three|last three|last 3|previous portfolios|holdings revisions)\b", re.I)


def _call(name, arguments, turn, index=0):
    return ToolCall(id=f"lifecycle-{turn}-{index}", name=name, arguments=arguments)


def _decision(row):
    return row.get("decision", row) if isinstance(row, dict) else {}


def _revision(row):
    row = _decision(row)
    rec = row.get("recommendation", {})
    return row.get("portfolio_revision", row.get("source_revision", rec.get("portfolio_state", {}).get("revision")))


def _issued(messages):
    out = []
    for message in messages:
        for block in message.get("content", []):
            result = block.get("tool_result", {})
            doc = result.get("content")
            if result.get("status") == "success" and isinstance(doc, dict) and doc.get("decision_id") and result.get("name") in ("recommend_portfolio", "recommend_classical_portfolio"):
                out.append(doc)
    return out


def _latest_attempt(messages):
    """Never resolve a failed fresh recommendation to an older successful one."""
    for message in reversed(messages):
        for block in reversed(message.get("content", [])):
            result = block.get("tool_result", {})
            if result.get("name") in ("recommend_portfolio", "recommend_classical_portfolio"):
                return result
    return None


def _strategy(row):
    return row.get("strategy") or row.get("algorithm") or row.get("recommendation", {}).get("strategy")


def _date(row):
    return row.get("reference_date") or row.get("recommendation", {}).get("as_of")


def _run(workflow, results, turn):
    mode = workflow["mode"]
    if any(not r.get("ok") for r in results):
        return {"workflow": workflow, "calls": []}
    if mode == "lifecycle_compare_read" and results:
        doc = _decision(results[-1].get("result", {}))
        return {"workflow":{"mode":"lifecycle_complete"}, "calls":[_call("compare_portfolio_decisions",{"portfolio_id":doc["portfolio_id"],"previous_decision_id":workflow["previous_decision_id"],"current_decision_id":workflow["current_decision_id"]},turn,1)]}
    if mode == "lifecycle_lookup":
        if not results:
            return {"workflow": workflow, "calls": []}
        rows = (results[-1].get("result") or {}).get("decisions", [])
        family = workflow.get("family")
        if family:
            rows = [row for row in rows if _strategy(row) in family]
        action = workflow["intent"]
        if action == "compare":
            current = rows[0] if rows else None
            previous = next((row for row in rows[1:] if _date(row) and _date(row) != _date(current) and _strategy(row) == _strategy(current)), None) if current else None
            if previous:
                return {"workflow": {"mode": "lifecycle_complete"}, "calls": [_call("compare_portfolio_decisions", {"portfolio_id":current["portfolio_id"], "previous_decision_id":previous["decision_id"], "current_decision_id":current["decision_id"]}, turn, 1)]}
            return {"workflow": workflow, "calls": [], "clarification": "I could not find two issued decisions from distinct market dates. I will not invent yesterday's recommendation; provide both decision IDs or generate the missing dated decision."}
        if not rows:
            return {"workflow": workflow, "calls": [], "clarification": "No stored recommendation was found. Ask for a PPO or traditional recommendation first."}
        selected = rows[0]
        if workflow.get("previous_date"):
            selected = next((row for row in rows[1:] if _date(row) and _date(row) != _date(rows[0])), None)
            if selected is None:
                return {"workflow": workflow, "calls": [], "clarification": "No issued recommendation from a previous market date was found. I will not substitute today's recommendation for yesterday's."}
        workflow = {**workflow, "mode":"lifecycle_read", "decision_id":selected["decision_id"]}
        return {"workflow":workflow,"calls":[_call("get_portfolio_decision", {"decision_id":workflow["decision_id"]}, turn, 1)]}
    if mode == "lifecycle_read":
        saved = next((r["result"] for r in reversed(results) if r.get("tool") == "get_portfolio_decision" and r.get("ok")), None)
        if not saved:
            return {"workflow":workflow,"calls":[]}
        doc = _decision(saved)
        args = {"portfolio_id":doc["portfolio_id"],"decision_id":doc["decision_id"]}
        action = workflow["intent"]
        if action in ("accept", "reject"):
            rev = _revision(doc)
            if not isinstance(rev,int):
                return {"workflow":workflow,"calls":[],"clarification":"The stored recommendation has no authoritative holdings revision and cannot be applied. Generate a new saved-paper recommendation."}
            args.update(action=action, expected_revision=rev, confirmed_by_user=False)
            name = "resolve_portfolio_decision"
        else:
            name = "evaluate_portfolio_decision" if action == "performance" else "explain_portfolio_decision"
            if workflow.get("instrument_id") and action == "why":
                args["instrument_id"] = workflow["instrument_id"]
        return {"workflow":{**workflow,"mode":"lifecycle_performance" if action=="performance" and workflow.get("deep_dive") else "lifecycle_complete"},"calls":[_call(name,args,turn,2)]}
    if mode == "lifecycle_performance" and not any(r.get("tool")=="research_market_events" for r in results):
        report = next((r["result"] for r in reversed(results) if r.get("tool")=="evaluate_portfolio_decision" and r.get("ok")), {})
        if report.get("analysis_id"):
            return {"workflow":{**workflow,"mode":"lifecycle_complete"},"calls":[_call("research_market_events",{"analysis_id":report["analysis_id"]},turn,3)]}
    return {"workflow":workflow,"calls":[]}


def lifecycle_plan(state, session_id):
    messages = state.get("messages", [])
    user = next((m for m in reversed(messages) if m.get("role")=="user" and any("text" in b for b in m.get("content",[]))), {})
    text = text_of(user)
    turn = state.get("turn",1)
    workflow = state.get("portfolio_workflow")
    if workflow:
        return _run(workflow,state.get("tool_results") or [],turn) if workflow["mode"].startswith("lifecycle_") else None
    for block in user.get("content",[]):
        request = block.get("tool_request") or {}
        if request.get("name") in LIFECYCLE_TOOLS:
            return {"workflow":{"mode":"lifecycle_complete"},"calls":[_call(request["name"],dict(request.get("arguments") or {}),turn)]}
        if request:
            return None
    ids = _PD.findall(text)
    portfolios = _PF.findall(text)
    portfolio = {"portfolio_id":portfolios[0]} if portfolios else {}
    if _HISTORY.search(text):
        if re.search(r"\b(?:interaction|conversation|activity|tool calls?|audit)\b",text,re.I):
            calls=[_call("list_agent_activity",{**portfolio,"limit":3},turn)]
        elif re.search(r"\b(?:recommendations?|decisions?)\b",text,re.I) and not re.search(r"\b(?:holdings|portfolios|snapshots)\b",text,re.I):
            calls=[_call("list_portfolio_decisions",{**portfolio,"limit":3},turn)]
        else:
            calls=[_call("get_portfolio_history",{**portfolio,"limit":3},turn),_call("list_portfolio_decisions",{**portfolio,"limit":3},turn,1),_call("list_market_snapshots",{"limit":3},turn,2)]
        return {"workflow":{"mode":"lifecycle_complete"},"calls":calls}
    if not ids and re.search(r"\b(?:traditional|classical|optimizer|optimization)\b",text,re.I) and re.search(r"\b(?:ppo|reinforcement|rl)\b",text,re.I):
        return None
    rejection = _REJECT.search(text) or ids and re.search(r"\b(?:reject|decline)\b", text, re.I)
    acceptance = _ACCEPT.search(text) or ids and re.search(r"\b(?:accept|approve|apply)\b", text, re.I)
    intent = "reject" if rejection else "accept" if acceptance else "performance" if _PERFORMANCE.search(text) else "compare" if _COMPARE.search(text) else "why" if _WHY.search(text) else None
    issued = _issued(messages)
    if portfolios:
        issued = [row for row in issued if (row.get("portfolio_id") or row.get("recommendation", {}).get("portfolio_state", {}).get("portfolio_id")) == portfolios[0]]
    latest = issued[-1] if issued else None
    last_attempt = _latest_attempt(messages)
    if not ids and issued and intent in ("accept", "reject", "why") and last_attempt and last_attempt.get("status") != "success":
        return {"workflow": {"mode": "lifecycle_complete"}, "calls": [], "clarification": "The latest recommendation failed. Generate a successful recommendation or provide an earlier stored decision ID explicitly."}
    if not intent and ids:
        return {"workflow":{"mode":"lifecycle_complete"},"calls":[_call("get_portfolio_decision",{"decision_id":ids[0]},turn)]}
    if not intent:
        return None
    # Keep legacy ca_* and pre-1.5 conversations on their original workflows.
    if not ids and not latest and intent in ("why","compare","performance"):
        if not re.search(r"\b(?:ppo|reinforcement|decision|stored|historical|postmortem|post.mortem)\b",text,re.I):
            return None
    if intent=="compare" and len(ids)>=2:
        # Get authoritative portfolio linkage before the model comparison.
        if latest:
            portfolio={"portfolio_id":latest.get("portfolio_id",latest.get("recommendation",{}).get("portfolio_state",{}).get("portfolio_id"))}
        if portfolio.get("portfolio_id"):
            return {"workflow":{"mode":"lifecycle_complete"},"calls":[_call("compare_portfolio_decisions",{**portfolio,"previous_decision_id":ids[0],"current_decision_id":ids[1]},turn)]}
        return {"workflow":{"mode":"lifecycle_compare_read","previous_decision_id":ids[0],"current_decision_id":ids[1]},"calls":[_call("get_portfolio_decision",{"decision_id":ids[0]},turn)]}
    family = ["ppo", "sac"] if re.search(r"\b(?:ppo|reinforcement|rl)\b", text, re.I) else ["min_variance", "mean_variance", "cvar"] if re.search(r"\b(?:traditional|classical|optimizer|optimization)\b", text, re.I) else None
    if family and latest and _strategy(latest) not in family:
        latest = next((row for row in reversed(issued) if _strategy(row) in family), None)
    previous_date = intent != "compare" and bool(re.search(r"\b(?:yesterday|previous day)\b", text, re.I))
    did = ids[0] if ids else latest.get("decision_id") if latest and not previous_date and intent != "compare" else None
    workflow={"mode":"lifecycle_read" if did else "lifecycle_lookup","intent":intent,"deep_dive":bool(_WHY.search(text) or re.search(r"deep dive|news|world",text,re.I)), "family":family,"previous_date":previous_date}
    for instrument,pattern in {"GOOGL":r"google|googl","NVDA":r"nvidia|nvda","AAPL":r"apple|aapl","NFLX":r"netflix|nflx","VOO":r"voo"}.items():
        if re.search(r"\b(?:"+pattern+r")\b",text,re.I):
            workflow["instrument_id"]=instrument
            break
    if did:
        return {"workflow":workflow,"calls":[_call("get_portfolio_decision",{"decision_id":did},turn)]}
    return {"workflow":workflow,"calls":[_call("list_portfolio_decisions",{**portfolio,"limit":30},turn)]}


def render_lifecycle(doc):
    if doc.get("decision"):
        row=doc["decision"]
        return "Stored decision " + row["decision_id"] + ":\n```json\n" + json.dumps(row,indent=2,sort_keys=True) + "\n```"
    if doc.get("status") in ("accepted","rejected") and doc.get("decision_id"):
        text=f"Paper decision {doc['decision_id']}: {doc['status']}. Holdings revision {doc['before_revision']} → {doc['after_revision']}."
        if doc["status"]=="accepted":
            text+=" Simulated fills use the stored recommendation's reference prices; this records a paper portfolio, not a broker execution. Tomorrow's recommendation reads the updated saved revision."
        else:
            text+=" Saved holdings are unchanged."
        return text+"\n\n```json\n"+json.dumps(doc,indent=2,sort_keys=True)+"\n```"
    return "Stored historical evidence:\n```json\n"+json.dumps(doc,indent=2,sort_keys=True)+"\n```"
