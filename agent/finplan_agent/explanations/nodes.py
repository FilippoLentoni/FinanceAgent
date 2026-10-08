"""Explanation subflow nodes of the LangGraph graph (design E1-E7; task 1.2):

``route -> explain_prepare -> [explain_confirm] -> explain_collect -> explain_narrate -> respond``

* ``explain_prepare``: validate the request, read the anchoring records, build the inventory, decide
  the evidence jobs, dry-run them and apply the budget gate (no job is created here);
* ``explain_confirm``: the phase 1 confirmation interrupt showing the exact ``submit_experiment`` calls
  with their estimates, or the exact plan-tool call of adopt-alternative with its concessions;
* ``explain_collect``: submit the confirmed jobs, read their status (never waiting: a running job ends
  the turn ``in_progress`` with its ``run_id``), fetch the evidence, run the deterministic checks
  (a failure returns ``VALIDATION_FAILED`` and blocks narration);
* ``explain_narrate``: ONE capped provider invocation over the compact evidence summary, preceded by
  the phase 1 budget check (``bedrock_explanations``); the explanation claim check; result assembly.

All tool calls go through :class:`~finplan_agent.explanations.tools.GuardedTools`, i.e. the caller's
own Gateway client. State keeps compact evidence documents and identifiers only, never tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from ..core.errors import AgentError
from ..graph.policy import SYSTEM_PROMPT
from ..graph.state import AgentContext, AgentState
from ..providers.base import GenerateRequest, text_of
from .claims import check_explanation_narrative
from .config import ExplanationSettings
from .evidence import EvidenceItem, evidence_from_job_result
from .result import assemble
from .tools import EvidenceJobs, GuardedTools, JobSpec
from .workflows import Analysis, Prepared, analyze, prepare, validate_request

__all__ = [
    "EXPLANATION_OUTPUT_CONTRACT",
    "after_explain_collect",
    "after_explain_confirm",
    "after_explain_prepare",
    "explain_collect",
    "explain_confirm",
    "explain_narrate",
    "explain_prepare",
]

#: Stable narration instructions (part of the cacheable prefix, after the system prompt and skills).
EXPLANATION_OUTPUT_CONTRACT = (
    "Explanation output contract. You receive evidence items, each with a citation key (ev1, ev2, ...), "
    "labels and deterministic statements computed by tools. Write a short narrative (at most 8 sentences) "
    "using ONLY figures that appear in those statements, and end every sentence that states a figure with "
    "the citation key of its evidence item in square brackets, for example [ev1]. Never add, subtract, "
    "average or round figures yourself. Describe re-solve, attribution and sweep results as modeled effects "
    "under the stated model and inputs, never as real-world causes. Report no_effect, infeasible, "
    "requires_retraining and not_available items as they are. Do not claim a trend unless the statements "
    "show it; never interpolate between sweep points. Present concessions as options for the user to decide; "
    "never recommend changing stored risk preferences or constraints."
)


def _emit(event: dict[str, Any]) -> None:
    get_stream_writer()(event)


def _settings(ctx: AgentContext) -> ExplanationSettings:
    s = getattr(ctx, "explanations", None)
    return s if isinstance(s, ExplanationSettings) else ExplanationSettings()


def _cid(state: AgentState) -> str:
    return state.get("correlation_id") or "corr-unknown-000"


def _item_state(i: EvidenceItem) -> dict[str, Any]:
    return {"key": i.key, "kind": i.kind, "ref": i.ref, "payload": i.payload, "source_tool": i.source_tool, "run_id": i.run_id, "labels": list(i.labels), "statements": list(i.statements)}


def _item_from(doc: Mapping[str, Any]) -> EvidenceItem:
    return EvidenceItem(**{k: doc[k] for k in ("key", "kind", "ref", "payload", "source_tool", "run_id", "labels", "statements")})


def _prep_state(p: Prepared) -> dict[str, Any]:
    return {
        "explanation_type": p.explanation_type,
        "subject": p.subject,
        "request_ids": p.request_ids,
        "jobs": [j.to_state() for j in p.jobs],
        "sync_evidence": [_item_state(i) for i in p.sync_evidence],
        "labels": list(p.labels),
        "context": p.context,
        "plan_call": p.plan_call,
        "findings": p.findings,
    }


def _prep_from(doc: Mapping[str, Any]) -> Prepared:
    return Prepared(
        explanation_type=doc["explanation_type"],
        subject=dict(doc["subject"]),
        request_ids=dict(doc["request_ids"]),
        jobs=[JobSpec.from_state(j) for j in doc.get("jobs") or []],
        sync_evidence=[_item_from(i) for i in doc.get("sync_evidence") or []],
        labels=list(doc.get("labels") or []),
        context=dict(doc.get("context") or {}),
        plan_call=doc.get("plan_call"),
        findings=dict(doc.get("findings") or {}),
    )


def _fail(state: AgentState, exc: AgentError, *, status: str = "failed", narrative: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {"error": exc.to_envelope(_cid(state)), "status": status, "narrative": narrative, "narrative_status": "not_needed" if not narrative else "generated", "explanation": {"stage": "done", **(extra or {})}}
    return out


def _evidence_unavailable(state: AgentState, exc: AgentError) -> dict[str, Any]:
    """A Gateway policy denial of an evidence job (FA-EV-10): no job exists, the caller is told why."""
    msg = "Evidence is unavailable for this caller: the Gateway policy does not allow submitting evidence jobs for your groups (FORBIDDEN). No job was created. Existing evidence can still be read by run_id."
    return _fail(state, exc, status="completed", narrative=msg, extra={"evidence_unavailable": True})


# ============================================================================ prepare
def explain_prepare(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    settings = _settings(ctx)
    _emit({"type": "progress", "stage": "explain_prepare"})
    if not settings.enabled:
        return _fail(state, AgentError.not_permitted("explanations are not enabled in this environment", kind="explanations_disabled", environment=ctx.environment))
    tools = GuardedTools(ctx.tools, ctx.catalog, emit=_emit)
    used = int(state.get("turn_tool_calls", 0))
    try:
        req = validate_request(state.get("explanation_request"), settings)
        prep = prepare(req, tools, settings, session_id=ctx.session_id, turn=int(state.get("turn", 1)))
    except AgentError as exc:
        return {**_fail(state, exc), "turn_tool_calls": used + tools.count}
    work: dict[str, Any] = {"request": req, "prepared": _prep_state(prep)}
    if prep.plan_call is not None:
        work.update(stage="confirm", confirm_kind="adopt_alternative", calls=[prep.plan_call])
        return {"explanation": work, "turn_tool_calls": used + tools.count}
    jobs = EvidenceJobs(tools, settings, session_id=ctx.session_id, turn=int(state.get("turn", 1)))
    if any(not j.run_id for j in prep.jobs):
        try:
            gate = jobs.estimate_and_gate(prep.jobs)
        except AgentError as exc:
            if exc.code == "FORBIDDEN":
                return {**_evidence_unavailable(state, exc), "turn_tool_calls": used + tools.count}
            return {**_fail(state, exc), "turn_tool_calls": used + tools.count}
        calls = jobs.submission_calls(prep.jobs)
        work["prepared"] = _prep_state(prep)
        work.update(stage="confirm", confirm_kind="evidence_jobs", calls=calls, gate=gate)
    else:
        work["stage"] = "collect"
    return {"explanation": work, "turn_tool_calls": used + tools.count}


def after_explain_prepare(state: AgentState) -> str:
    stage = (state.get("explanation") or {}).get("stage")
    return {"confirm": "explain_confirm", "collect": "explain_collect"}.get(stage or "", "respond")


# ============================================================================ confirm
def explain_confirm(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    work = dict(state.get("explanation") or {})
    prep = work.get("prepared") or {}
    calls = [{"tool": c["tool"], "arguments": c["arguments"], "idempotency_key": c["idempotency_key"]} for c in work.get("calls") or []]
    detail: dict[str, Any] = {"kind": work.get("confirm_kind"), "explanation_type": prep.get("explanation_type")}
    if work.get("confirm_kind") == "evidence_jobs":
        detail["estimates"] = (work.get("gate") or {}).get("estimates", [])
        detail["budget"] = {k: v for k, v in (work.get("gate") or {}).items() if k != "estimates"}
    else:
        detail["classification"] = (prep.get("findings") or {}).get("classification")
        detail["concessions"] = (prep.get("findings") or {}).get("concessions", [])
        detail["policy_unchanged"] = True
    answer = interrupt({"type": "confirmation_required", "calls": calls, "explanation": detail})
    approved = isinstance(answer, dict) and answer.get("approve") is True
    if approved:
        work.update(stage="collect", approved_keys=[c["idempotency_key"] for c in calls])
        return {"explanation": work, "confirmation": {"decision": "approved", "calls": calls}}
    work["stage"] = "done"
    what = "No evidence job was submitted" if work.get("confirm_kind") == "evidence_jobs" else "No plan version was created and the stored policy is unchanged"
    return {
        "explanation": work,
        "confirmation": {"decision": "declined", "calls": calls},
        "declined": [{"turn": state.get("turn"), "calls": calls}],
        "narrative": f"Declined. {what}.",
        "narrative_status": "generated",
    }


def after_explain_confirm(state: AgentState) -> str:
    return "explain_collect" if (state.get("explanation") or {}).get("stage") == "collect" else "respond"


# ============================================================================ collect
def explain_collect(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    settings = _settings(ctx)
    _emit({"type": "progress", "stage": "explain_collect"})
    work = dict(state.get("explanation") or {})
    prep = _prep_from(work["prepared"])
    tools = GuardedTools(ctx.tools, ctx.catalog, approved_keys=work.get("approved_keys") or [], emit=_emit)
    used = int(state.get("turn_tool_calls", 0))

    def done(update: dict[str, Any]) -> dict[str, Any]:
        return {**update, "turn_tool_calls": used + tools.count}

    if prep.plan_call is not None:
        call = prep.plan_call
        try:
            res = tools.read(call["tool"], call["arguments"]) or {}
        except AgentError as exc:
            return done(_fail(state, exc))
        new_pv = res.get("plan_version_id") or (res.get("plan_version") or {}).get("plan_version_id")
        sweep = prep.sync_evidence[0]
        sweep.statements.append(f"Sweep point {prep.request_ids['point_index']} of run {sweep.run_id} was adopted as override version {new_pv} after explicit confirmation [{sweep.key}].")
        cls = prep.findings.get("classification")
        text = (
            f"Created override version {new_pv} as a child of {prep.subject['plan_version_id']} from sweep point {prep.request_ids['point_index']} (classified {cls}) [{sweep.key}]. The stored constraints and risk preferences are unchanged."
        )
        result = assemble(
            explanation_type="adopt_alternative",
            subject=prep.subject,
            request_ids={**prep.request_ids, "created_plan_version_id": new_pv},
            items=[sweep],
            checks=[],
            labels=["concession"] if cls == "concession" else [],
            narrative=text,
            narrative_status="generated",
            citations=[sweep.key],
            findings=prep.findings,
        )
        work["stage"] = "done"
        return done({"explanation": work, "explanation_result": result, "narrative": text, "narrative_status": "generated"})

    jobs = EvidenceJobs(tools, settings, session_id=ctx.session_id, turn=int(state.get("turn", 1)))
    try:
        jobs.submit(prep.jobs)
    except AgentError as exc:
        if exc.code == "FORBIDDEN":
            return done(_evidence_unavailable(state, exc))
        return done(_fail(state, exc))
    work["prepared"] = _prep_state(prep)
    try:
        results, running = jobs.collect([j for j in prep.jobs if j.run_id])
    except AgentError as exc:
        return done(_fail(state, exc, extra={"run_ids": {j.evidence_kind: j.run_id for j in prep.jobs if j.run_id}}))
    if running:
        run_ids = {r["evidence_kind"]: r["run_id"] for r in running}
        done_ids = {j.evidence_kind: j.run_id for j in prep.jobs if j.run_id}
        resume = {**(work.get("request") or {}), "evidence_run_ids": done_ids}
        work["stage"] = "done"
        text = "The evidence jobs are still running: " + ", ".join(f"{k} {v}" for k, v in sorted(run_ids.items())) + ". Ask again with these run_ids to receive the explanation; no new job will be submitted."
        return done(
            {"explanation": work, "in_progress": {"run_id": running[0]["run_id"], "state": running[0]["state"], "tool": "submit_experiment", "runs": running, "resume_request": resume}, "narrative": text, "narrative_status": "generated"}
        )
    items: list[EvidenceItem] = list(prep.sync_evidence)
    by_kind: dict[str, EvidenceItem] = {}
    try:
        for job in prep.jobs:
            item = evidence_from_job_result(f"ev{len(items) + 1}", results[job.evidence_kind], expected_kind=job.evidence_kind)
            items.append(item)
            by_kind[job.evidence_kind] = item
        analysis = analyze(prep, by_kind, settings)
    except AgentError as exc:
        return done(_fail(state, exc))
    failed = [c for c in analysis.checks if c.status == "failed"]
    if not failed:
        work.update(stage="narrate", items=[_item_state(i) for i in items], analysis=_analysis_state(analysis))
    else:
        # Checkpoints keep identifiers only: the failed evidence is re-read through tools by run_id.
        work = {"stage": "done", "evidence": [i.contract_ref() for i in items], "run_ids": {j.evidence_kind: j.run_id for j in prep.jobs if j.run_id}}
    if failed:
        exc = AgentError.validation(
            "deterministic evidence checks failed: " + ", ".join(c.name for c in failed),
            pointer="/evidence",
            failed_checks=[c.record() for c in failed],
            evidence=[i.contract_ref() for i in items],
        )
        return done({**_fail(state, exc), "explanation": work})
    return done({"explanation": work})


def _analysis_state(a: Analysis) -> dict[str, Any]:
    return {"checks": [c.record() for c in a.checks], "labels": a.labels, "mandatory": a.mandatory, "findings": a.findings, "solution_status": a.solution_status}


def after_explain_collect(state: AgentState) -> str:
    return "explain_narrate" if (state.get("explanation") or {}).get("stage") == "narrate" else "respond"


# ============================================================================ narrate
class _CheckRecord:
    def __init__(self, rec: Mapping[str, Any]) -> None:
        self._rec = dict(rec)

    def record(self) -> dict[str, Any]:
        return dict(self._rec)


def explain_narrate(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    from ..graph.nodes import _invoke_provider

    ctx = runtime.context
    settings = _settings(ctx)
    _emit({"type": "progress", "stage": "explain_narrate"})
    work = dict(state.get("explanation") or {})
    prep = _prep_from(work["prepared"])
    items = [_item_from(i) for i in work.get("items") or []]
    an = work.get("analysis") or {}
    last = next((m for m in reversed(state.get("messages", [])) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), None)
    request = GenerateRequest(
        purpose="narrate",
        system=SYSTEM_PROMPT,
        stable_instructions=tuple(ctx.stable_instructions) + (EXPLANATION_OUTPUT_CONTRACT,),
        messages=[{"role": "user", "content": [{"text": text_of(last) if last else "Explain the evidence."}]}],
        max_tokens=settings.narration_cap(ctx.provider_config.max_tokens_invocation),
        temperature=ctx.provider_config.temperature,
        evidence=tuple(i.summary() for i in items),
    )
    updates: dict[str, Any] = {}
    status, message, text = "generated", None, ""
    try:
        result, updates = _invoke_provider(state, ctx, request, stream_tokens=False)
        text = result.text
    except AgentError as exc:
        status = "budget_exceeded" if exc.code == "BUDGET_EXCEEDED" else "unavailable"
        d = exc.details
        if status == "budget_exceeded":
            remaining = d.get("session_remaining_usd", d.get("remaining_allocation_usd"))
            message = f"Narration was not run: the remaining {d.get('budget_category', 'bedrock_explanations')} budget ({remaining} USD) cannot cover the estimate ({d.get('estimated_usd_upper_bound')} USD). The evidence is complete."
        else:
            message = f"Narration was not run: the explanation provider is unavailable ({exc.code}). The evidence is complete."
        updates["error"] = state.get("error") or exc.to_envelope(_cid(state))
    claim = None
    citations: list[str] = []
    if status == "generated":
        claim = check_explanation_narrative(text, items, mandatory=an.get("mandatory") or [])
        text, citations = claim.text, claim.citations
        _emit({"type": "token", "text": text})
    checks = [_CheckRecord(c) for c in an.get("checks") or []]
    doc = assemble(
        explanation_type=prep.explanation_type,
        subject=prep.subject,
        request_ids=prep.request_ids,
        items=items,
        checks=checks,  # type: ignore[arg-type]
        labels=an.get("labels") or [],
        narrative=text,
        narrative_status=status,
        citations=citations,
        narrative_message=message,
        claim_record=claim.record() if claim else None,
        solution_status=an.get("solution_status"),
        findings=an.get("findings"),
    )
    work["stage"] = "done"
    return {
        **updates,
        "explanation": {"stage": "done", "request": work.get("request"), "prepared": {"explanation_type": prep.explanation_type, "request_ids": prep.request_ids}},
        "explanation_result": doc,
        "narrative": doc["narrative"],
        "narrative_status": status,
        "claim_check": claim.record() if claim else {"passed": True, "checked_figures": 0, "removed_figures": []},
    }
