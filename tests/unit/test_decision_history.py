"""Actual news formation chronology can advance while completed prices do not."""

from copy import deepcopy

import pytest

from finplan_agent.core.decision_history import previous_decision


def issued(day, checksum, decision_id):
    cutoff = f"2026-10-{day}T13:00:00Z"
    return {
        "decision_id": decision_id,
        "algorithm": "ppo",
        "portfolio_id": "synthetic-paper",
        "recommendation": {
            "strategy": "ppo",
            "as_of": "2026-10-16",
            "decision_at": cutoff,
            "policy_artifact_checksum": "sha256:" + "c" * 64,
            "portfolio_state": {"portfolio_id": "synthetic-paper", "revision": 1},
            "target_weights": [{"instrument_id": "SYN_A", "weight": 0.5}],
            "diagnostics": {
                "news_evidence": {
                    "consumed": True,
                    "feature_protocol": "gdelt-gkg-v2-premarket-organization-tone-v1",
                    "decision_time": cutoff,
                    "provenance": {"availability_mode": "platform", "document_checksum": "sha256:" + checksum * 64, "lineage": {"source_available_at": f"2026-10-{day}T12:40:00Z", "central_available_at": f"2026-10-{day}T12:45:00Z"}},
                }
            },
        },
    }


def test_sunday_selects_real_saturday_formation_despite_unchanged_friday_close():
    saturday, sunday = issued("17", "a", "saturday"), issued("18", "b", "sunday")
    retry = deepcopy(sunday)
    retry["decision_id"] = "same-state-new-receipt"
    retry["recommendation"]["serving_time"] = "2026-10-18T19:00:00Z"
    assert previous_decision([retry, saturday], "ppo", sunday) == saturday
    assert previous_decision([retry], "ppo", sunday) is None


@pytest.mark.parametrize("invalid", ["future_central", "future_source", "same_document", "same_formation", "naive_cutoff", "mismatched_declared_cutoff", "wrong_portfolio", "wrong_universe", "unconsumed", "local_only", "changed_protocol", "changed_actor"])
def test_ineligible_or_replayed_same_close_state_is_not_a_prior_daily_recommendation(invalid):
    old, current = issued("17", "a", "old"), issued("18", "b", "current")
    rec = old["recommendation"]
    news = rec["diagnostics"]["news_evidence"]
    proof = news["provenance"]
    if invalid == "future_central":
        proof["lineage"]["central_available_at"] = "2026-10-17T13:00:01Z"
    elif invalid == "future_source":
        proof["lineage"]["source_available_at"] = "2026-10-17T13:00:01Z"
    elif invalid == "same_document":
        proof["document_checksum"] = "sha256:" + "b" * 64
    elif invalid == "same_formation":
        rec["decision_at"] = news["decision_time"] = current["recommendation"]["decision_at"]
    elif invalid == "naive_cutoff":
        rec["decision_at"] = news["decision_time"] = "2026-10-17T13:00:00"
    elif invalid == "mismatched_declared_cutoff":
        news["decision_time"] = "2026-10-17T12:00:00Z"
    elif invalid == "wrong_portfolio":
        old["portfolio_id"] = "other-paper"
    elif invalid == "wrong_universe":
        rec["target_weights"][0]["instrument_id"] = "SYN_B"
    elif invalid == "unconsumed":
        news["consumed"] = False
    elif invalid == "local_only":
        proof["availability_mode"] = "retrospective_archive_audit"
    elif invalid == "changed_protocol":
        news['feature_protocol'] = 'different-source-protocol'
    elif invalid == "changed_actor":
        rec['policy_artifact_checksum'] = 'sha256:' + 'd' * 64
    assert previous_decision([old], "ppo", current) is None


@pytest.mark.parametrize('change', ['protocol', 'actor', 'price_only'])
def test_an_older_price_date_does_not_make_a_different_news_model_a_controlled_prior(change):
    old, current = issued('17', 'a', 'old'), issued('18', 'b', 'current')
    old['recommendation']['as_of'] = '2026-10-15'
    assert previous_decision([old], 'ppo', current) == old
    if change == 'protocol':
        old['recommendation']['diagnostics']['news_evidence']['feature_protocol'] = 'earlier-protocol'
    elif change == 'actor':
        old['recommendation']['policy_artifact_checksum'] = 'sha256:' + 'd' * 64
    else:
        old['recommendation']['diagnostics']['news_evidence']['consumed'] = False
    assert previous_decision([old], 'ppo', current) is None
