"""Deployed beta/gamma explanation checks with REAL payloads sent to the deployed agent as the
``ci_test`` principal (add-explanation-workflows FA-EV-02/06/10 deployed parts; lesson L5). Skipped
offline; the stage runner fails the suite when fewer than one test executed.

Until the FinanceLambdasTool explanation read tools (EX-OQ-9) and the FinanceModel evidence job types
(GAP-E1) are deployed, a well-formed explanation reaches the Gateway and ends with a contract error
(``DEPENDENCY_UNAVAILABLE`` for a tool that is not offered, ``NOT_FOUND`` for an unknown version);
once they are deployed, the same request returns a contract explanation result. Either way the
response is never ``INTERNAL`` and never a model-generated number without evidence.
"""

from __future__ import annotations

from finplan_agent.core.ids import new_session_id
from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed

_ULID = "01JABCDEFGHJKMNPQRSTVWXY"


def _final(events):
    assert isinstance(events, list) and events and events[-1]["type"] == "final", events
    return events[-1]


def test_malformed_explanation_is_a_contract_validation_error():
    env = deployed()
    status, events = env.invoke_agent({"explanation": {"type": "not-a-workflow"}}, new_session_id())
    final = _final(events)
    assert status == 200 and final["status"] == "failed"
    assert final["error"]["code"] == "VALIDATION_FAILED" and final["error"]["details"]["pointer"] == "/explanation/type"


def test_recommendation_change_explanation_reaches_the_gateway_as_the_caller():
    env = deployed()
    req = {"type": "recommendation_change", "plan_version_id": f"pv_{_ULID}02", "compare_to_plan_version_id": f"pv_{_ULID}01"}
    status, events = env.invoke_agent({"explanation": req}, new_session_id())
    final = _final(events)
    assert status == 200
    assert any(e.get("type") == "progress" and e.get("stage") == "explain_prepare" for e in events), "explanations are not enabled in this environment"
    if final["answer"] and final["answer"].get("explanation"):
        doc = final["answer"]["explanation"]
        assert doc["evidence"] and all(e["checksum"].startswith("sha256:") for e in doc["evidence"])
        assert doc["narrative_status"] in ("generated", "unavailable", "budget_exceeded")
    else:
        assert final["error"]["code"] in ("DEPENDENCY_UNAVAILABLE", "NOT_FOUND", "FORBIDDEN"), final["error"]
    if env.env == "beta":
        assert final["usage"].get("provider_kind") in (None, "fixture")
