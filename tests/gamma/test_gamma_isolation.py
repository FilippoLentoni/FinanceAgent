"""Gamma isolation, drift and parity checks against the DEPLOYED gamma environment (FA-GW-01, FA-PL-05,
FA-POL-06; spec agent-release-pipeline "Gamma isolation verified"). Read-only control-plane calls as the
gamma stage role plus real MCP payloads; skipped offline."""

from __future__ import annotations

import json

from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed


def _all_items(call, key: str, **kwargs):
    """Every page of an AgentCore control-plane list call (pages hold at most 10 items)."""
    items, token = [], None
    while True:
        page = call(**kwargs, **({"nextToken": token} if token else {}))
        items += page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return items


def _gateway_id(env) -> str:
    url = env.param(env.names.gateway_endpoint_ref)
    return url.split("//", 1)[1].split(".", 1)[0]


def test_every_target_resolves_to_this_environments_lambdas_only():
    env = deployed()
    ctl = env.control()
    gid = _gateway_id(env)
    targets = _all_items(ctl.list_gateway_targets, "items", gatewayIdentifier=gid)
    assert targets, "the gateway has no targets"
    registered = env.registered_targets()
    seen = set()
    for item in targets:
        tgt = ctl.get_gateway_target(gatewayIdentifier=gid, targetId=item["targetId"])
        arn = tgt["targetConfiguration"]["mcp"]["lambda"]["lambdaArn"]
        assert f":function:finplan-{env.env}-financelambdastool-" in arn
        for other in ("beta", "gamma", "prod"):
            if other != env.env:
                assert f"finplan-{other}-" not in arn
        tool = tgt["name"].replace("-", "_")
        assert registered.get(tool) == arn
        ssm_ref = env.param(f"/finplan/{env.env}/financelambdastool/lambda/{tool.replace('_', '-')}-arn")
        assert ssm_ref == arn, tool
        seen.add(tool)
    assert seen == set(registered)


def test_deployed_policy_matches_the_release_manifest_digest():
    env = deployed()
    digest = env.param(env.own("agent", "policy-digest"))
    stack = env.session.client("cloudformation").describe_stacks(StackName=f"finplan-{env.env}-financeagent-agent")["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs") or []}
    assert outputs["PolicyDigest"] == digest
    engine_id = outputs["PolicyEngineArn"].rsplit("/", 1)[-1]
    policies = _all_items(env.control().list_policies, "policies", policyEngineId=engine_id)
    names = {p["name"] for p in policies}
    from infra.stacks.agent import _policy_resource_name
    from infra.stacks.tool_policy import allowed_roles

    expected = {_policy_resource_name(env.env, t, "allow") for t in env.registered_targets() if allowed_roles(t)}
    assert expected <= names, sorted(expected - names)
    assert all(p.get("status") in (None, "ACTIVE") for p in policies)


def test_gateway_and_runtime_are_bound_to_the_gamma_pool():
    env = deployed()
    gw = env.control().get_gateway(gatewayIdentifier=_gateway_id(env))
    meta = json.loads(env.param(env.names.authorizer_metadata_ref))
    jwt = gw["authorizerConfiguration"]["customJWTAuthorizer"]
    assert jwt["discoveryUrl"] == meta["discovery_url"]
    assert sorted(jwt.get("allowedClients", [])) == sorted(meta["allowed_clients"])
