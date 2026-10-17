"""Recommendation run-log queries shared by the hosted agent route and the operator script.

The run log (``finplan-<env>-financeagent-recommendation-runs``) holds one item per (run, portfolio):
``pk=run_id``, ``sk=portfolio_id`` and the GSI ``recent`` (partition ``env``, sort
``started_key = <started_at ISO UTC>#<run_id>``). Items reference full immutable decisions by ``pd_`` id.

Everything here is deterministic: requests are parsed with regular expressions, the table is read with
``Query`` only (newest first, bounded paging) and answers are rendered from the stored values. The
renderer returns every figure it prints as evidence, so the claim check can verify the answer without
any model narration. No Gateway tool is called.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

__all__ = [
    "DEFAULT_LIMIT", "MAX_LIMIT", "METHODS", "INDEX", "RunLogReader", "RunLogUnavailable", "RunsPage",
    "day_bounds", "get_run", "latest_runs", "parse_runs_request", "plain", "render_runs", "runs_plan", "table_name",
]

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
INDEX = "recent"
METHODS = ("ppo", "min_variance", "mean_variance", "cvar")
METHOD_LABELS = {"ppo": "PPO", "min_variance": "minimum variance", "mean_variance": "mean variance", "cvar": "scenario CVaR"}
NEW_YORK = ZoneInfo("America/New_York")
_MAX_PAGES = 10
_TARGETS_SHOWN = 4

_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
          "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
_WORD_NUMBERS = {w: i + 1 for i, w in enumerate(_WORDS)}
_NUMBER = r"(?:\d{1,3}|" + "|".join(_WORDS) + r")"
_NOUN = r"(?:recommendations|runs|policies)"
_LATEST = re.compile(r"\b(?:latest|last|most\s+recent|recent|newest)\s+(?:(?P<n>" + _NUMBER + r")\s+)?(?:[\w-]+\s+){0,5}?(?P<noun>" + _NOUN + r")\b", re.I)
_COUNT_FIRST = re.compile(r"\b(?P<n>" + _NUMBER + r")\s+(?:latest|last|most\s+recent|recent|newest)\s+(?:[\w-]+\s+){0,5}?(?P<noun>" + _NOUN + r")\b", re.I)
_RUN_LOG = re.compile(r"\b(?:recommendation\s+run\s+log|run\s+log|recommendation\s+runs)\b", re.I)
_DATED_NOUN = re.compile(r"\b(?P<noun>" + _NOUN + r")\b", re.I)
_SINCE = re.compile(r"\bsince\s+(?P<since>\d{4}-\d{2}-\d{2}|today|yesterday)\b", re.I)
_DAY = re.compile(r"\b(?P<day>today|yesterday)(?:['’]s)?\b", re.I)
_ON_DATE = re.compile(r"\b(?:on|for|from)\s+(?P<on>\d{4}-\d{2}-\d{2})\b", re.I)
_HISTORY = re.compile(r"\b(?:history|latest three|last three|last 3|previous portfolios|holdings revisions)\b", re.I)
# Requests that belong to other routes: other records, actions on a decision, new recommendations.
_OTHER = re.compile(
    r"\b(?:holdings?|revisions?|snapshots?|portfolios|interactions?|conversations?|activity|audit|tool\s+calls?"
    r"|why|explain\w*|rationale|compare|comparison|changed?|changes|differ\w*|accept|approve|apply|reject|decline"
    r"|evaluate|assess|review|perform\w*|doing|returns?|profit\w*|gains?|loss\w*|outcomes?|postmortem|generate|create|make|new|fresh|trigger|rerun|re-run|request|should|invest\w*"
    r"|experiments?|benchmarks?|jobs?|backtests?|research|training|crypto|bitcoin|ethereum|btc|eth|coinbase)\b",
    re.I,
)
_LIST_VERB = re.compile(r"\b(?:show|list|display|what|which|were|did)\b", re.I)
_PD = re.compile(r"\bpd_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_PF = re.compile(r"\bpf_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_METHOD_PATTERNS = (
    ("ppo", r"\b(?:ppo|reinforcement|rl)\b"),
    ("min_variance", r"\bmin(?:imum)?[\s_-]*variance\b"),
    ("mean_variance", r"\bmean[\s_-]*variance\b"),
    ("cvar", r"\b(?:scenario[\s_-]+)?c[\s-]?va\s?r\b"),
)


class RunLogUnavailable(Exception):
    """The run log cannot be read (not configured, missing table or denied). Never substitute data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class RunsPage:
    items: list[dict[str, Any]] = field(default_factory=list)
    more: bool = False


# ---------------------------------------------------------------------------------------------- values
def plain(value: Any) -> Any:
    """DynamoDB ``Decimal`` -> ``int``/``float`` (recursively) so values print and verify as numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value


def table_name(environment: str) -> str:
    return f"finplan-{environment}-financeagent-recommendation-runs"


def day_bounds(day: date) -> tuple[str, str]:
    """UTC ISO bounds of one America/New_York calendar day (the formation calendar)."""
    start = datetime.combine(day, dtime(0, 0), NEW_YORK).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), dtime(0, 0), NEW_YORK).astimezone(timezone.utc)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ny_today(now: datetime | None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(NEW_YORK).date()


# ---------------------------------------------------------------------------------------------- parse
def _methods(text: str) -> list[str] | None:
    found = [m for m, p in _METHOD_PATTERNS if re.search(p, text, re.I)]
    if re.search(r"\b(?:traditional|classical|optimi[sz]er)\b", text, re.I):
        found += [m for m in ("min_variance", "mean_variance", "cvar") if m not in found]
    return found or None


def _number(token: str | None) -> int | None:
    if not token:
        return None
    return int(token) if token.isdigit() else _WORD_NUMBERS[token.lower()]


def parse_runs_request(text: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """``{"limit", "requested", "methods", "since", "until", "day", "portfolio_id"}`` or None when the
    text is not a run-log listing request (existing history, explanation and action routes keep it)."""
    if not text or _PD.search(text) or _OTHER.search(text):
        return None
    match = _LATEST.search(text) or _COUNT_FIRST.search(text)
    log_phrase = _RUN_LOG.search(text)
    since = _SINCE.search(text)
    day = _DAY.search(text)
    on = _ON_DATE.search(text)
    # A bare date ("PPO recommendations for today") may be a request for a NEW recommendation:
    # only "since <day>" or an explicit listing verb makes it a run-log listing.
    dated = bool((since or (day or on) and _LIST_VERB.search(text)) and _DATED_NOUN.search(text))
    if not (match or log_phrase or dated):
        return None
    noun = (match.group("noun") if match else (_DATED_NOUN.search(text) or log_phrase).group(0)).lower()
    # The established lifecycle history route owns "latest three recommendations", "... history" etc.
    if _HISTORY.search(text) and not log_phrase and noun not in ("runs", "policies"):
        return None
    requested = _number(match.group("n")) if match else None
    limit = DEFAULT_LIMIT if requested is None else max(1, min(MAX_LIMIT, requested))
    today = _ny_today(now)
    lo = hi = None
    label = None
    if since:
        token = since.group("since").lower()
        try:
            start = today if token == "today" else today - timedelta(days=1) if token == "yesterday" else date.fromisoformat(token)
        except ValueError:
            return None
        lo, label = day_bounds(start)[0], "since " + start.isoformat()
    elif on:
        try:
            start = date.fromisoformat(on.group("on"))
        except ValueError:
            return None
        (lo, hi), label = day_bounds(start), "on " + start.isoformat()
    elif day:
        start = today if day.group("day").lower() == "today" else today - timedelta(days=1)
        (lo, hi), label = day_bounds(start), "on " + start.isoformat()
    if (lo or hi) and requested is None:
        limit = MAX_LIMIT
    portfolio = _PF.search(text)
    return {"limit": limit, "requested": requested, "methods": _methods(text), "since": lo, "until": hi,
            "period": label, "portfolio_id": portfolio.group(0) if portfolio else None}


# ---------------------------------------------------------------------------------------------- query
def _keep(item: Mapping[str, Any], methods: Iterable[str] | None, portfolio_id: str | None) -> bool:
    if str(item.get("run_id", "")).startswith("cap#") or not item.get("run_id"):
        return False
    if portfolio_id and item.get("portfolio_id") != portfolio_id:
        return False
    if methods:
        present = item.get("methods") or {}
        requested = item.get("requested_methods") or []
        return any(m in present or m in requested for m in methods)
    return True


def latest_runs(table: Any, environment: str, *, limit: int = DEFAULT_LIMIT, methods: Iterable[str] | None = None,
                since: str | None = None, until: str | None = None, portfolio_id: str | None = None,
                trigger: str | None = None, max_pages: int = _MAX_PAGES) -> RunsPage:
    """Newest-first run items from the ``recent`` index: bounded paging until ``limit`` kept items."""
    limit = max(1, min(MAX_LIMIT, int(limit)))
    methods = list(methods or [])
    condition = "#env = :env"
    values: dict[str, Any] = {":env": environment}
    if since and until:
        condition += " AND started_key BETWEEN :lo AND :hi"
        values.update({":lo": since, ":hi": until})
    elif since:
        condition += " AND started_key >= :lo"
        values[":lo"] = since
    elif until:
        condition += " AND started_key < :hi"
        values[":hi"] = until
    filtered = bool(methods or portfolio_id or trigger)
    kwargs: dict[str, Any] = {"IndexName": INDEX, "KeyConditionExpression": condition, "ExpressionAttributeNames": {"#env": "env"},
                              "ExpressionAttributeValues": values, "ScanIndexForward": False, "Limit": min(100, limit * 4) if filtered else limit}
    page = RunsPage()
    for _ in range(max_pages):
        response = table.query(**kwargs)
        for item in response.get("Items") or []:
            item = plain(item)
            if _keep(item, methods, portfolio_id) and (not trigger or item.get("trigger") == trigger):
                if len(page.items) >= limit:
                    page.more = True
                    return page
                page.items.append(item)
        key = response.get("LastEvaluatedKey")
        if not key:
            return page
        if len(page.items) >= limit:
            page.more = True
            return page
        kwargs["ExclusiveStartKey"] = key
    page.more = True
    return page


def get_run(table: Any, run_id: str) -> list[dict[str, Any]]:
    """Every portfolio item of one run (``Query`` on the partition key)."""
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": "run_id = :r", "ExpressionAttributeValues": {":r": run_id}}
    for _ in range(_MAX_PAGES):
        response = table.query(**kwargs)
        items.extend(plain(i) for i in response.get("Items") or [])
        if not response.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items


# ---------------------------------------------------------------------------------------------- render
def _pct(weight: Any) -> str:
    return f"{float(weight) * 100:.2f}%"


def _targets(targets: Mapping[str, Any] | None, evidence: list[Any]) -> str | None:
    rows = [(k, v) for k, v in (targets or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not rows:
        return None
    rows.sort(key=lambda kv: (-float(kv[1]), kv[0]))
    shown = rows[:_TARGETS_SHOWN]
    evidence.extend(v for _, v in shown)
    text = ", ".join(f"{k} {_pct(v)}" for k, v in shown)
    if len(rows) > len(shown):
        evidence.append(len(rows) - len(shown))
        text += f" and {len(rows) - len(shown)} more"
    return text


def _method_line(method: str, entry: Mapping[str, Any], evidence: list[Any]) -> tuple[str, str | None]:
    status = entry.get("status") or "not_recorded"
    parts = [f"{METHOD_LABELS.get(method, method)} ({method}): {status}"]
    decision = entry.get("decision_id")
    if decision:
        parts.append(f"decision {decision}")
    for key, label in (("completed_session", "completed session"), ("formation_at", "formation")):
        if entry.get(key):
            evidence.append(str(entry[key]))
            parts.append(f"{label} {entry[key]}")
    news = entry.get("news") or {}
    if isinstance(news, Mapping) and news.get("source_day"):
        evidence.append(str(news["source_day"]))
        parts.append(f"news source day {news['source_day']}")
    if entry.get("error_code"):
        parts.append(f"error {entry['error_code']}")
    targets = _targets(entry.get("targets"), evidence)
    if targets:
        parts.append("targets " + targets)
    return "; ".join(parts), decision


def render_runs(items: list[Mapping[str, Any]], request: Mapping[str, Any], *, more: bool = False, heading: str | None = None) -> tuple[str, dict[str, Any]]:
    """(answer text, evidence of every printed figure and date) for newest-first run items."""
    evidence: list[Any] = []
    methods = request.get("methods") or None
    scope = []
    if methods:
        scope.append(", ".join(METHOD_LABELS[m] for m in methods))
    if request.get("portfolio_id"):
        scope.append("portfolio " + request["portfolio_id"])
    if request.get("trigger"):
        scope.append(request["trigger"] + " runs")
    if request.get("period"):
        evidence.append(request["period"].split(" ", 1)[1])
        scope.append(request["period"])
    suffix = (" (" + "; ".join(scope) + ")") if scope else ""
    if not items:
        return ("No recommendation runs were found in the run log" + suffix + ". I have not inferred any recommendation; "
                "ask for a new PPO or traditional recommendation, or wait for the next scheduled run."), {"values": evidence}
    limit = int(request.get("limit") or DEFAULT_LIMIT)
    evidence.extend([len(items), limit])
    lines = [heading or f"Latest {len(items)} recommendation run record(s) from the run log, newest first{suffix}:"]
    requested = request.get("requested")
    if isinstance(requested, int) and requested > MAX_LIMIT:
        evidence.extend([requested, MAX_LIMIT])
        lines.append(f"(You asked for {requested}; at most {MAX_LIMIT} are listed per request.)")
    decisions: list[str] = []
    for index, item in enumerate(items, 1):
        evidence.extend([index, str(item.get("started_at", "")), str(item.get("run_id", ""))])
        lines.append("")
        lines.append(f"{index}. Run {item.get('run_id')} for {item.get('portfolio_id')}: started {item.get('started_at', 'not_recorded')}, "
                     f"trigger {item.get('trigger', 'not_recorded')}, status {item.get('status', 'not_recorded')}."
                     + (f" Error {item['error_code']}." if item.get("error_code") else ""))
        entries = item.get("methods") or {}
        order = [m for m in METHODS if m in entries] + sorted(m for m in entries if m not in METHODS)
        for method in order:
            if methods and method not in methods:
                continue
            text, decision = _method_line(method, entries[method] or {}, evidence)
            lines.append("   - " + text)
            if decision:
                decisions.append(decision)
        for method in item.get("requested_methods") or []:
            if method not in entries and (not methods or method in methods):
                lines.append(f"   - {METHOD_LABELS.get(method, method)} ({method}): not recorded")
    if more:
        lines.append("")
        lines.append("Older run records may exist; ask with a larger count or a date to see more.")
    lines.append("")
    if decisions:
        lines.append(f"Each decision id refers to the full immutable stored decision. Ask to explain, compare or evaluate a listed id, for example \"explain {decisions[0]}\".")
    else:
        lines.append("None of the listed runs issued a decision, so there is no decision id to explain or evaluate.")
    return "\n".join(lines), {"values": evidence}


# ---------------------------------------------------------------------------------------------- reader
class RunLogReader:
    """Read-only access to the run log for the hosted agent. The table name is resolved lazily
    (SSM) and only a successful resolution is cached, so a later publication takes effect."""

    def __init__(self, environment: str, resolve_table: Callable[[], Any], *, ttl_seconds: float = 300.0) -> None:
        self.environment = environment
        self._resolve = resolve_table
        self._ttl = ttl_seconds
        self._cached: tuple[float, Any] | None = None

    def _table(self) -> Any:
        if self._cached and time.monotonic() - self._cached[0] < self._ttl:
            return self._cached[1]
        try:
            table = self._resolve()
        except RunLogUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - reported as unavailable, never as data
            raise RunLogUnavailable(_error_code(exc)) from None
        if table is None:
            raise RunLogUnavailable("NOT_CONFIGURED")
        self._cached = (time.monotonic(), table)
        return table

    def latest(self, **kwargs: Any) -> RunsPage:
        table = self._table()
        try:
            return latest_runs(table, self.environment, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RunLogUnavailable(_error_code(exc)) from None


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return re.sub(r"[^A-Za-z0-9_.-]", "", str(code))[:64] or "UNAVAILABLE"
    return type(exc).__name__[:64]


def ssm_table_resolver(ssm: Any, parameter: str, table_factory: Callable[[str], Any]) -> Callable[[], Any]:
    """Resolve the table name from SSM; an absent parameter means the feature is not deployed."""

    def resolve() -> Any:
        try:
            name = ssm.get_parameter(Name=parameter)["Parameter"]["Value"].strip()
        except Exception as exc:  # noqa: BLE001
            code = _error_code(exc)
            raise RunLogUnavailable("NOT_CONFIGURED" if code == "ParameterNotFound" else code) from None
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", name):
            raise RunLogUnavailable("INVALID_TABLE_SETTING")
        return table_factory(name)

    return resolve


# ---------------------------------------------------------------------------------------------- agent
def _unavailable(code: str) -> str:
    return (f"The recommendation run log is unavailable ({code}). No run or recommendation data is shown, "
            "and none has been inferred. Ask again later, or ask for a specific stored decision by its id.")


def runs_plan(state: Mapping[str, Any], reader: RunLogReader | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The deterministic run-log answer for "latest N recommendations/runs/policies" requests."""
    messages = state.get("messages", [])
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), None)
    if user is None or any(b.get("tool_request") for b in user.get("content", [])):
        return None
    text = " ".join(b["text"] for b in user.get("content", []) if isinstance(b.get("text"), str))
    request = parse_runs_request(text, now=now)
    if request is None:
        return None
    workflow: dict[str, Any] = {"kind": "recommendation_runs", "mode": "recommendation_runs",
                                "request": {k: v for k, v in request.items() if k not in ("since", "until")}}
    if reader is None:
        return {"workflow": {**workflow, "evidence": {"values": []}}, "calls": [], "clarification": _unavailable("NOT_CONFIGURED"), "degraded": "run_log_unavailable"}
    try:
        page = reader.latest(limit=request["limit"], methods=request["methods"], since=request["since"], until=request["until"], portfolio_id=request["portfolio_id"])
    except RunLogUnavailable as exc:
        return {"workflow": {**workflow, "evidence": {"values": []}}, "calls": [], "clarification": _unavailable(exc.code), "degraded": "run_log_unavailable"}
    text, evidence = render_runs(page.items, request, more=page.more)
    return {"workflow": {**workflow, "evidence": evidence}, "calls": [], "clarification": text}
