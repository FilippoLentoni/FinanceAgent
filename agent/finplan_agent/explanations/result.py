"""The explanation result assembler (design E2; spec explanation-evidence "Evidence is separate from
narrative"; FA-EV-02; task 1.1).

The result IS a contract ``core/v1/explanation-result.json`` document (``domain``,
``domain_schema_version``, ``subject``, ``evidence[]`` trusted references with checksums,
``narrative``, ``generated_at``), validated against the pinned contract before it is returned, plus
the explanation fields of design E2 that the contract does not define yet (CONTRACT GAP-E2 /
EX-OQ-2; the envelope allows additional top-level properties):

* ``explanation_type``, ``request_ids``;
* ``evidence_items[]``: citation key, evidence kind, artifact ID, checksum, run ID, labels and the
  deterministic statements quoting the evidence;
* ``checks[]`` (name, status, tolerance, observed), ``labels[]``;
* ``narrative_status`` (``generated`` | ``unavailable`` | ``budget_exceeded``), ``narrative_message``,
  ``citations[]`` (citation key -> artifact ID), ``claim_check``;
* ``completion_status`` (always ``succeeded`` for a returned result) and ``solution_status``
  (``no_effect`` when the change has no modeled effect; CS-07);
* ``findings`` (sweep classifications, concessions, experiment proposals), ``disclaimer``.

The contract requires a non-empty ``narrative`` string; when no narrative was generated it holds a
fixed figure-free notice and ``narrative_status`` says why.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ..core.errors import AgentError
from .checks import Check
from .evidence import EvidenceItem
from .workflows import DISCLAIMER, DOMAIN, DOMAIN_SCHEMA_VERSION

__all__ = ["NARRATIVE_STATUSES", "assemble", "notice", "validate_result"]

NARRATIVE_STATUSES = ("generated", "unavailable", "budget_exceeded")


def notice(status: str) -> str:
    if status == "budget_exceeded":
        return "Narrative not generated (budget_exceeded): the evidence section below is complete and is the authoritative result."
    return "Narrative unavailable: the explanation provider could not be used; the evidence section below is complete and is the authoritative result."


def validate_result(doc: Mapping[str, Any]) -> None:
    from finplan_contracts.validate import validate

    res = validate(dict(doc), "explanation-result")
    if not res.valid:
        raise AgentError.internal("the explanation result does not conform to the contract explanation envelope")


def assemble(
    *,
    explanation_type: str,
    subject: Mapping[str, Any],
    request_ids: Mapping[str, Any],
    items: Sequence[EvidenceItem],
    checks: Iterable[Check],
    labels: Iterable[str],
    narrative: str,
    narrative_status: str,
    citations: Iterable[str] = (),
    narrative_message: str | None = None,
    claim_record: Mapping[str, Any] | None = None,
    solution_status: str | None = None,
    findings: Mapping[str, Any] | None = None,
    synthetic: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if narrative_status not in NARRATIVE_STATUSES:
        raise ValueError(f"unknown narrative status {narrative_status}")
    if not items:
        raise AgentError.internal("an explanation result needs at least one evidence reference")
    by_key = {i.key: i for i in items}
    text = narrative if narrative_status == "generated" and narrative.strip() else notice(narrative_status)
    lbls = list(dict.fromkeys(list(labels) + [lb for i in items for lb in i.labels]))
    doc: dict[str, Any] = {
        "domain": DOMAIN,
        "domain_schema_version": DOMAIN_SCHEMA_VERSION,
        "subject": {k: v for k, v in subject.items() if k in ("plan_version_id", "compare_to_plan_version_id") and v},
        "evidence": [i.contract_ref() for i in items],
        "narrative": text,
        "generated_at": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "explanation_type": explanation_type,
        "request_ids": dict(request_ids),
        "evidence_items": [
            {"cite": i.key, "kind": i.kind, "artifact_id": i.ref.get("artifact_id"), "checksum": i.ref.get("checksum"), "run_id": i.run_id, "source_tool": i.source_tool, "labels": list(i.labels), "statements": list(i.statements)}
            for i in items
        ],
        "checks": [c.record() for c in checks],
        "labels": lbls,
        "narrative_status": narrative_status,
        "citations": [{"cite": k, "artifact_id": by_key[k].ref.get("artifact_id")} for k in dict.fromkeys(citations) if k in by_key] if narrative_status == "generated" else [],
        "completion_status": "succeeded",
        "solution_status": solution_status or ("no_effect" if "no_effect" in lbls and explanation_type == "recommendation_change" else "not_applicable"),
        "findings": dict(findings or {}),
    }
    if "modeled_effect" in lbls:
        doc["disclaimer"] = DISCLAIMER
    if narrative_message:
        doc["narrative_message"] = narrative_message
    if claim_record is not None:
        doc["claim_check"] = dict(claim_record)
    if synthetic is True or any(i.ref.get("synthetic") is True for i in items):
        doc["synthetic"] = True
    validate_result(doc)
    return doc
