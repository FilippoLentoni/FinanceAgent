"""Select actual prior issued states without mistaking repeated price closes for time."""
from __future__ import annotations

import re
from datetime import datetime


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _recommendation(row):
    return row.get('recommendation') or row


def _news_formation(rec):
    news = (rec.get('diagnostics') or {}).get('news_evidence') or {}
    if not isinstance(news, dict):
        return None
    proof = news.get('provenance') or {}
    if not isinstance(proof, dict):
        return None
    lineage = proof.get('lineage') or {}
    if not isinstance(lineage, dict):
        return None
    formed = _time(rec.get('decision_at'))
    declared = _time(news.get('decision_time') or news.get('formation_time'))
    central = _time(lineage.get('central_available_at'))
    source = _time(lineage.get('source_available_at'))
    checksum = proof.get('document_checksum') or news.get('corpus_checksum')
    protocol = news.get('feature_protocol')
    if (news.get('consumed') is not True or proof.get('availability_mode') != 'platform'
            or formed is None or declared != formed or central is None or central > formed
            or source is not None and source > formed or not isinstance(checksum, str)
            or re.fullmatch(r'sha256:[0-9a-f]{64}', checksum) is None or not isinstance(protocol, str) or not protocol):
        return None
    return formed, checksum, protocol


def _reported_model_identity(rec):
    identity = {key: rec[key] for key in ('policy_artifact_checksum', 'policy_source_run_id', 'export_run_id', 'configuration_id', 'settings') if rec.get(key) is not None}
    diagnostics = rec.get('diagnostics') or {}
    if diagnostics.get('solver') is not None:
        identity['solver'] = diagnostics['solver']
    return identity or None


def previous_decision(rows: list[dict], method: str, current: dict) -> dict | None:
    """Accept an older price date or an independently retained eligible news formation.

    Serving/receipt timestamps do not establish a new state. On an unchanged close,
    both source documents must be centrally eligible at their declared formations;
    the older formation and its actual document checksum must differ. A repeated
    call or renamed decision for the same frozen state cannot become yesterday.
    """
    rec = _recommendation(current)
    current_pid = current.get('portfolio_id') or (rec.get('portfolio_state') or {}).get('portfolio_id')
    current_universe = {row.get('instrument_id') for row in rec.get('target_weights', [])}
    current_day = current.get('reference_date') or rec.get('as_of')
    for row in rows:
        old = _recommendation(row)
        strategy = row.get('algorithm') or row.get('strategy') or old.get('strategy')
        old_pid = row.get('portfolio_id') or (old.get('portfolio_state') or {}).get('portfolio_id')
        universe = {weight.get('instrument_id') for weight in old.get('target_weights', [])}
        day = row.get('reference_date') or old.get('as_of')
        if (strategy != method or not day or not current_day or old_pid and current_pid and old_pid != current_pid
                or universe and current_universe and universe != current_universe):
            continue
        if day < current_day:
            return row
        if day == current_day:
            previous, present = _news_formation(old), _news_formation(rec)
            identity = _reported_model_identity(rec)
            if (previous and present and previous[0] < present[0] and previous[1] != present[1]
                    and previous[2] == present[2] and identity is not None and _reported_model_identity(old) == identity):
                return row
    return None
