"""Gateway tool policy matrix (FA-POL-03, FA-POL-05, FA-POL-06) and the contract-schema projection to the
Gateway ``SchemaDefinition`` subset (FA-GW-04)."""

from __future__ import annotations

import json

import pytest

from infra.stacks.tool_policy import PolicyError, allowed_roles, decide, load_policy, policy_digest, render, validate_policy
from infra.stacks.tool_schemas import GATEWAY_TYPES, contract_tools, project, to_cfn, tool_definition

READ = ["describe_capabilities", "get_plan", "get_plan_version", "list_plan_versions", "query_market_data", "get_job_status", "get_experiment_result"]
CI_SCOPE = ["finplan-agent/ci_test"]


# ------------------------------------------------------------------ policy matrix
@pytest.mark.parametrize("tool", READ)
def test_read_tools_allowed_for_every_role_and_the_ci_principal(tool):
    for g in ("viewer", "researcher", "plan_editor", "plan_publisher"):
        assert decide(tool, groups=[g])
    assert decide(tool, scopes=CI_SCOPE)
    assert not decide(tool)  # default deny: no group, no scope


@pytest.mark.parametrize(
    "tool,allowed",
    [
        ("publish_plan_version", {"plan_publisher"}),
        ("create_override_version", {"plan_editor"}),
        ("validate_plan_version", {"plan_editor", "plan_publisher"}),
        ("submit_experiment", {"researcher"}),
        ("refresh_market_data", {"researcher"}),
    ],
)
def test_state_changing_tools_only_for_their_roles(tool, allowed):
    for g in ("viewer", "researcher", "plan_editor", "plan_publisher", "ci_test"):
        assert decide(tool, groups=[g]) == (g in allowed), g
    assert not decide(tool, scopes=CI_SCOPE)


def test_decision_never_depends_on_the_channel():
    import inspect

    assert "channel" not in inspect.signature(decide).parameters  # FA-POL-03 parity by construction


def test_live_arguments_and_live_tools_are_denied():
    assert not decide("submit_experiment", groups=["researcher"], arguments={"mode": "live"})
    assert decide("submit_experiment", groups=["researcher"], arguments={"mode": "paper"})
    doc = json.loads(json.dumps(load_policy()))
    doc["roles"]["viewer"]["tools"].append("execute_trade")
    assert validate_policy(doc)
    assert not decide("execute_trade", groups=["viewer"], doc=doc)
    assert all(p.tool != "execute_trade" for p in render(["execute_trade"], doc=doc))


def test_render_one_permit_per_permitted_tool_and_forbids_for_denied_arguments():
    tools = contract_tools()
    pols = render(tools, {t: tool_definition(t).input_schema.get("properties", {}) for t in tools})
    permits = {p.tool for p in pols if p.kind == "permit"}
    assert permits == {t for t in tools if allowed_roles(t)}
    forbids = render(["submit_experiment"], {"submit_experiment": {"mode": {"type": "string"}}})
    assert [p.kind for p in forbids] == ["permit", "forbid"]
    assert 'context.input has mode && (context.input.mode == "live")' in forbids[1].statement
    publish = next(p for p in pols if p.tool == "publish_plan_version")
    assert '\\"plan_publisher\\"' in publish.statement and "ci_test" not in publish.statement
    assert 'action == AgentCore::Action::"publish-plan-version___publish_plan_version"' in publish.statement


def test_policy_digest_is_stable_and_changes_with_the_policy():
    tools = contract_tools()
    a = policy_digest(render(tools))
    assert a == policy_digest(render(list(reversed(tools)))) and a.startswith("sha256:")
    doc = json.loads(json.dumps(load_policy()))
    doc["roles"]["viewer"]["tools"].remove("get_plan")
    assert policy_digest(render(tools, doc=doc)) != a


def test_policy_file_validates(tmp_path):
    assert validate_policy(load_policy()) == []
    bad = tmp_path / "bad.yaml"
    bad.write_text("roles: {viewer: {tools: [place_order]}}\ndenied_tool_words: [order]\n")
    with pytest.raises(PolicyError):
        load_policy(bad)


# ------------------------------------------------------------------ schema projection (FA-GW-04)
def _walk(schema):
    yield schema
    for sub in (schema.get("properties") or {}).values():
        yield from _walk(sub)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_every_contract_tool_projects_to_the_gateway_subset():
    tools = contract_tools()
    assert "describe_capabilities" in tools and len(tools) >= 10
    for t in tools:
        d = tool_definition(t)
        for schema in (d.input_schema, d.output_schema):
            assert schema["type"] == "object"
            for node in _walk(schema):
                assert set(node) <= {"type", "description", "properties", "required", "items"}
                assert node["type"] in GATEWAY_TYPES
        stem = "recommend-portfolio-invocation" if t == "recommend_portfolio" else t.replace('_', '-')
        assert d.input_schema_id.endswith(f"tools/{stem}-request.json")


def test_pattern_constraint_moves_to_the_description():
    s = project("tools/get-plan-version-request")
    pv = s["properties"]["plan_version_id"]
    assert pv["type"] == "string" and "pattern" in pv["description"] and "pv_" in pv["description"]
    assert s["required"] == ["plan_version_id"]


def test_cfn_casing():
    c = to_cfn({"type": "object", "properties": {"a_b": {"type": "array", "items": {"type": "string"}}}, "required": ["a_b"]})
    assert c == {"Type": "object", "Properties": {"a_b": {"Type": "array", "Items": {"Type": "string"}}}, "Required": ["a_b"]}
