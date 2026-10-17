"""Present the actual news source and cutoff without treating research as consumption."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

GDELT_PROTOCOL = 'gdelt-gkg-v1-organization-tone-v1'


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def summarize(news: dict) -> dict:
    protocol = news.get('feature_protocol')
    proof = news.get('provenance') if isinstance(news.get('provenance'), dict) else {}
    lineage = proof.get('lineage') or {}
    cutoff = news.get('decision_time') or news.get('formation_time')
    result = {'consumed': news.get('consumed') is True, 'feature_protocol': protocol,
              'decision_cutoff': cutoff, 'training_corpus_checksum': news.get('training_corpus_checksum') or news.get('training_news_corpus_checksum'),
              'corpus_checksum': news.get('corpus_checksum')}
    if protocol != GDELT_PROTOCOL:
        result['source_kind'] = 'declared_research_features' if news.get('consumed') else 'not_consumed'
        return result
    result.update(source_kind='GDELT_released_numeric_machine_metadata', source_day=proof.get('source_day'),
                  source_available_at=lineage.get('source_available_at'), central_available_at=lineage.get('central_available_at'),
                  retrieved_at=lineage.get('retrieved_at'), availability_mode=proof.get('availability_mode'),
                  availability_basis=lineage.get('availability_basis'), document_checksum=proof.get('document_checksum'),
                  rights_status=(lineage.get('rights') or {}).get('rights_status'), rights_scope=(lineage.get('rights') or {}).get('scope'),
                  attribution=lineage.get('attribution'),
                  limitations=news.get('limitations') or proof.get('disclosures') or [],
                  same_morning_paid_headlines='Separate research collection; consumption by this numeric-archive policy is not established.',
                  sentiment_interpretation='Organization-level tone is not calibrated stock sentiment; VOO uses Vanguard mentions rather than a constituent-weighted news basket.')
    formed, released = _time(cutoff), _time(lineage.get('source_available_at'))
    if formed and released:
        result['source_availability_lag_hours'] = round((formed - released).total_seconds() / 3600, 3)
    if formed and isinstance(proof.get('source_day'), str):
        try:
            result['archive_day_lag_days'] = (formed.astimezone(UTC).date() - date.fromisoformat(proof['source_day'])).days
        except ValueError:
            pass
    return result


def render(news: dict) -> str:
    """Runtime prose uses only original producer figures, leaving derived lag to reports."""
    if not news.get('consumed'):
        return 'Approved news feature consumption is not established by this recommendation.'
    if news.get('feature_protocol') != GDELT_PROTOCOL:
        cutoff = news.get('formation_time') or news.get('decision_time') or 'unavailable'
        return ('This recommendation consumes the declared centrally stored news features at cutoff ' + str(cutoff)
                + '. Source and feature identities remain in the frozen evidence; this establishes neither improved returns nor news causality.')
    details = summarize(news)
    return ('This recommendation consumes released GDELT numeric machine metadata under feature protocol ' + GDELT_PROTOCOL
            + '. Archive source day: ' + str(details.get('source_day') or 'unavailable')
            + '; source available at: ' + str(details.get('source_available_at') or 'unavailable')
            + '; centrally available at: ' + str(details.get('central_available_at') or 'unavailable')
            + '; decision cutoff: ' + str(details.get('decision_cutoff') or 'unavailable')
            + '. This is a daily UTC archive, with publication and ingestion lag; it does not establish consumption of separately collected same-morning paid research headlines. '
            + details['sentiment_interpretation'] + ' Numerical feature consumption does not establish improved returns or causal attribution.')
