"""Evidence items: tool-computed evidence, its trusted reference and its citation key (design E2).

An :class:`EvidenceItem` wraps ONE evidence document returned by a tool or an evidence job, together
with the contract trusted reference (``core/v1/artifact-ref.json``, kind ``explanation_evidence``,
with checksum) that identifies it. The agent never edits the numbers: it reads them, checks them
(:mod:`.checks`), labels them, and renders deterministic statements that quote them verbatim.

Draft evidence payload shapes (CONTRACT GAP-E2 / EX-OQ-2: the per-kind ``finance/v1`` schemas do
not exist yet) are documented in ``docs/explanations.md`` and pinned as DRAFT schemas in
``tests/fixtures/explanations/draft-schemas/`` (never published).

Citation keys are ``ev1``, ``ev2``, ... in the order the evidence was obtained; narratives cite them as
``[ev1]``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import AgentError

__all__ = [
    "EVIDENCE_KINDS",
    "EvidenceItem",
    "evidence_from_job_result",
    "evidence_from_tool",
    "fmt",
    "numeric_leaves",
    "CHECKSUM_RE",
]

#: Evidence kinds (design E2) and the FinanceModel job types that produce them (GAP-E1).
EVIDENCE_KINDS = (
    "performance_decomposition",
    "difference_inventory",
    "controlled_resolve_set",
    "grouped_shapley",
    "sensitivity_sweep",
)
CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}\Z")


def fmt(v: Any) -> str:
    """Verbatim rendering of a tool value in a statement (the claim check matches it exactly)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def numeric_leaves(doc: Any) -> list[float]:
    out: list[float] = []

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            if math.isfinite(v):
                out.append(float(v))
        elif isinstance(v, Mapping):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(doc)
    return out


def _valid_ref(ref: Any) -> bool:
    return (
        isinstance(ref, Mapping)
        and ref.get("kind") == "explanation_evidence"
        and isinstance(ref.get("artifact_id"), str)
        and bool(_ARTIFACT_ID_RE.match(ref["artifact_id"]))
        and isinstance(ref.get("checksum"), str)
        and bool(CHECKSUM_RE.match(ref["checksum"]))
        and isinstance(ref.get("owner"), str)
        and isinstance(ref.get("content_type"), str)
    )


@dataclass
class EvidenceItem:
    key: str
    kind: str
    ref: dict[str, Any]
    payload: dict[str, Any]
    source_tool: str
    run_id: str | None = None
    labels: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)

    def values(self) -> list[float]:
        """Every number in the evidence document (what a narrative citing this item may state)."""
        return numeric_leaves(self.payload)

    def strings(self) -> set[str]:
        out: set[str] = set()

        def walk(v: Any) -> None:
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, Mapping):
                for k, x in v.items():
                    out.add(str(k))
                    walk(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    walk(x)

        walk(self.payload)
        return out

    def label(self, *labels: str) -> None:
        for lb in labels:
            if lb not in self.labels:
                self.labels.append(lb)

    def contract_ref(self) -> dict[str, Any]:
        keep = ("artifact_id", "owner", "kind", "checksum", "content_type", "size_bytes", "domain", "synthetic")
        return {k: self.ref[k] for k in keep if k in self.ref}

    def summary(self, max_statements: int = 12) -> dict[str, Any]:
        """Compact narration input: citation key, kind, labels and the deterministic statements."""
        return {"cite": self.key, "kind": self.kind, "labels": list(self.labels), "artifact_id": self.ref.get("artifact_id"), "checksum": self.ref.get("checksum"), "statements": self.statements[:max_statements]}


def _evidence_ref(doc: Mapping[str, Any], *, tool: str) -> dict[str, Any]:
    ref = doc.get("evidence_ref")
    if ref is None:
        refs = [a for a in doc.get("artifacts") or [] if isinstance(a, Mapping) and a.get("kind") == "explanation_evidence"]
        ref = refs[0] if len(refs) == 1 else None
    if not _valid_ref(ref):
        raise AgentError.dependency(f"{tool} returned no trusted explanation_evidence reference with a checksum", tool=tool, reason="evidence_reference_missing")
    return dict(ref)  # type: ignore[arg-type]


def evidence_from_tool(key: str, tool: str, doc: Any, *, expected_kind: str) -> EvidenceItem:
    """Evidence returned synchronously by a read tool (``compare_plan_versions``)."""
    if not isinstance(doc, Mapping):
        raise AgentError.dependency(f"{tool} returned no evidence document", tool=tool)
    ref = _evidence_ref(doc, tool=tool)
    payload = doc.get("evidence")
    if not isinstance(payload, Mapping) or payload.get("evidence_kind") != expected_kind:
        raise AgentError.dependency(f"{tool} returned evidence of an unexpected kind", tool=tool, expected_kind=expected_kind, reason="evidence_kind_mismatch")
    return EvidenceItem(key=key, kind=expected_kind, ref=ref, payload=dict(payload), source_tool=tool)


def evidence_from_job_result(key: str, doc: Any, *, expected_kind: str) -> EvidenceItem:
    """Evidence from ``get_experiment_result`` (contract ``job-result``; CS-07): the run must have
    ``completion_status`` ``succeeded`` (any ``solution_status``, including ``infeasible`` and
    ``no_effect``) and carry exactly one ``explanation_evidence`` artifact and the evidence payload."""
    if not isinstance(doc, Mapping):
        raise AgentError.dependency("get_experiment_result returned no document", tool="get_experiment_result")
    run_id = doc.get("run_id")
    status = doc.get("completion_status")
    if status != "succeeded":
        err = doc.get("error") or {}
        raise AgentError.dependency(f"the evidence run {run_id} did not succeed ({status})", tool="get_experiment_result", run_id=run_id, completion_status=status, run_error_code=err.get("code") if isinstance(err, Mapping) else None)
    if doc.get("artifacts_complete") is False:
        raise AgentError.dependency(f"the evidence run {run_id} has incomplete artifacts", run_id=run_id, reason="artifacts_incomplete")
    ref = _evidence_ref(doc, tool="get_experiment_result")
    payload = (doc.get("payload") or {}).get("evidence") if isinstance(doc.get("payload"), Mapping) else None
    if not isinstance(payload, Mapping) or payload.get("evidence_kind") != expected_kind:
        raise AgentError.dependency("the evidence run returned evidence of an unexpected kind", run_id=run_id, expected_kind=expected_kind, reason="evidence_kind_mismatch")
    item = EvidenceItem(key=key, kind=expected_kind, ref=ref, payload=dict(payload), source_tool="get_experiment_result", run_id=run_id if isinstance(run_id, str) else None)
    item.payload.setdefault("solution_status", doc.get("solution_status"))
    return item


#: Fields an evidence document must record so it can be recomputed (spec "Evidence is reproducible
#: from identifiers"; task 1.6). The FinanceModel evidence job records its own submission.
REPRODUCTION_FIELDS = ("job_type", "input_snapshot_id", "evaluation_window", "configuration")


def reproduction_arguments(item: EvidenceItem) -> dict[str, Any]:
    """The ``submit_experiment`` arguments that recompute ``item`` from its recorded identifiers,
    seeds and evaluator version (without ``idempotency_key``: a reproduction is a new research run).
    Raises ``DEPENDENCY_UNAVAILABLE`` naming the missing fields when the evidence cannot be reproduced."""
    rec = item.payload.get("recorded_request")
    missing = [f for f in REPRODUCTION_FIELDS if not isinstance(rec, Mapping) or rec.get(f) in (None, "")]
    for f in ("evaluator_version",):
        if not item.payload.get(f):
            missing.append(f)
    if missing:
        raise AgentError.dependency("the evidence does not record what is needed to recompute it", reason="not_reproducible", missing=missing, artifact_id=item.ref.get("artifact_id"))
    assert isinstance(rec, Mapping)
    cfg = dict(rec["configuration"])
    payload = dict(cfg.get("payload") or {})
    payload["reproduce"] = {
        "artifact_id": item.ref.get("artifact_id"),
        "checksum": item.ref.get("checksum"),
        "evaluator_version": item.payload.get("evaluator_version"),
        "seeds": list(item.payload.get("seeds") or (item.payload.get("lineage") or {}).get("seeds") or []),
    }
    cfg["payload"] = payload
    return {"domain": "finance", "domain_schema_version": "1.0", "purpose": "research", "job_type": rec["job_type"], "input_snapshot_id": rec["input_snapshot_id"], "evaluation_window": dict(rec["evaluation_window"]), "configuration": cfg}


def reproduced(original: EvidenceItem, recomputed: EvidenceItem) -> bool:
    """FA-EV-03: the recomputed artifact's checksum equals the original's."""
    return bool(original.ref.get("checksum")) and original.ref.get("checksum") == recomputed.ref.get("checksum")
