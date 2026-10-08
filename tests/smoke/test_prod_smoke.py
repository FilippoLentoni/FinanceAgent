"""Read-only prod smoke (FA-PL-04 prod, FA-PRV-11): real payloads to the deployed prod Gateway and Runtime
with the prod ci_test principal; only read-only tools; no plan version, publication, execution or job is
created. Skipped offline."""

from __future__ import annotations

from finplan_agent.core.ids import new_session_id
from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed


def test_prod_gateway_lists_and_describes_read_only():
    env = deployed()
    gw = env.gateway()
    names = {t.name for t in gw.list_tools()}
    assert "describe_capabilities" in names
    assert not names & {"publish_plan_version", "create_override_version", "submit_experiment", "refresh_market_data", "validate_plan_version"}
    assert gw.call_tool("describe_capabilities", {}).ok


def test_prod_agent_describe():
    env = deployed()
    status, doc = env.invoke_agent({"action": "describe", "stream": False}, new_session_id())
    assert status == 200 and doc["framework"] == "langgraph" and doc["environment"] == "prod"
    assert doc["release_id"] == env.manifest()["release_id"]


def test_prod_manifest_records_the_approval():
    m = deployed().manifest()
    assert m.get("approved_by") and m.get("approved_at")
