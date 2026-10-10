"""The deterministic claim check (spec explanation-provider "Provider output never carries authoritative
numbers"; FA-PRV-05; task 3.5).

Every number in a narrative draft must match a value from a tool result of the CURRENT turn (figures
are always re-read through tools, never taken from checkpoints) under declared rounding:

* a figure written with ``d`` decimals matches a tool value ``v`` when ``round(v, d)`` equals it;
* a percentage ``p%`` also matches a tool value ``v`` when ``round(v * 100, d)`` equals ``p``
  (fractions reported as percentages);
* thousands separators are ignored; ISO dates must appear verbatim in a tool string value.

Numbers inside identifiers (``pv_01J...``, ``run_...``, hashes) are not figures. Unsupported figures are
replaced by ``[unsupported figure removed]`` and listed in the check record, so the response never
carries a provider-made number. LLM judging is never part of this check.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ClaimCheckResult", "claim_check", "collect_values", "REMOVED"]

REMOVED = "[unsupported figure removed]"
_DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+Z?)?(?![\w-])")
_NUM = re.compile(r"(?<![\w.$/-])-?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?%?(?![\w])")
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)](?=\s)")
_VERSION_FIELD = re.compile(r'"(?:[A-Za-z_]\w*_)?version"\s*:\s*"(?P<value>[^"\n]+)"')
_DOTTED_VERSION = re.compile(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?")


@dataclass
class ClaimCheckResult:
    text: str
    checked: int = 0
    unsupported: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.unsupported

    def record(self) -> dict[str, Any]:
        return {"passed": self.passed, "checked_figures": self.checked, "removed_figures": list(self.unsupported)}


def collect_values(results: Iterable[Any]) -> tuple[list[float], set[str]]:
    """Numeric leaves and string leaves of tool results (numeric strings count as numbers)."""
    nums: list[float] = []
    strings: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            if math.isfinite(v):
                nums.append(float(v))
        elif isinstance(v, str):
            strings.add(v)
            try:
                f = float(v.replace(",", ""))
                if math.isfinite(f):
                    nums.append(f)
            except ValueError:
                pass
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for r in results:
        walk(r)
    return nums, strings


def _matches(token: str, values: list[float]) -> bool:
    pct = token.endswith("%")
    raw = token.rstrip("%").replace("$", "").replace(",", "")
    try:
        n = float(raw)
    except ValueError:
        return True
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    for v in values:
        if "e" in raw.lower():
            if math.isclose(v * (100 if pct else 1), n, rel_tol=1e-12, abs_tol=1e-30):
                return True
            continue
        if round(v, decimals) == n:
            return True
        if pct and round(v * 100, decimals) == n:
            return True
    return False


def claim_check(text: str, tool_results: Iterable[Any]) -> ClaimCheckResult:
    values, strings = collect_values(tool_results)
    result = ClaimCheckResult(text=text)
    out: list[str] = []
    pos = 0
    spans: list[tuple[int, int, str]] = []
    markers = [(m.start(), m.end()) for m in _LIST_MARKER.finditer(text)]
    # Dated news/literature titles may contain figures. Preserve them only when the
    # complete source string is reproduced verbatim, rather than trusting an isolated digit.
    verbatim = []
    # A quoted software/schema version is metadata, not a financial figure.
    # Accept only the complete exact version from this turn's evidence, scoped
    # to a version field; its digits cannot authorize figures elsewhere.
    for match in _VERSION_FIELD.finditer(text):
        source = match.group('value')
        if source in strings and _DOTTED_VERSION.fullmatch(source):
            verbatim.append(match.span('value'))
    for source in strings:
        if len(source) < 16:
            continue
        offset = 0
        while (start := text.find(source, offset)) >= 0:
            verbatim.append((start, start + len(source)))
            offset = start + len(source)
    for m in _DATE.finditer(text):
        spans.append((m.start(), m.end(), "date"))
    for m in _NUM.finditer(text):
        if any(s <= m.start() < e for s, e, _ in spans) or any(s <= m.start() < e for s, e in markers):
            continue
        spans.append((m.start(), m.end(), "num"))
    spans.sort()
    for start, end, kind in spans:
        token = text[start:end]
        result.checked += 1
        ok = any(a <= start and end <= b for a, b in verbatim) or (any(token in s for s in strings) if kind == "date" else _matches(token, values))
        out.append(text[pos:start])
        if ok:
            out.append(token)
        else:
            out.append(REMOVED)
            result.unsupported.append(token)
        pos = end
    out.append(text[pos:])
    result.text = "".join(out)
    return result
