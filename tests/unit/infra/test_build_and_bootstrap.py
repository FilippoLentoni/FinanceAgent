"""Build stage and bootstrap (offline, fakes and moto): a failing gate produces no artifact, the release
carries a digest-pinned Runtime image (L3), the bootstrap deploys only the account-level stacks without
CDKToolkit (L1), reads the shared ~/.finplan/bootstrap.json, reuses the platform's CodeConnection and
publishes the Gateway principals before FinanceLambdasTool's Gateway-facing release (GAP-3)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from finplan_contracts.bootstrap import BootstrapStop
from moto import mock_aws

from scripts import bootstrap as bs
from scripts import build_stage as bst
from scripts.release import ReleaseInfo

#: A syntactically valid, obviously fake account ID built at run time (no 12-digit literal in files).
FAKE_ACCOUNT = "0" * 12
OTHER_ACCOUNT = "1" * 12

COMMIT = "a" * 40


# ------------------------------------------------------------------ build stage
def _copy(assembly: Path):
    def synth_fn(out: Path) -> Path:
        shutil.copytree(assembly, out)
        return out

    return synth_fn


def _image(release_id: str, commit: str):
    return SimpleNamespace(repository="finplan-shared-financeagent-runtime-images", digest="sha256:" + "1" * 64, base_images={})


def test_build_emits_a_digest_pinned_release_and_variables(assembly, tmp_path):
    out = tmp_path / "build-output"
    variables = tmp_path / "build.env"
    info = bst.run_build(bst.ROOT, out, source_commit=COMMIT, region="us-east-2", account=FAKE_ACCOUNT, image_fn=_image, synth_fn=_copy(assembly), run_tests=False, variables=variables, log=lambda _m: None)
    stored = ReleaseInfo.load(out / "release-info.json")
    assert stored.release_id == info.release_id and stored.image_uri.endswith("@sha256:" + "1" * 64)
    assert (out / "cdk.out" / "manifest.json").is_file() and (out / "scripts" / "stage_runner.py").is_file() and (out / "policy" / "tool-policy.yaml").is_file()
    text = variables.read_text()
    assert f"RELEASE_ID={info.release_id}" in text and "IMAGE_URI=" in text


def test_failing_post_gate_produces_no_artifact(assembly, tmp_path):
    def bad_synth(out: Path) -> Path:
        shutil.copytree(assembly, out)
        p = next(x for x in out.rglob("*.template.json") if x.name.startswith("BetaAgent"))
        t = json.loads(p.read_text())
        t["Resources"]["Budget"] = {"Type": "AWS::Budgets::Budget", "Properties": {}}
        p.write_text(json.dumps(t))
        return out

    out = tmp_path / "build-output"
    with pytest.raises(bst.BuildFailed, match="post-synth"):
        bst.run_build(bst.ROOT, out, source_commit=COMMIT, region="us-east-2", account=FAKE_ACCOUNT, image_fn=_image, synth_fn=bad_synth, run_tests=False, log=lambda _m: None)
    assert not out.exists()


def test_image_without_digest_produces_no_artifact(assembly, tmp_path):
    out = tmp_path / "build-output"
    with pytest.raises(bst.BuildFailed, match="digest"):
        bst.run_build(bst.ROOT, out, source_commit=COMMIT, region="us-east-2", account=FAKE_ACCOUNT, image_fn=lambda r, c: SimpleNamespace(repository="r", digest=None), synth_fn=_copy(assembly), run_tests=False, log=lambda _m: None)
    assert not out.exists()


def test_configuration_gate(tmp_path):
    assert bst.config_problems() == []
    root = tmp_path / "repo"
    shutil.copytree(bst.ROOT / "config", root / "config")
    beta = json.loads((root / "config" / "beta.json").read_text())
    beta["explanation"]["provider"] = "bedrock"
    beta["explanation"]["rates"] = {"input_per_1k_usd": 1}
    (root / "config" / "beta.json").write_text(json.dumps(beta))
    probs = " ".join(bst.config_problems(root))
    assert "fixture provider only" in probs and "no rates" in probs


# ------------------------------------------------------------------ bootstrap
def test_bootstrap_assembly_has_only_tooling_stacks_and_no_cdk_bootstrap(assembly, tmp_path):
    dst = bs.bootstrap_assembly(assembly, tmp_path / "boot")
    manifest = json.loads((dst / "manifest.json").read_text())
    stacks = sorted(a["properties"]["stackName"] for a in manifest["artifacts"].values() if a["type"] == "aws:cloudformation:stack")
    assert stacks == sorted(bs.BOOTSTRAP_STACKS)
    assert bs.cdk_bootstrap_references(dst) == []
    # Regression (first FinanceAgent bootstrap): every file the manifest names must be copied,
    # including each artifact's additionalMetadataFile, or the CDK CLI fails with ENOENT.
    for art in manifest["artifacts"].values():
        props = art.get("properties") or {}
        for rel in (props.get("templateFile"), props.get("file"), art.get("additionalMetadataFile")):
            if rel:
                assert (dst / rel).exists(), rel


def test_bootstrap_refuses_a_missing_assembly(tmp_path):
    with pytest.raises(BootstrapStop, match="not synthesized"):
        bs.bootstrap_assembly(tmp_path / "nothing", tmp_path / "boot")


def test_local_config_is_the_shared_file_and_never_inside_the_repo(tmp_path):
    shared = tmp_path / "bootstrap.json"
    shared.write_text(json.dumps({"account_id": FAKE_ACCOUNT, "primary_region": "us-east-2", "codeconnection_arn": "x", "repo": "financialplanning", "budget_notification_email": "hidden"}))
    data = bs.read_local_config(shared, environ={}, overlay=None)
    assert data == {"account_id": FAKE_ACCOUNT, "primary_region": "us-east-2", "codeconnection_arn": "x"}
    inside = bs.ROOT / "config" / "shared.json"
    with pytest.raises(BootstrapStop, match="inside the repository"):
        bs.read_local_config(inside, environ={}, overlay=None)
    assert str(bs.SHARED_CONFIG).endswith(".finplan/bootstrap.json")


@mock_aws
def test_codeconnection_is_reused_from_the_platform_reference():
    ssm = boto3.client("ssm", region_name="us-east-2")
    with pytest.raises(BootstrapStop, match="no CodeConnection"):
        bs.load_bootstrap_config({"account_id": FAKE_ACCOUNT, "primary_region": "us-east-2"}, ssm)
    ssm.put_parameter(Name=bs.PLATFORM_CONNECTION_PARAMETER, Value="reused-connection", Type="String")
    cfg = bs.load_bootstrap_config({"account_id": FAKE_ACCOUNT, "primary_region": "us-east-2"}, ssm)
    assert cfg.codeconnection_arn == "reused-connection" and cfg.repo == "financeagent" and cfg.pipeline_name == "finplan-shared-financeagent-pipeline"


@mock_aws
def test_deployer_deploys_only_tooling_then_publishes_gateway_principals(tmp_path):
    ssm = boto3.client("ssm", region_name="us-east-2")
    cmds = []
    dep = bs.ToolingDeployer(tmp_path, ssm, region="us-east-2", dry_run_passed=False, runner=lambda cmd, **kw: cmds.append(cmd) or SimpleNamespace(returncode=0), out=lambda _m: None)
    with pytest.raises(BootstrapStop):
        dep(["finplan-beta-financeagent-agent"])
    dep(list(bs.BOOTSTRAP_STACKS))
    assert cmds and "aws-cdk@2" in " ".join(cmds[0]) and f"{bs.TOOLING_STACK_NAME}:SourceDryRunPassed=false" in cmds[0]
    for env in ("beta", "gamma", "prod"):
        assert ssm.get_parameter(Name=f"/finplan/{env}/financeagent/agent/gateway-principal-ref")["Parameter"]["Value"] == f"finplan-{env}-financeagent-gateway-service-role"
    shared = ssm.get_parameter(Name=bs.SHARED_ENFORCED_ROLES_PARAMETER)["Parameter"]["Value"]
    assert shared == "finplan-shared-financeagent-pipeline-role,finplan-shared-financeagent-pipeline-build-project-role"
    names = [p["Name"] for p in ssm.describe_parameters()["Parameters"]]
    assert not any("budget-allocation" in x for x in names)  # FinanceAgent never writes a budget parameter


def test_model_access_report_is_read_only():
    class B:
        def get_inference_profile(self, inferenceProfileIdentifier):  # noqa: N803
            return {"status": "ACTIVE"}

    status = bs.report_model_access(B(), out=lambda _m: None)
    assert set(status) == {"beta", "gamma", "prod"} and all(v == "ACTIVE" for v in status.values())
