"""Synthesized IaC (offline): every post-synth gate passes, plus targeted synth tests per requirement
(FA-POL-07, FA-GW-01, FA-GW-05, FA-SS-04, FA-PRV-08, FA-PL-01) and the deploy-lesson regressions
(L1 no CDKToolkit, L3 digest-pinned artifact, L6 no budget / 30-day logs / enforced role names)."""

from __future__ import annotations

import json
import re

import pytest

from infra.stacks import naming as n
from scripts.infra_gates import GateContext, environment_problems, run_gates, runtime_role_problems
from tests.unit.infra.conftest import resources

#: A syntactically valid, obviously fake account ID built at run time (no 12-digit literal in files).
FAKE_ACCOUNT = "0" * 12
OTHER_ACCOUNT = "1" * 12

pytestmark = pytest.mark.synth
ENVS = ("beta", "gamma", "prod")


def test_every_post_synth_gate_passes(assembly):
    ctx = GateContext(assembly)
    results = run_gates(ctx)
    assert {g: p for g, p in results.items() if p} == {}
    gaps = " ".join(ctx.notes.get("ownership", []))
    assert "FA-GAP-RUNTIME-ROLE" in gaps and "FA-GAP-POLICY" in gaps  # reported, not hidden


def test_template_compaction_preserves_values_and_size_gate_checks_packaged_bytes(tmp_path):
    from scripts.synth import _compact_templates

    doc = {"Resources": {"Policy": {"Statement": "// readiness comment\npermit(principal, action, resource);"}}, "Description": "é"}
    path = tmp_path / "BetaAgent.template.json"
    path.write_text(json.dumps(doc, indent=8), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    _compact_templates(tmp_path)
    assert json.loads(path.read_text()) == doc
    assert path.read_bytes() == (json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert manifest.read_bytes() == b"{}\n"
    assert not run_gates(GateContext(tmp_path), ["template-size"])["template-size"]
    path.write_text(json.dumps({"Description": "x" * (1024 * 1024)}))
    assert run_gates(GateContext(tmp_path), ["template-size"])["template-size"]


def test_ownership_gate_still_fails_on_an_unlisted_resource(assembly, tmp_path):
    import shutil

    dst = tmp_path / "asm"
    shutil.copytree(assembly, dst)
    path = next(p for p in dst.rglob("*.template.json") if p.name.startswith("BetaAgent"))
    t = json.loads(path.read_text())
    t["Resources"]["Rogue"] = {"Type": "AWS::SQS::Queue", "Properties": {"Tags": [{"Key": "logical-role", "Value": "agent-runtime"}]}}
    path.write_text(json.dumps(t))
    assert run_gates(GateContext(dst), ["ownership"])["ownership"]


_STREAM_POLICY = {"PolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": ["dynamodb:GetItem", "dynamodb:GetRecords", "dynamodb:GetShardIterator"], "Resource": "*"}]}}

#: One planted violation per deploy lesson whose guard is a post-synth gate: (gate, resource or None, raw text).
_LESSON_VIOLATIONS = {
    "L1-cdk-toolkit-role": ("no-cdk-bootstrap", None, "arn:aws:iam::{acct}:role/cdk-hnb659fds-deploy-role-{acct}-us-east-2"),
    "L1-cdk-assets-bucket": ("no-cdk-bootstrap", None, "cdk-hnb659fds-assets-{acct}-us-east-2"),
    "L2-stream-actions": ("dynamodb-policies", {"Type": "AWS::DynamoDB::Table", "Properties": {"ResourcePolicy": _STREAM_POLICY}}, None),
    "L6-budget": ("no-budget", {"Type": "AWS::Budgets::Budget", "Properties": {"Budget": {"BudgetType": "COST", "TimeUnit": "MONTHLY"}}}, None),
    "L6-log-retention": ("log-retention", {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": 731}}, None),
    # not a deploy lesson: the live-permission scan (task 1.4, ENV-05) must also reject its violation
    "ENV05-live-permission": ("live-perm-scan-synth", {"Type": "AWS::IAM::ManagedPolicy", "Properties": {"PolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["payments:*"], "Resource": "*"}]}}}, None),
}


@pytest.mark.parametrize("case", sorted(_LESSON_VIOLATIONS))
def test_lesson_gates_fail_on_a_planted_violation(assembly, tmp_path, case):
    """Regression for the real-deploy lessons L1, L2 and L6: each gate rejects its planted violation."""
    import shutil

    gate, resource, text = _LESSON_VIOLATIONS[case]
    dst = tmp_path / "asm"
    shutil.copytree(assembly, dst)
    path = next(p for p in dst.rglob("*.template.json") if p.name.startswith("BetaAgent"))
    t = json.loads(path.read_text())
    if resource is not None:
        t["Resources"]["Planted"] = resource
    else:
        t.setdefault("Outputs", {})["Planted"] = {"Value": text.format(acct=FAKE_ACCOUNT)}
    path.write_text(json.dumps(t))
    assert run_gates(GateContext(assembly), [gate])[gate] == []  # the real assembly is clean
    assert run_gates(GateContext(dst), [gate])[gate], f"{gate} accepted the planted {case} violation"


# ------------------------------------------------------------------ identity (FA-POL-07)
@pytest.mark.parametrize("env", ENVS)
def test_one_pool_per_environment_admin_created_users_and_groups(templates, env):
    t = templates[f"identity:{env}"]
    pools = resources(t, "AWS::Cognito::UserPool")
    assert len(pools) == 1
    pool = next(iter(pools.values()))["Properties"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    assert pool["UserPoolName"] == f"finplan-{env}-financeagent-users"
    groups = {r["Properties"]["GroupName"] for r in resources(t, "AWS::Cognito::UserPoolGroup").values()}
    assert groups == {"viewer", "researcher", "plan_editor", "plan_publisher", "ci_test"}
    assert pool.get("DeletionProtection") == ("ACTIVE" if env == "prod" else "INACTIVE")


def test_clients_pkce_public_and_ci_client_credentials_with_secret_by_name(templates):
    t = templates["identity:beta"]
    clients = {r["Properties"]["ClientName"]: r["Properties"] for r in resources(t, "AWS::Cognito::UserPoolClient").values()}
    pkce = clients["finplan-beta-financeagent-users-pkce"]
    ci = clients["finplan-beta-financeagent-ci-test"]
    assert pkce.get("GenerateSecret") in (False, None) and pkce["AllowedOAuthFlows"] == ["code"]
    assert ci["GenerateSecret"] is True and ci["AllowedOAuthFlows"] == ["client_credentials"]
    assert any("/ci_test" in json.dumps(s) for s in ci["AllowedOAuthScopes"]) and len(ci["AllowedOAuthScopes"]) == 1
    assert not any("ci_test" in json.dumps(s) for s in pkce["AllowedOAuthScopes"])
    secret = next(iter(resources(t, "AWS::SecretsManager::Secret").values()))["Properties"]
    assert secret["Name"] == "finplan/beta/financeagent/ci-test-client"
    assert "ClientSecret" in json.dumps(secret["SecretString"])  # deploy-time GetAtt, never a literal


@pytest.mark.parametrize("env", ENVS)
def test_gateway_and_runtime_bound_to_the_same_environments_pool(templates, env):
    assert environment_problems(templates[f"agent:{env}"], env, env) == []
    text = json.dumps(templates[f"agent:{env}"])
    for other in ENVS:
        if other != env:
            assert f"finplan-{other}-financeagent-identity" not in text


# ------------------------------------------------------------------ gateway (FA-GW-01/02/04)
def test_targets_are_conditional_on_resolved_references_and_never_literal(templates):
    t = templates["agent:gamma"]
    targets = resources(t, "AWS::BedrockAgentCore::GatewayTarget")
    from infra.stacks.tool_schemas import classical_tools, primary_tools

    assert len(targets) == len(primary_tools()) + len(classical_tools()) >= 12
    for _lid, r in targets.items():
        lam = r["Properties"]["TargetConfiguration"]["Mcp"]["Lambda"]
        assert lam["LambdaArn"]["Ref"].startswith("Target") and r["Condition"].startswith("Register")
        assert r["Properties"]["CredentialProviderConfigurations"] == [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}]
        assert "_" not in r["Properties"]["Name"]
        tool = lam["ToolSchema"]["InlinePayload"][0]
        assert tool["InputSchema"]["Type"] == "object" and "$ref" not in json.dumps(tool)
    pat = t["Parameters"]["TargetGetPlanVersionArn"]["AllowedPattern"]
    assert re.match(pat, f"arn:aws:lambda:us-east-2:{FAKE_ACCOUNT}:function:finplan-gamma-financelambdastool-get-plan-version:current")
    assert not re.match(pat, f"arn:aws:lambda:us-east-2:{FAKE_ACCOUNT}:function:finplan-beta-financelambdastool-get-plan-version:current")
    assert re.match(pat, "none")


@pytest.mark.parametrize("env", ENVS)
def test_both_gateway_targets_accept_a_boolean_paper_confirmation(templates, env):
    targets = resources(templates[f"agent:{env}"], "AWS::BedrockAgentCore::GatewayTarget")
    schemas = [r["Properties"]["TargetConfiguration"]["Mcp"]["Lambda"]["ToolSchema"]["InlinePayload"][0]
               for r in targets.values()]
    resolutions = [s["InputSchema"] for s in schemas if s["Name"] == "resolve_portfolio_decision"]
    assert len(resolutions) == 2
    for schema in resolutions:
        assert schema["Properties"]["confirmed_by_user"] == {"Type": "boolean", "Description": "[const true]"}
        assert "confirmed_by_user" in schema["Required"]


def test_policies_follow_their_target_and_the_engine_enforces(templates):
    t = templates["agent:beta"]
    gw = next(iter(resources(t, "AWS::BedrockAgentCore::Gateway").values()))["Properties"]
    assert gw["PolicyEngineConfiguration"]["Mode"] == "ENFORCE" and gw["AuthorizerType"] == "CUSTOM_JWT" and gw["ProtocolType"] == "MCP"
    assert gw["RoleArn"]["Fn::Join"][1][-1] == ":role/finplan-beta-financeagent-gateway-service-role"
    pols = resources(t, "AWS::BedrockAgentCore::Policy")
    assert pols
    for r in pols.values():
        stmt = r["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Sub"][0]
        target = next(d for d in r["DependsOn"] if d.startswith(("Target", "ClassicalTarget")))
        assert r["Condition"] == "Register" + target.removeprefix("Classical")[len("Target") :]
        assert "${GatewayArn}" in stmt and "AgentCore::OAuthUser" in stmt
    allow_publish = next(r for lid, r in pols.items() if lid.startswith("PolicyPublishPlanVersion"))
    assert '"plan_publisher' in allow_publish["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Sub"][0]
    assert "ci_test" not in allow_publish["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Sub"][0]


def _active_references(value, enabled):
    """Resolve conditional dependency branches, like CloudFormation does for sparse catalogs."""
    if isinstance(value, list):
        return set().union(*(_active_references(v, enabled) for v in value))
    if not isinstance(value, dict):
        return set()
    if "Fn::If" in value:
        condition, yes, no = value["Fn::If"]
        return _active_references(yes if condition in enabled else no, enabled)
    if "Fn::GetAtt" in value:
        return {value["Fn::GetAtt"][0]}
    return set().union(*(_active_references(v, enabled) for v in value.values()))


@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("catalog", ("all", "alternating", "single", "none"))
def test_catalog_updates_are_serial_and_policies_wait_for_all_enabled_targets(templates, env, catalog):
    t = templates[f"agent:{env}"]
    targets = resources(t, "AWS::BedrockAgentCore::GatewayTarget")
    policies = resources(t, "AWS::BedrockAgentCore::Policy")
    conditions = sorted({r["Condition"] for r in targets.values()})
    enabled = set(conditions if catalog == "all" else conditions[::2] if catalog == "alternating" else ["RegisterGetPortfolioHistory"] if catalog == "single" else [])
    active_targets = {lid: r for lid, r in targets.items() if r["Condition"] in enabled}
    active_policies = {lid: r for lid, r in policies.items() if r["Condition"] in enabled}
    dependencies = {}
    groups = {}
    for gateway in ("Gateway", "ClassicalGateway"):
        group = {lid: r for lid, r in targets.items() if r["Properties"]["GatewayIdentifier"] == {"Fn::GetAtt": [gateway, "GatewayIdentifier"]}}
        active = {lid: r for lid, r in group.items() if r["Condition"] in enabled}
        groups[gateway] = set(active)
        previous = None
        for lid, resource in sorted(active.items(), key=lambda pair: pair[1]["Properties"]["Name"]):
            refs = _active_references(resource["Properties"]["Description"], enabled)
            assert refs == ({previous} if previous else set())
            # Hard dependencies on an optional target would suppress enabled
            # later targets whenever the predecessor is configured as none.
            assert not (set(resource.get("DependsOn", [])) & set(targets))
            dependencies[lid] = refs
            previous = lid
        for lid, resource in active_policies.items():
            if resource["Properties"]["PolicyEngineId"] != {"Fn::GetAtt": ["ClassicalToolPolicyEngine" if gateway == "ClassicalGateway" else "ToolPolicyEngine", "PolicyEngineId"]}:
                continue
            statement, substitutions = resource["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Sub"]
            assert "// Registered catalog readiness: ${CatalogTargetIds}" in statement
            refs = _active_references(substitutions["CatalogTargetIds"], enabled)
            assert refs == ({previous} if previous else set())
            direct = set(resource.get("DependsOn", [])) & set(targets)
            assert len(direct) == 1 and direct <= set(active)
            dependencies[lid] = refs | direct
    # Simulate a legal CloudFormation schedule. Concurrent writes to the same
    # catalog and policy creation against a partial catalog must be impossible.
    completed = set()
    while len(completed) < len(dependencies):
        ready = {lid for lid, deps in dependencies.items() if lid not in completed and deps <= completed}
        assert ready, "the deployment dependency graph contains a cycle"
        for gateway, group in groups.items():
            assert len(ready & group) <= 1, (gateway, "parallel catalog updates")
        for lid in ready & set(active_policies):
            gateway = "ClassicalGateway" if lid.startswith("Classical") else "Gateway"
            assert groups[gateway] <= completed, "policy started before its complete catalog was ready"
        completed.update(ready)
    assert completed == set(active_targets) | set(active_policies)


# ------------------------------------------------------------------ runtime (FA-PRV-08, FA-POL-01, L3)
def test_runtime_contract_wiring(templates):
    t = templates["agent:beta"]
    rt = next(iter(resources(t, "AWS::BedrockAgentCore::Runtime").values()))["Properties"]
    assert rt["NetworkConfiguration"] == {"NetworkMode": "PUBLIC"} and rt["ProtocolConfiguration"] == "HTTP"
    assert rt["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == ["Authorization"]
    assert set(rt["EnvironmentVariables"]) == {"FINPLAN_ENV", "FINPLAN_RELEASE_ID", "FINPLAN_MEMORY_ID", "FINPLAN_GATEWAY_URL", "FINPLAN_CLASSICAL_GATEWAY_URL"}
    assert rt["AgentRuntimeName"] == "finplan_beta_financeagent"
    pat = t["Parameters"]["ImageUri"]["AllowedPattern"]
    good = FAKE_ACCOUNT + ".dkr.ecr.us-east-2.amazonaws.com/finplan-shared-financeagent-runtime-images@sha256:" + "a" * 64
    assert re.match(pat, good)
    assert not re.match(pat, good.split("@")[0] + ":latest")  # never a tag (lesson L3)


def test_runtime_role_scope_and_conditional_bedrock_grant(templates):
    t = templates["agent:prod"]
    assert runtime_role_problems(t, "prod") == []
    pol = t["Resources"]["RuntimeBedrockInvoke"]
    assert pol["Condition"] == "HasBedrockGrant" and t["Parameters"]["BedrockInvokeArns"]["Default"] == "none"
    role = next(r for r in resources(t, "AWS::IAM::Role").values() if r["Properties"]["RoleName"] == "finplan-prod-financeagent-runtime-role")
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"} and "aws:SourceAccount" in json.dumps(trust["Condition"])


def test_runtime_role_gate_rejects_a_lambda_grant(templates):
    t = json.loads(json.dumps(templates["agent:beta"]))
    role = next(r for r in resources(t, "AWS::IAM::Role").values() if r["Properties"]["RoleName"].endswith("-runtime-role"))
    role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"].append({"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": "*"})
    assert any("FA-POL-01" in p for p in runtime_role_problems(t, "x"))


def test_runtime_log_group_keeps_30_days_and_role_cannot_create_log_groups(templates):
    t = templates["agent:beta"]
    lg = next(iter(resources(t, "AWS::Logs::LogGroup").values()))["Properties"]
    assert lg["RetentionInDays"] == 30 and "AgentRuntimeId" in json.dumps(lg["LogGroupName"]) and "-DEFAULT" in json.dumps(lg["LogGroupName"])
    role = next(r for r in resources(t, "AWS::IAM::Role").values() if r["Properties"]["RoleName"].endswith("-runtime-role"))
    assert "logs:CreateLogGroup" not in json.dumps(role)
    assert "logs:PutLogEvents" in json.dumps(role)  # explicit role writes its own logs


# ------------------------------------------------------------------ memory (FA-SS-04)
def test_memory_short_term_only(templates):
    mem = next(iter(resources(templates["agent:gamma"], "AWS::BedrockAgentCore::Memory").values()))["Properties"]
    assert "MemoryStrategies" not in mem and mem["EventExpiryDuration"] == 30


# ------------------------------------------------------------------ tooling / pipeline (FA-PL-01, L1, L6)
def test_gateway_service_roles_exist_per_environment_in_tooling(templates):
    t = templates["tooling"]
    roles = {r["Properties"]["RoleName"]: r for r in resources(t, "AWS::IAM::Role").values()}
    for env in ENVS:
        r = roles[n.gateway_role_name(env)]
        tags = {x["Key"]: x["Value"] for x in r["Properties"]["Tags"]}
        assert tags["environment"] == env and tags["logical-role"] == "gateway-service-role"
        assert f"finplan-{env}-permission-boundary" in json.dumps(r["Properties"]["PermissionsBoundary"])
        assert f"function:finplan-{env}-financelambdastool-*" in json.dumps(r)


def test_classical_catalog_and_policy_are_separate_from_the_existing_ppo_gateway(templates):
    from infra.stacks.tool_schemas import CLASSICAL_ONLY_TOOLS, CLASSICAL_SHARED_TOOLS, classical_tools, primary_tools

    t = templates["agent:beta"]
    gateways = resources(t, "AWS::BedrockAgentCore::Gateway")
    assert set(gateways) == {"Gateway", "ClassicalGateway"}
    assert gateways["Gateway"]["Properties"]["AuthorizerConfiguration"] == gateways["ClassicalGateway"]["Properties"]["AuthorizerConfiguration"]
    assert gateways["Gateway"]["Properties"]["PolicyEngineConfiguration"] != gateways["ClassicalGateway"]["Properties"]["PolicyEngineConfiguration"]
    actual = {"Gateway": set(), "ClassicalGateway": set()}
    for target in resources(t, "AWS::BedrockAgentCore::GatewayTarget").values():
        props = target["Properties"]
        gateway_id = props["GatewayIdentifier"]["Fn::GetAtt"][0]
        actual[gateway_id].add(props["TargetConfiguration"]["Mcp"]["Lambda"]["ToolSchema"]["InlinePayload"][0]["Name"])
    assert actual["Gateway"] == set(primary_tools())
    assert actual["ClassicalGateway"] == set(classical_tools())
    assert not actual["Gateway"] & CLASSICAL_ONLY_TOOLS
    assert actual["ClassicalGateway"] <= CLASSICAL_ONLY_TOOLS | CLASSICAL_SHARED_TOOLS
    assert "recommend_portfolio" not in actual["ClassicalGateway"]


def test_classical_bootstrap_grants_extend_beta_only(templates):
    roles = {r["Properties"]["RoleName"]: r["Properties"] for r in resources(templates["tooling"], "AWS::IAM::Role").values()}
    for env in ENVS:
        role = roles[n.gateway_role_name(env)]
        trust = role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
        assert ("classical-gateway" in json.dumps(trust)) == (env == "beta")
        assert ("classical_tools" in json.dumps(role["Policies"])) == (env == "beta")
        if env != "beta":
            # No shared-bootstrap permission changes in Gamma/prod.
            assert trust["StringEquals"] == {"aws:SourceAccount": {"Ref": "AWS::AccountId"}}
            assert trust["ArnLike"]["aws:SourceArn"]["Fn::Join"][1][-1] == f":gateway/finplan-{env}-financeagent-gateway-*"


def test_only_human_resolution_and_beta_paid_research_receive_identity_token_header(templates):
    from infra.stacks.tool_schemas import CLASSICAL_IDENTITY_HEADER

    for env in ENVS:
        for logical, target in resources(templates[f"agent:{env}"], "AWS::BedrockAgentCore::GatewayTarget").items():
            metadata = target["Properties"].get("MetadataConfiguration")
            if logical in {"TargetResolvePortfolioDecision", "ClassicalTargetResolvePortfolioDecision"} or env == "beta" and logical in {"ClassicalTargetRunPortfolioResearch", "ClassicalTargetRunRecursiveImprovement"}:
                assert metadata == {"AllowedRequestHeaders": [CLASSICAL_IDENTITY_HEADER]}
            else:
                assert metadata is None


def test_environment_gate_rejects_missing_or_overbroad_identity_header_propagation(templates):
    from copy import deepcopy

    t = deepcopy(templates["agent:beta"])
    t["Resources"]["ClassicalTargetRunPortfolioResearch"]["Properties"].pop("MetadataConfiguration")
    assert any("identity header propagation" in p for p in environment_problems(t, "test", "beta"))
    t = deepcopy(templates["agent:beta"])
    t["Resources"]["TargetRecommendPortfolio"]["Properties"]["MetadataConfiguration"] = {"AllowedRequestHeaders": ["X-Finplan-User-Token"]}
    assert any("identity header propagation" in p for p in environment_problems(t, "test", "beta"))


def test_pipeline_wiring(templates):
    t = templates["tooling"]
    p = next(iter(resources(t, "AWS::CodePipeline::Pipeline").values()))["Properties"]
    assert [s["Name"] for s in p["Stages"]] == ["Source", "Build", "Beta", "Gamma", "Approval", "Prod"]
    beta = next(s for s in p["Stages"] if s["Name"] == "Beta")
    names = [a["Name"] for a in sorted(beta["Actions"], key=lambda a: a["RunOrder"])]
    assert names == ["Resolve", "DeployIdentity", "DeployAgent", "PublishRelease", "IntegrationBetaTests"]
    resolve = next(a for a in beta["Actions"] if a["Name"] == "Resolve")
    assert resolve["Namespace"] == "ResolveBeta"
    deploy_agent = next(a for a in beta["Actions"] if a["Name"] == "DeployAgent")
    overrides = json.loads(deploy_agent["Configuration"]["ParameterOverrides"])
    assert overrides == {"ImageUri": "#{BuildVariables.IMAGE_URI}", "ReleaseId": "#{BuildVariables.RELEASE_ID}"}
    assert len(deploy_agent["Configuration"]["ParameterOverrides"].encode()) <= 1024
    resolved_artifact = resolve["OutputArtifacts"][0]["Name"]
    assert deploy_agent["Configuration"]["TemplateConfiguration"] == f"{resolved_artifact}::agent-parameters.json"
    assert {a["Name"] for a in deploy_agent["InputArtifacts"]} == {"BuildOutput", resolved_artifact}
    build_project = next(r for r in resources(t, "AWS::CodeBuild::Project").values() if r["Properties"]["Name"] == "finplan-shared-financeagent-pipeline-build-project")
    env = build_project["Properties"]["Environment"]
    assert env["Type"] == "ARM_CONTAINER" and env["PrivilegedMode"] is True
    assert "connection" not in json.dumps(p).lower() or "codeconnection-ref" in json.dumps(t)


def test_metadata_artifact_exception_rejects_executable_substitution_and_other_consumers(templates):
    from copy import deepcopy

    from scripts.infra_gates import validated_resolve_metadata_template

    template = templates["tooling"]
    projected, problems = validated_resolve_metadata_template(template)
    assert not problems and projected != template

    def actions_for(t, stage="Beta"):
        pipeline = next(iter(resources(t, "AWS::CodePipeline::Pipeline").values()))["Properties"]
        return next(s["Actions"] for s in pipeline["Stages"] if s["Name"] == stage)

    for invalid in ("template", "image", "consumer", "producer"):
        bad = deepcopy(template)
        beta = actions_for(bad)
        deploy = next(a for a in beta if a["Name"] == "DeployAgent")
        if invalid == "template":
            deploy["Configuration"]["TemplatePath"] = "ResolvedBetaConfiguration::replacement.template.json"
        elif invalid == "image":
            deploy["Configuration"]["ParameterOverrides"] = json.dumps({"ImageUri": "arbitrary-later-image", "ReleaseId": "#{BuildVariables.RELEASE_ID}"})
        elif invalid == "consumer":
            actions_for(bad, "Gamma")[0]["InputArtifacts"].append({"Name": "ResolvedBetaConfiguration"})
        else:
            next(a for a in beta if a["Name"] == "Resolve")["InputArtifacts"] = [{"Name": "SourceOutput"}]
        rejected, errors = validated_resolve_metadata_template(bad)
        assert rejected == bad and errors, invalid


def test_no_budget_and_30_day_logs_everywhere(assembly):
    ctx = GateContext(assembly)
    res = run_gates(ctx, ["no-budget", "log-retention", "no-cdk-bootstrap", "budget-roles"])
    assert all(not v for v in res.values()), res
    assert n.budget_enforced_role_names("gamma")[0] == "finplan-gamma-financeagent-runtime-role"
