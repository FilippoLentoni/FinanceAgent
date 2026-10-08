"""Reference resolution, compatibility gate, release publishing, ledger and stage-runner checks with moto
SSM and fakes (FA-GW-02, FA-GW-03, FA-GW-05, FA-PL-03, FA-PL-04, FA-PRV-09, FA-PRV-14; lessons L3, L5)."""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from infra.stacks.tool_schemas import contract_tools
from scripts import release as rel
from scripts import stage_runner

#: A syntactically valid, obviously fake account ID built at run time (no 12-digit literal in files).
FAKE_ACCOUNT = "0" * 12
OTHER_ACCOUNT = "1" * 12

ACCOUNT = FAKE_ACCOUNT
TOKEN_URL = "https://fake-domain.auth.us-east-2.amazoncognito.com/oauth2/" + "token"
REGION = "us-east-2"
FLT = "financelambdastool"


def _arn(env, tool, account=ACCOUNT):
    return f"arn:aws:lambda:{REGION}:{account}:function:finplan-{env}-{FLT}-{tool.replace('_', '-')}:current"


def _catalog(env, tools):
    from finplan_contracts.schemas import load_store

    store = load_store()
    out = []
    for t in tools:
        stem = t.replace("_", "-")
        out.append(
            {
                "name": t,
                "description": t,
                "input_schema_id": store.get(f"tools/{stem}-request").id,
                "output_schema_id": store.get(f"tools/{stem}-response").id,
                "state_changing": False,
                "role_class": "reader",
                "lambda_ref_parameter": f"/finplan/{env}/{FLT}/lambda/{stem}-arn",
            }
        )
    return {"environment": env, "release_id": rel.mint_release_id(), "contract_version": "1.0.0", "tools": out, "synthetic": True}


def _seed(ssm, env, tools, *, majors=(1,), arn_env=None, account=ACCOUNT):
    manifest = {"repo": FLT, "environment": env, "served_contract_majors": list(majors), "contract_version": f"{majors[0]}.0.0", "outputs": {"tool-catalog": f"/finplan/{env}/{FLT}/contract/tool-catalog"}}
    ssm.put_parameter(Name=f"/finplan/{env}/{FLT}/release/manifest", Value=json.dumps(manifest), Type="String")
    ssm.put_parameter(Name=f"/finplan/{env}/{FLT}/contract/tool-catalog", Value=json.dumps(_catalog(env, tools)), Type="String", Tier="Advanced")
    for t in tools:
        ssm.put_parameter(Name=f"/finplan/{env}/{FLT}/lambda/{t.replace('_', '-')}-arn", Value=_arn(arn_env or env, t, account), Type="String")


@pytest.fixture
def ssm():
    with mock_aws():
        yield boto3.client("ssm", region_name=REGION)


# ------------------------------------------------------------------ identity / digest
def test_release_id_is_a_contract_release_id():
    from finplan_contracts.validate import validate  # noqa: F401

    rid = rel.mint_release_id(datetime(2026, 10, 8, tzinfo=UTC))
    assert re.fullmatch(r"rel_[0-7][0-9A-HJKMNP-TV-Z]{25}", rid)
    assert rel.mint_release_id() != rel.mint_release_id()


def test_assembly_digest_covers_the_image(tmp_path):
    (tmp_path / "manifest.json").write_text("{}")
    a = rel.assembly_digest(tmp_path, "sha256:" + "a" * 64)
    assert a != rel.assembly_digest(tmp_path, "sha256:" + "b" * 64) and a == rel.assembly_digest(tmp_path, "sha256:" + "a" * 64)
    with pytest.raises(rel.ManifestError):
        rel.image_uri(ACCOUNT, REGION, "repo", "latest")


# ------------------------------------------------------------------ compatibility gate + registration
def test_registration_from_the_environments_released_references(ssm):
    _seed(ssm, "gamma", ["describe_capabilities", "get_plan_version"])
    targets, cat_rel, _notes = rel.resolve_targets(ssm, "gamma", account=ACCOUNT, tools=contract_tools(), pinned_major=1)
    assert targets["describe_capabilities"] == _arn("gamma", "describe_capabilities")
    assert targets["get_plan_version"] == _arn("gamma", "get_plan_version")
    assert targets["publish_plan_version"] == "none"  # removed from the catalog -> no target
    assert cat_rel.startswith("rel_")


class _FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 casing
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _seed_pointer(ssm, env, tools, *, account=ACCOUNT):
    """The deployed FinanceLambdasTool format: SSM holds a tool-catalog-pointer to its pipeline store."""
    import hashlib

    _seed(ssm, env, tools, account=account)
    full = _catalog(env, tools)
    body = json.dumps(full).encode()
    bucket, key = f"finplan-shared-{FLT}-pipeline-store-{account}", f"releases/{full['release_id']}/tool-catalog/{env}.json"
    pointer = {"kind": "tool-catalog-pointer", "environment": env, "release_id": full["release_id"], "s3_uri": f"s3://{bucket}/{key}", "sha256": hashlib.sha256(body).hexdigest(), "tools": sorted(tools)}
    ssm.put_parameter(Name=f"/finplan/{env}/{FLT}/contract/tool-catalog", Value=json.dumps(pointer), Type="String", Overwrite=True)
    return _FakeS3({(bucket, key): body}), full


def test_registration_follows_the_deployed_catalog_pointer(ssm):
    s3, full = _seed_pointer(ssm, "beta", ["describe_capabilities", "get_plan"])
    targets, cat_rel, _notes = rel.resolve_targets(ssm, "beta", account=ACCOUNT, tools=contract_tools(), pinned_major=1, s3=s3)
    assert targets["describe_capabilities"] == _arn("beta", "describe_capabilities") and targets["get_plan"] == _arn("beta", "get_plan")
    assert cat_rel == full["release_id"]
    with pytest.raises(rel.DependencyMissing, match="pointer"):
        rel.resolve_targets(ssm, "beta", account=ACCOUNT, tools=contract_tools(), pinned_major=1)  # no S3 client: fail closed


def test_catalog_pointer_into_another_accounts_store_is_refused(ssm):
    s3, _full = _seed_pointer(ssm, "beta", ["describe_capabilities"], account=OTHER_ACCOUNT)
    with pytest.raises(rel.DependencyMissing):
        rel.resolve_targets(ssm, "beta", account=ACCOUNT, tools=contract_tools(), pinned_major=1, s3=s3)


def test_no_tool_release_is_a_dependency_missing_stop(ssm):
    with pytest.raises(rel.DependencyMissing, match="dependency missing"):
        rel.resolve_targets(ssm, "gamma", account=ACCOUNT, tools=contract_tools(), pinned_major=1)


def test_incompatible_contract_major_stops_before_registration(ssm):
    _seed(ssm, "gamma", ["describe_capabilities"], majors=(2,))
    with pytest.raises(rel.DependencyMissing, match="incompatible contract major"):
        rel.resolve_targets(ssm, "gamma", account=ACCOUNT, tools=contract_tools(), pinned_major=1)


def test_cross_environment_or_cross_account_lambda_refs_are_refused(ssm):
    _seed(ssm, "gamma", ["describe_capabilities"], arn_env="prod")
    with pytest.raises(rel.DependencyMissing, match="alias-qualified gamma"):
        rel.resolve_targets(ssm, "gamma", account=ACCOUNT, tools=contract_tools(), pinned_major=1)


def test_cross_account_lambda_ref_is_refused(ssm):
    _seed(ssm, "beta", ["describe_capabilities"], account=OTHER_ACCOUNT)
    with pytest.raises(rel.DependencyMissing):
        rel.resolve_targets(ssm, "beta", account=ACCOUNT, tools=contract_tools(), pinned_major=1)


def test_resolution_variables_are_exported_safely(tmp_path, ssm):
    _seed(ssm, "beta", ["describe_capabilities"])
    res = rel.resolve("beta", ssm=ssm, bedrock=None, account=ACCOUNT, region=REGION)
    v = res.variables()
    assert v["BEDROCK_INVOKE_ARNS"] == "none" and v["TARGET_DESCRIBE_CAPABILITIES"].endswith(":current")
    assert res.registered == ["describe_capabilities"]
    out = tmp_path / "resolved.env"
    rel.write_variables(out, v)
    assert "TARGET_GET_PLAN=none" in out.read_text()
    with pytest.raises(rel.ManifestError):
        rel.write_variables(out, {"X": "a;rm -rf /"})


def test_rollback_reuses_the_recorded_target_set(ssm):
    recorded = {"describe_capabilities": _arn("beta", "describe_capabilities")}
    res = rel.resolve("beta", ssm=ssm, bedrock=None, account=ACCOUNT, region=REGION, recorded_targets=recorded)
    assert res.registered == ["describe_capabilities"]  # no catalog read needed


# ------------------------------------------------------------------ publish
class FakeCfn:
    def __init__(self, env):
        self.env = env

    def describe_stacks(self, StackName):  # noqa: N803
        if StackName.endswith("-identity"):
            outs = {
                "UserPoolId": f"{REGION}_FAKEPOOL",
                "PkceClientId": "fakepkceclient",
                "CiTestClientId": "fakeciclient",
                "DiscoveryUrl": f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_FAKEPOOL/.well-known/openid-configuration",
                "Issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_FAKEPOOL",
                "TokenEndpoint": TOKEN_URL,
                "CiTestClientSecretName": f"finplan/{self.env}/financeagent/ci-test-client",
            }
        else:
            outs = {
                "RuntimeArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/finplan_{self.env}_financeagent-abcdefghij",
                "GatewayUrl": "https://fakegw.gateway.bedrock-agentcore.us-east-2.amazonaws.com/mcp",
                "PolicyDigest": "sha256:" + "c" * 64,
            }
        return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outs.items()], "Parameters": []}]}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):  # noqa: N803
        if kw.get("IfNoneMatch") == "*" and (Bucket, Key) in self.objects:
            raise RuntimeError("PreconditionFailed")
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _info(**kw):
    base = dict(
        release_id=rel.mint_release_id(),
        source_commit="a" * 40,
        artifact_digest="sha256:" + "d" * 64,
        contract_version="1.0.0",
        contract_digest="sha256:" + "e" * 64,
        served_contract_majors=[1],
        region=REGION,
        built_at="2026-10-08T00:00:00Z",
        image_repository="finplan-shared-financeagent-runtime-images",
        image_digest="sha256:" + "f" * 64,
    )
    base["image_uri"] = rel.image_uri(ACCOUNT, REGION, base["image_repository"], base["image_digest"])
    base.update(kw)
    return rel.ReleaseInfo(**base)


def test_publish_writes_references_config_and_a_valid_manifest(ssm):
    s3 = FakeS3()
    targets = {t: "none" for t in contract_tools()} | {"describe_capabilities": _arn("beta", "describe_capabilities")}
    info = _info()
    m = rel.publish_release(info, "beta", ssm=ssm, cfn=FakeCfn("beta"), targets=targets, s3=s3, store_bucket="store")
    get = lambda name: ssm.get_parameter(Name=name)["Parameter"]["Value"]  # noqa: E731
    assert get("/finplan/beta/financeagent/release/current-release-id") == info.release_id
    assert get("/finplan/beta/financeagent/config/explanation-provider") == "fixture"
    assert get("/finplan/beta/financeagent/agent/gateway-principal-ref") == "finplan-beta-financeagent-gateway-service-role"
    assert get("/finplan/beta/financeagent/secret-ref/ci-test-client") == "finplan/beta/financeagent/ci-test-client"
    meta = json.loads(get("/finplan/beta/financeagent/agent/authorizer-metadata-ref"))
    assert set(meta) >= {"discovery_url", "issuer", "allowed_clients", "token_endpoint"}
    assert get("/finplan/beta/financeagent/config/budget-enforced-role-names").split(",")[0] == "finplan-beta-financeagent-runtime-role"
    assert json.loads(get("/finplan/beta/financeagent/agent/gateway-targets"))["describe_capabilities"].endswith(":current")
    assert m["outputs"]["policy-digest"] == "/finplan/beta/financeagent/agent/policy-digest" and m["previous_release_id"] is None
    assert ("store", f"releases/{info.release_id}/targets/beta.json") in s3.objects
    # a second release records the first as previous
    m2 = rel.publish_release(_info(), "beta", ssm=ssm, cfn=FakeCfn("beta"), targets=targets)
    assert m2["previous_release_id"] == info.release_id


def test_operator_rates_are_preserved_never_invented(ssm):
    ssm.put_parameter(Name="/finplan/gamma/financeagent/config/explanation-guards", Value=json.dumps({"rates": {"source": "configured", "retrieved_at": "2026-10-08"}}), Type="String")
    rel.publish_release(_info(), "gamma", ssm=ssm, cfn=FakeCfn("gamma"), targets={"describe_capabilities": "none"})
    guards = json.loads(ssm.get_parameter(Name="/finplan/gamma/financeagent/config/explanation-guards")["Parameter"]["Value"])
    assert guards["rates"]["source"] == "configured" and guards["max_tokens_invocation"] == __import__("json").loads(Path(__file__).resolve().parents[3].joinpath("config", "gamma.json").read_text())["guard_defaults"]["max_tokens_invocation"]


def test_prod_needs_the_approval_and_beta_refuses_bedrock(ssm):
    with pytest.raises(rel.ManifestError, match="approval"):
        rel.publish_release(_info(), "prod", ssm=ssm, cfn=FakeCfn("prod"), targets={})
    cfg = rel.load_env_config("beta") | {"explanation": {"provider": "bedrock", "model_id": "x"}}
    with pytest.raises(rel.ManifestError, match="fixture"):
        rel.planned_parameters(
            "beta",
            _info(),
            {"UserPoolId": "p", "DiscoveryUrl": "d", "Issuer": "i", "PkceClientId": "a", "CiTestClientId": "b", "TokenEndpoint": TOKEN_URL, "RuntimeArn": "r", "GatewayUrl": "g", "PolicyDigest": "sha256:" + "0" * 64},
            cfg,
            targets={},
            existing_guards=None,
        )


def test_ledger_round_trip_and_tamper_detection(tmp_path):
    from scripts.release import fetch_build_output, store_build_output

    out = tmp_path / "build-output"
    (out / "cdk.out").mkdir(parents=True)
    (out / "cdk.out" / "manifest.json").write_text("{}")
    info = _info(artifact_digest=rel.assembly_digest(out / "cdk.out", "sha256:" + "f" * 64))
    (out / "release-info.json").write_text(info.to_json())
    s3 = FakeS3()
    store_build_output(s3, "store", info, out)
    back = fetch_build_output(s3, "store", info.release_id, tmp_path / "rb")
    assert back.rollback is True and back.release_id == info.release_id
    with pytest.raises(RuntimeError):
        store_build_output(s3, "store", info, out)  # write-once ledger


# ------------------------------------------------------------------ stage runner (L3, L5)
def test_precheck_refuses_a_tag_or_source_only_artifact():
    assert stage_runner.precheck_problems("beta", _info()) == []
    assert any("digest-pinned" in p for p in stage_runner.precheck_problems("beta", _info(image_uri=None)))
    assert any("digest-pinned" in p for p in stage_runner.precheck_problems("beta", _info(image_uri="x/y:latest")))


def _junit(path: Path, tests: int, skipped: int) -> None:
    path.write_text(f'<testsuites><testsuite tests="{tests}" failures="0" errors="0" skipped="{skipped}"/></testsuites>')


def test_zero_executed_deployed_tests_fail_the_stage(tmp_path):
    def run(cmd, cwd, env):
        assert env["FINPLAN_TARGET_ENV"] == "beta" and "FINPLAN_OFFLINE_TESTS" not in env
        _junit(Path(next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1]), 4, 4)
        return SimpleNamespace(returncode=0)

    assert stage_runner.tests_action("beta", run=run, environ={"FINPLAN_OFFLINE_TESTS": "1"}, out=lambda _m: None) == 1


def test_executed_deployed_tests_pass_the_stage(tmp_path):
    def run(cmd, cwd, env):
        _junit(Path(next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1]), 4, 0)
        return SimpleNamespace(returncode=0)

    assert stage_runner.tests_action("gamma", run=run, environ={}, out=lambda _m: None) == 0


def test_suite_counts(tmp_path):
    p = tmp_path / "j.xml"
    _junit(p, 5, 2)
    assert stage_runner.suite_counts(p)["executed"] == 3
