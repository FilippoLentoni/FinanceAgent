"""Tool access of the explanation subflows: guarded, caller-scoped, budget-gated (tasks 1.2, 1.7, 1.11).

:class:`GuardedTools` is the ONLY way explanation code reaches a tool. It wraps the per-invocation
Gateway client, which carries the authenticated caller's own token (spec "Explanations run as the
authenticated caller": no service identity exists in this path; the Runtime role has no
``lambda:InvokeFunction``). It enforces:

* **no state change without confirmation** (FA-EV-08, task 1.7): a tool the catalog marks
  state-changing (or does not know) is called only when its exact idempotency key was approved through
  the phase 1 confirmation interrupt. The one exception is ``submit_experiment`` with ``dry_run``
  true, which validates and estimates without creating anything;
* the phase 1 live-action refusals and denied tool names;
* a hard bound on the calls one explanation makes (it is deterministic code, not a model loop).

:class:`EvidenceJobs` runs the evidence pipeline of design E7: dry-run estimates -> budget gate against
the explanation limits and the estimates' ``remaining_allocation_usd`` (``cpu_research``) -> (after
confirmation) submit -> bounded status reads -> result fetch. FinanceModel's own pre-flight check stays
authoritative; this gate only refuses earlier.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import AgentError
from ..core.ids import idempotency_key
from ..graph.policy import tool_call_refusal
from ..tools.catalog import ToolCatalog
from ..tools.mcp_client import ToolClient, ToolOutcome
from .config import EVIDENCE_BUDGET_CATEGORY, ExplanationSettings

__all__ = ["CallLog", "EvidenceJobs", "GuardedTools", "JobSpec", "MAX_CALLS_PER_EXPLANATION", "TERMINAL_OK", "NON_TERMINAL"]

MAX_CALLS_PER_EXPLANATION = 24
TERMINAL_OK = "succeeded"
NON_TERMINAL = frozenset({"awaiting_approval", "queued", "starting", "running", "stopping"})


@dataclass
class CallLog:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, tool: str, ok: bool, summary: Mapping[str, Any], error_code: str | None) -> None:
        self.calls.append({"tool": tool, "ok": ok, "summary": dict(summary), "error_code": error_code})


def _summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    out: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, (str, int, float, bool)) and len(out) < 12:
            out[k] = v
    return out


class GuardedTools:
    def __init__(
        self,
        tools: ToolClient,
        catalog: ToolCatalog,
        *,
        approved_keys: Iterable[str] = (),
        emit: Callable[[dict[str, Any]], None] | None = None,
        max_calls: int = MAX_CALLS_PER_EXPLANATION,
    ) -> None:
        self._tools = tools
        self._catalog = catalog
        self._approved = set(approved_keys)
        self._emit = emit or (lambda _e: None)
        self._max = max_calls
        self.count = 0
        self.log = CallLog()

    def requires_confirmation(self, name: str, arguments: Mapping[str, Any]) -> bool:
        if not self._catalog.state_changing(name):
            return False
        return not (name == "submit_experiment" and arguments.get("dry_run") is True)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        refusal = tool_call_refusal(name, arguments, denied=self._catalog.is_denied(name))
        if refusal is not None:
            raise AgentError(refusal["code"], refusal["message"], {"kind": refusal["kind"], "tool": name})
        if name not in self._catalog.entries:
            # Not in the released tool catalog (e.g. a tool still to be shipped by FinanceLambdasTool):
            # nothing is called, and the caller learns the dependency is missing rather than being
            # told to confirm a call that cannot happen.
            raise AgentError.dependency(f"{name} is not in the released tool catalog", kind="tool_not_released", tool=name)
        if self.requires_confirmation(name, arguments) and arguments.get("idempotency_key") not in self._approved:
            raise AgentError.not_permitted(f"{name} changes state and was not confirmed; explanations never change state on their own", kind="unconfirmed_state_change", tool=name)
        if self.count >= self._max:
            raise AgentError.budget(f"the explanation exceeded its tool-call bound ({self._max})", limit="max_calls_per_explanation", value=self._max)
        self.count += 1
        self._emit({"type": "tool_call", "tool": name, "arguments": json.loads(json.dumps(arguments, default=str)), "id": f"ex-{self.count}"})
        outcome = self._tools.call_tool(name, arguments)
        code = None if outcome.ok else (outcome.error or {}).get("code")
        summary = _summary(outcome.result) if outcome.ok else {}
        self.log.add(name, outcome.ok, summary, code)
        self._emit({"type": "tool_result_summary", "tool": name, "id": f"ex-{self.count}", "ok": outcome.ok, "summary": summary, "error_code": code})
        return outcome

    def read(self, name: str, arguments: dict[str, Any]) -> Any:
        """A read tool call whose failure is raised as the tool's own contract error."""
        outcome = self.call(name, arguments)
        if not outcome.ok:
            err = outcome.error or {}
            code = err.get("code") or "DEPENDENCY_UNAVAILABLE"
            details = dict(err.get("details") or {})
            details.setdefault("tool", name)
            if code == "NOT_FOUND" and "not offered" in str(err.get("message", "")):
                # The read tool does not exist in this environment yet (EX-OQ-9).
                raise AgentError.dependency(f"the tool {name} is not available in this environment", tool=name, reason="tool_not_offered")
            raise AgentError(code, str(err.get("message") or f"{name} failed"), details)
        return outcome.result


@dataclass
class JobSpec:
    """One evidence job: a FinanceModel CPU experiment submitted through ``submit_experiment``."""

    evidence_kind: str
    job_type: str
    arguments: dict[str, Any]
    resolves: int = 0
    run_id: str | None = None
    estimate: dict[str, Any] | None = None
    idempotency_key: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("evidence_kind", "job_type", "arguments", "resolves", "run_id", "estimate", "idempotency_key")}

    @classmethod
    def from_state(cls, doc: Mapping[str, Any]) -> JobSpec:
        return cls(**{k: doc.get(k) for k in ("evidence_kind", "job_type", "arguments", "resolves", "run_id", "estimate", "idempotency_key")})


class EvidenceJobs:
    def __init__(self, tools: GuardedTools, settings: ExplanationSettings, *, session_id: str, turn: int) -> None:
        self.tools = tools
        self.settings = settings
        self.session_id = session_id
        self.turn = turn

    def _submit_args(self, job: JobSpec, *, dry_run: bool) -> dict[str, Any]:
        args = {**job.arguments, "job_type": job.job_type, "dry_run": dry_run}
        args["idempotency_key"] = idempotency_key(self.session_id, self.turn, "submit_experiment", {**args, "dry_run": False})
        return args

    # ------------------------------------------------------------------ 1. estimates + gate
    def estimate_and_gate(self, jobs: list[JobSpec]) -> dict[str, Any]:
        """Dry-run every job that has no run yet, then apply the budget gate. Raises
        ``BUDGET_EXCEEDED`` (nothing submitted), ``OPERATION_NOT_PERMITTED`` (a non-CPU-research
        category: paid or GPU jobs are never submitted by the agent) or the tool's own error
        (``FORBIDDEN`` when the caller's groups may not submit)."""
        pending = [j for j in jobs if not j.run_id]
        resolves = sum(j.resolves for j in pending)
        if resolves > self.settings.max_resolves_per_request:
            raise AgentError.validation(
                f"the request needs {resolves} re-solves; the maximum per request is {self.settings.max_resolves_per_request}",
                pointer="/explanation",
                max_resolves_per_request=self.settings.max_resolves_per_request,
                requested_resolves=resolves,
            )
        estimates: list[dict[str, Any]] = []
        for job in pending:
            args = self._submit_args(job, dry_run=True)
            job.idempotency_key = args["idempotency_key"]
            res = self.tools.read("submit_experiment", args)
            est = dict((res or {}).get("cost_estimate") or {})
            if "estimated_usd_upper_bound" not in est or "remaining_allocation_usd" not in est or "budget_category" not in est:
                raise AgentError.dependency("the dry run returned no complete cost estimate", tool="submit_experiment", job_type=job.job_type)
            job.estimate = {k: est.get(k) for k in ("estimated_usd_upper_bound", "remaining_allocation_usd", "budget_category", "price_retrieved_at")}
            estimates.append({"job_type": job.job_type, "evidence_kind": job.evidence_kind, "resolves": job.resolves, **job.estimate})
        total = round(sum(float(e["estimated_usd_upper_bound"]) for e in estimates), 6)
        gate = {"estimates": estimates, "total_estimated_usd_upper_bound": total, "max_estimated_usd_per_request": self.settings.max_estimated_usd_per_request}
        for e in estimates:
            if e["budget_category"] != EVIDENCE_BUDGET_CATEGORY:
                raise AgentError.not_permitted(
                    "evidence jobs run only as CPU research; a job in another budget category needs recorded human approval outside the agent",
                    kind="non_cpu_evidence_job",
                    job_type=e["job_type"],
                    budget_category=e["budget_category"],
                )
        if estimates:
            remaining = min(float(e["remaining_allocation_usd"]) for e in estimates)
            gate["remaining_allocation_usd"] = remaining
            gate["budget_category"] = EVIDENCE_BUDGET_CATEGORY
            if total > remaining + 1e-12:
                raise AgentError.budget(
                    "the remaining cpu_research allocation cannot cover the evidence jobs; no job was submitted",
                    budget_category=EVIDENCE_BUDGET_CATEGORY,
                    remaining_allocation_usd=remaining,
                    estimated_usd_upper_bound=total,
                    estimates=estimates,
                    offer=self._offer(jobs),
                )
            if total > self.settings.max_estimated_usd_per_request + 1e-12:
                raise AgentError.budget(
                    "the evidence estimate exceeds the per-request explanation limit; no job was submitted",
                    budget_category=EVIDENCE_BUDGET_CATEGORY,
                    limit="max_estimated_usd_per_request",
                    limit_usd=self.settings.max_estimated_usd_per_request,
                    estimated_usd_upper_bound=total,
                    remaining_allocation_usd=remaining,
                    estimates=estimates,
                    offer=self._offer(jobs),
                )
        return gate

    @staticmethod
    def _offer(jobs: list[JobSpec]) -> str:
        kinds = {j.evidence_kind for j in jobs}
        if "grouped_shapley" in kinds:
            return "controlled_resolve_only: request the explanation without shapley (k + 2 re-solves instead of 2^k)"
        if "sensitivity_sweep" in kinds:
            return "fewer_sweep_points: request fewer parameter values"
        return "existing_evidence_only: reuse evidence by run_id from an earlier explanation"

    # ------------------------------------------------------------------ 2. submit (confirmed)
    def submission_calls(self, jobs: list[JobSpec]) -> list[dict[str, Any]]:
        """The exact ``submit_experiment`` calls shown in the confirmation request."""
        out = []
        for job in jobs:
            if job.run_id:
                continue
            args = self._submit_args(job, dry_run=False)
            job.idempotency_key = args["idempotency_key"]
            out.append({"tool": "submit_experiment", "arguments": args, "idempotency_key": args["idempotency_key"], "estimate": job.estimate})
        return out

    def submit(self, jobs: list[JobSpec]) -> None:
        for job in jobs:
            if job.run_id:
                continue
            res = self.tools.read("submit_experiment", self._submit_args(job, dry_run=False))
            run_id = (res or {}).get("run_id")
            if not isinstance(run_id, str):
                raise AgentError.dependency("submit_experiment returned no run_id", tool="submit_experiment")
            job.run_id = run_id

    # ------------------------------------------------------------------ 3. status + results
    def collect(self, jobs: list[JobSpec]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """(results by evidence kind, still-running jobs). Never waits: at most ``status_polls``
        status reads per job; a long job ends the turn ``in_progress`` with its ``run_id``."""
        results: dict[str, Any] = {}
        running: list[dict[str, Any]] = []
        for job in jobs:
            state = None
            for _ in range(max(1, self.settings.status_polls)):
                st = self.tools.read("get_job_status", {"run_id": job.run_id})
                state = (st or {}).get("state")
                if state not in NON_TERMINAL:
                    break
            if state in NON_TERMINAL:
                running.append({"run_id": job.run_id, "state": state, "evidence_kind": job.evidence_kind})
                continue
            results[job.evidence_kind] = self.tools.read("get_experiment_result", {"run_id": job.run_id})
        return results, running
