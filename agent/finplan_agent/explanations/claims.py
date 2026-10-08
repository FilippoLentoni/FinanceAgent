"""Explanation claim check: the phase 1 claim check extended for evidence (design E6; FA-EV-01,
FA-EV-05, FA-WF3-06; task 1.4).

The narrative draft is checked sentence by sentence, deterministically (never by an LLM):

1. **Citations.** Citation tags ``[evN]`` must name an evidence item of this result; unknown tags are
   removed and recorded.
2. **Grounded figures.** Every figure in a sentence must match (declared rounding, phase 1 rules) a
   value of an evidence item the SAME sentence cites. A figure in a sentence without a citation, or one
   the cited evidence does not contain (e.g. a sum the provider computed), is replaced by the phase 1
   removal marker.
3. **Causal wording.** Causal verbs attached to modeled effects ("caused", "drove", "led to",
   "because the market", ...) are rewritten as modeled effects and recorded.
4. **Trend claims.** A sentence claiming a monotonic or steady trend must be supported by the computed
   sweep points it cites; otherwise the sentence is removed and recorded (no interpolation, no assumed
   monotonicity).
5. **No-effect claims.** A sentence claiming "no effect", "unchanged" or "insensitive" must cite an
   item labeled ``no_effect``; otherwise it is removed.
6. **Mandatory statements** (disclaimer, partial-period note, uncertainty note) are appended when the
   draft omits them. When nothing cited survives, the narrative is replaced by the deterministic
   evidence statements (which always cite).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..graph.claim_check import REMOVED, claim_check
from .evidence import EvidenceItem

__all__ = ["ExplanationClaimResult", "check_explanation_narrative", "CITATION_RE", "is_monotonic"]

CITATION_RE = re.compile(r"\[(ev\d{1,3})\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")
_CAUSAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbecause (?:the )?market\b", re.I), "while the market"),
    (re.compile(r"\b(?:was|were|is|are) caused by\b", re.I), "is modeled as attributable to"),
    (re.compile(r"\b(?:caused|causes|cause)\b", re.I), "is modeled to account for"),
    (re.compile(r"\b(?:drove|drives|driven)\b", re.I), "is modeled to account for"),
    (re.compile(r"\b(?:led to|leads to|resulted in)\b", re.I), "is modeled to account for"),
    (re.compile(r"\b(?:due to|because of)\b", re.I), "modeled as attributable to"),
)
_TREND = re.compile(r"(?<!non-)(?<!non)(?<!not )\b(?:monotonic(?:ally)?|steadily|consistently|strictly (?:increasing|decreasing)|always (?:rises|falls|increases|decreases))\b", re.I)
_NO_EFFECT = re.compile(r"\b(?:no effect|no modeled effect|no change|unchanged|identical|insensitive)\b", re.I)
_IDENT = re.compile(r"^(?:sha256:[0-9a-f]{64}|cfg_[0-9a-f]{64}|[a-z]{2,4}_[0-7][0-9A-HJKMNP-TV-Z]{25})\Z")
_METRIC_WORDS = {"risk": "risk", "return": "expected_return", "turnover": "turnover", "cost": "cost_estimate"}


@dataclass
class ExplanationClaimResult:
    text: str
    checked_figures: int = 0
    removed_figures: list[str] = field(default_factory=list)
    unknown_citations: list[str] = field(default_factory=list)
    causal_rewrites: list[str] = field(default_factory=list)
    rejected_sentences: list[dict[str, str]] = field(default_factory=list)
    appended: list[str] = field(default_factory=list)
    replaced_with_statements: bool = False
    citations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.removed_figures or self.unknown_citations or self.causal_rewrites or self.rejected_sentences or self.replaced_with_statements)

    def record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checked_figures": self.checked_figures,
            "removed_figures": list(self.removed_figures),
            "unknown_citations": list(self.unknown_citations),
            "causal_rewrites": list(self.causal_rewrites),
            "rejected_sentences": list(self.rejected_sentences),
            "appended_statements": len(self.appended),
            "replaced_with_statements": self.replaced_with_statements,
        }


def is_monotonic(series: Sequence[float]) -> bool:
    if len(series) < 2:
        return True
    inc = all(b >= a for a, b in zip(series, series[1:], strict=False))
    dec = all(b <= a for a, b in zip(series, series[1:], strict=False))
    return inc or dec


def _sweep_series(item: EvidenceItem) -> dict[str, list[float]]:
    """Computed metric series over the sweep's FEASIBLE points, in sweep order."""
    out: dict[str, list[float]] = {}
    for p in item.payload.get("points") or []:
        if not isinstance(p, Mapping) or p.get("solution_status") == "infeasible":
            continue
        for k, v in (p.get("metrics") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.setdefault(k, []).append(float(v))
    return out


def _trend_supported(sentence: str, cited: list[EvidenceItem]) -> bool:
    sweeps = [i for i in cited if i.kind == "sensitivity_sweep"]
    if not sweeps:
        return False  # a trend needs computed points
    named = {m for w, m in _METRIC_WORDS.items() if re.search(rf"\b{w}", sentence, re.I)}
    for item in sweeps:
        series = _sweep_series(item)
        metrics = named or set(series)
        if not metrics or any(not is_monotonic(series.get(m, [])) or m not in series for m in metrics):
            return False
    return True


def _mask_identifiers(sentence: str, cited: list[EvidenceItem]) -> tuple[str, list[str]]:
    """Identifiers and checksums of the cited evidence are not figures: they are masked before the
    figure check (an all-digit checksum would otherwise read as a number)."""
    idents: set[str] = set()
    for i in cited:
        idents.update(x for x in (i.ref.get("artifact_id"), i.ref.get("checksum"), i.run_id) if isinstance(x, str))
        idents.update(x for x in i.strings() if _IDENT.match(x))
    tokens: list[str] = []
    out = sentence
    for ident in sorted(idents, key=len, reverse=True):
        if ident in out:
            tokens.append(ident)
            out = out.replace(ident, chr(0xE000 + len(tokens) - 1))
    return out, tokens


def _unmask(text: str, tokens: list[str]) -> str:
    for n, ident in enumerate(tokens):
        text = text.replace(chr(0xE000 + n), ident)
    return text


def _rewrite_causal(sentence: str, rec: ExplanationClaimResult) -> str:
    out = sentence
    for pattern, repl in _CAUSAL:
        for m in pattern.finditer(out):
            rec.causal_rewrites.append(m.group(0))
        out = pattern.sub(repl, out)
    return out


def check_explanation_narrative(text: str, items: Iterable[EvidenceItem], *, mandatory: Sequence[str] = ()) -> ExplanationClaimResult:
    by_key = {i.key: i for i in items}
    rec = ExplanationClaimResult(text="")
    kept: list[str] = []
    cited_all: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text.strip()) if text.strip() else []:
        sentence = raw
        tags = CITATION_RE.findall(sentence)
        unknown = [t for t in tags if t not in by_key]
        for t in unknown:
            rec.unknown_citations.append(t)
            sentence = sentence.replace(f"[{t}]", "")
        cited = [by_key[t] for t in dict.fromkeys(tags) if t in by_key]
        if any(s in sentence for s in mandatory):
            kept.append(sentence)  # deterministic statements are kept verbatim
            cited_all.extend(i.key for i in cited)
            continue
        if _TREND.search(sentence) and not _trend_supported(sentence, cited):
            rec.rejected_sentences.append({"rule": "unsupported_trend", "sentence": sentence[:300]})
            continue
        if _NO_EFFECT.search(sentence) and not any("no_effect" in i.labels for i in cited):
            rec.rejected_sentences.append({"rule": "unsupported_no_effect", "sentence": sentence[:300]})
            continue
        sentence = _rewrite_causal(sentence, rec)
        values: list[Any] = [i.payload for i in cited]
        masked, tokens = _mask_identifiers(sentence, cited)
        checked = claim_check(masked, values)
        checked.text = _unmask(checked.text, tokens)
        rec.checked_figures += checked.checked
        rec.removed_figures.extend(checked.unsupported)
        kept.append(checked.text)
        cited_all.extend(i.key for i in cited)
    rec.citations = list(dict.fromkeys(cited_all))
    if not rec.citations:
        rec.replaced_with_statements = bool(text.strip())
        stmts = [s for i in by_key.values() for s in i.statements]
        kept = stmts
        rec.citations = [i.key for i in by_key.values() if i.statements]
    body = " ".join(s for s in kept if s.strip())
    for m in mandatory:
        if m not in body:
            body = f"{body} {m}".strip()
            rec.appended.append(m)
    rec.text = body
    return rec


def contains_removed(text: str) -> bool:
    return REMOVED in text
