"""Deterministic claim check (FA-PRV-05; task 3.5)."""

from __future__ import annotations

from finplan_agent.graph.claim_check import REMOVED, claim_check

RESULTS = [{"plan_version_id": "pv_01JABCDEFGHJKMNPQRSTVWXYZ1", "expected_return": 0.0612, "weights": [{"w": 0.6}, {"w": 0.4}], "nav": 104250.5, "as_of": "2026-10-07", "revision": 3}]


def test_supported_figures_pass_with_rounding_and_percentages():
    text = "Plan pv_01JABCDEFGHJKMNPQRSTVWXYZ1 (revision 3) expects 0.06, i.e. 6.12% or 6.1%; weights 60% and 0.4; NAV 104,250.5 as of 2026-10-07."
    res = claim_check(text, RESULTS)
    assert res.passed and res.text == text and res.checked >= 7


def test_planted_unsupported_figures_are_removed():
    text = "Expected return 7.5% with a sum of 1.0 and NAV 99,000 on 2026-10-01."
    res = claim_check(text, RESULTS)
    assert not res.passed and set(res.unsupported) == {"7.5%", "1.0", "99,000", "2026-10-01"}
    assert res.text.count(REMOVED) == 4 and "7.5" not in res.text


def test_identifiers_and_hashes_are_not_figures():
    res = claim_check("Run run_01JABCDEFGHJKMNPQRSTVWXYZ3 checksum sha256:" + "ab" * 32, [])
    assert res.passed and res.checked == 0


def test_numbers_only_from_current_turn_results():
    assert not claim_check("The weight is 0.6.", []).passed


def test_numbered_list_markers_are_formatting_but_financial_figures_are_checked():
    text = '1. Weight 60%.\n2) NAV 104,250.5.\n10. Expected return 7.5%.'
    res = claim_check(text, RESULTS)
    assert res.unsupported == ['7.5%']
    assert res.text.startswith('1. Weight 60%.\n2) NAV 104,250.5.\n10.')
    assert res.checked == 3


def test_exact_evidence_versions_in_metadata_do_not_authorize_financial_figures():
    evidence=[{'contract_version': '1.5.0', 'domain_schema_version': '1.0', 'implementation': {'numpy_version': '2.5.3'}}]
    metadata='{"contract_version": "1.5.0", "domain_schema_version": "1.0", "numpy_version": "2.5.3"}'
    assert claim_check(metadata, evidence).passed
    result=claim_check(metadata+'; return 1.5% and volatility 2.5.', evidence)
    assert result.unsupported==['1.5%', '2.5']
    assert result.text.startswith(metadata)
    assert not claim_check(metadata, []).passed
    assert not claim_check('{"contract_version": "1.5.1"}', evidence).passed
    assert not claim_check('{"version": "9.9%"}', [{'version': '9.9%'}]).passed
