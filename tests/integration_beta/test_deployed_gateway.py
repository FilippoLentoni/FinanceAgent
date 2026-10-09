"""Deployed Gateway, identity and release checks with REAL payloads (FA-GW-01/02/05, FA-POL-02/03/05/07,
FA-PL-03/04, FA-PRV-01/09/14). The stage runner runs this suite in beta and again in gamma, as the stage
role with real credentials and the environment's ci_test token; skipped offline."""

from __future__ import annotations

import json
import os

from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed

READ_ONLY = ("recommend_portfolio", "get_performance_evidence", "get_publication", "list_publications", "list_executions", "describe_capabilities", "get_plan", "get_plan_version", "list_plan_versions", "query_market_data", "get_job_status", "get_experiment_result")


def test_release_manifest_and_published_references():
    env = deployed()
    manifest = env.manifest()
    assert manifest["repo"] == "financeagent" and manifest["environment"] == env.env
    assert env.param(env.own("release", "current-release-id")) == manifest["release_id"]
    if os.environ.get("FINPLAN_RELEASE_ID"):
        assert manifest["release_id"] == os.environ["FINPLAN_RELEASE_ID"]
    for key in (
        "user-pool-ref",
        "authorizer-metadata-ref",
        "ci-test-client-secret-ref",
        "runtime-ref",
        "gateway-endpoint-ref",
        "gateway-principal-ref",
        "policy-digest",
        "gateway-targets",
        "explanation-provider",
        "budget-enforced-role-names",
    ):
        assert key in manifest["outputs"], key
        assert manifest["outputs"][key].startswith(f"/finplan/{env.env}/financeagent/")
    assert env.param(env.own("agent", "gateway-principal-ref")) == f"finplan-{env.env}-financeagent-gateway-service-role"
    roles = env.param(env.own("config", "budget-enforced-role-names")).split(",")
    assert roles[0] == f"finplan-{env.env}-financeagent-runtime-role"


def test_authorizer_metadata_points_to_this_environments_pool():
    env = deployed()
    meta = json.loads(env.param(env.names.authorizer_metadata_ref))
    pool = env.param(env.names.user_pool_ref)
    assert meta["issuer"].endswith("/" + pool) and pool in meta["discovery_url"]
    assert len(meta["allowed_clients"]) == 2 and meta["token_endpoint"].endswith("/oauth2/token")


def test_beta_runs_the_configured_hosted_provider():
    env = deployed()
    if env.env == "beta":
        assert env.param(env.own("config", "explanation-provider")) == "bedrock"


def test_unauthenticated_gateway_call_is_rejected():
    env = deployed()
    body = {"jsonrpc": "2.0", "id": "x", "method": "tools/list", "params": {}}
    assert env.raw_gateway_post(body, None) in (401, 403)
    assert env.raw_gateway_post(body, "not-a-token") in (401, 403)


def test_gateway_lists_exactly_the_registered_tools_the_ci_principal_may_use():
    env = deployed()
    registered = set(env.registered_targets())
    assert registered, "no tool is registered in this environment"
    listed = {t.name for t in env.gateway().list_tools()}
    # read-only tools are listed for the ci principal; argument-limited grants (production_strategy get)
    # may be listed too; no other state-changing tool is ever offered (FA-POL-03/05)
    assert registered & set(READ_ONLY) <= listed <= registered & (set(READ_ONLY) | {"production_strategy"}), (listed, registered)


def test_describe_capabilities_through_the_gateway():
    out = deployed().gateway().call_tool("describe_capabilities", {})
    assert out.ok, out.error
    assert isinstance(out.result, dict)


def test_state_changing_tool_is_denied_for_the_read_only_principal():
    env = deployed()
    registered = env.registered_targets()
    tool = next((t for t in ("publish_plan_version", "create_override_version", "submit_experiment") if t in registered), None)
    if tool is None:
        assert "describe_capabilities" in registered
        return
    gw = env.gateway()
    gw.list_tools()
    out = gw.call_tool(tool, {})
    assert not out.ok
