"""Foundation regressions: contract pin (CS-04), agent gates (FA-RT-01, FA-RT-03, FA-PRV-11, FA-PRV-14)
and the real-deploy lessons that apply to the agent artifact (L3 image dependencies, L4 regional S3,
L5 offline/deployed split)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ contract pin
def test_contract_pin_verifies():
    from scripts.check_contracts_pin import check

    assert check(ROOT) == []


def test_wrong_digest_fails(tmp_path):
    from scripts.check_contracts_pin import check

    pin = json.loads((ROOT / "contracts-pin.json").read_text())
    pin["sha256"] = "0" * 64
    p = tmp_path / "pin.json"
    p.write_text(json.dumps(pin))
    problems = check(ROOT, pin_path=p)
    assert any("Digest mismatch" in x for x in problems)


def test_pinned_wheel_is_the_newest_platform_build():
    from scripts.check_contracts_pin import _sha256, find_source_wheel

    src = ROOT.parent / "FinancialPlanning" / "vendor" / "finplan-contracts"
    if not src.is_dir():
        pytest.skip("sibling FinancialPlanning checkout not present")
    pin = json.loads((ROOT / "contracts-pin.json").read_text())
    assert _sha256(find_source_wheel(src)) == pin["sha256"]


# ------------------------------------------------------------------ agent gates
def test_repository_passes_agent_gates():
    from scripts.agent_gates import run_all

    assert [str(f) for f in run_all(ROOT)] == []


def _copy_agent(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "agent", root / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", root / "config")
    return root


def test_architecture_check_rejects_other_frameworks(tmp_path):
    from scripts.agent_gates import architecture_findings

    root = _copy_agent(tmp_path)
    (root / "agent" / "finplan_agent" / "alt.py").write_text("from strands import Agent\nimport crewai\n")
    found = architecture_findings(root)
    assert {f.path for f in found} == {"agent/finplan_agent/alt.py"} and len(found) == 2


def test_architecture_check_rejects_second_entry_point(tmp_path):
    from scripts.agent_gates import architecture_findings

    root = _copy_agent(tmp_path)
    (root / "agent" / "finplan_agent" / "other.py").write_text("from bedrock_agentcore.runtime import BedrockAgentCoreApp\napp = BedrockAgentCoreApp()\n")
    assert any("second Runtime entry point" in f.message for f in architecture_findings(root))


def test_model_id_scan_names_the_file(tmp_path):
    from scripts.agent_gates import model_id_findings

    root = _copy_agent(tmp_path)
    fx = json.loads((ROOT / "tests" / "fixtures" / "config" / "provider-configs.json").read_text())
    (root / "agent" / "finplan_agent" / "bad.py").write_text(f"MODEL = {fx['model_ids']['prod_profile']!r}\n")
    (root / "tests" / "fixtures" / "config").mkdir(parents=True)
    (root / "tests" / "fixtures" / "config" / "ok.json").write_text(json.dumps(fx))
    found = model_id_findings(root)
    assert [f.path for f in found] == ["agent/finplan_agent/bad.py"]


def test_websocket_requires_recorded_requirement(tmp_path):
    from scripts.agent_gates import provider_kind_findings, websocket_findings

    root = _copy_agent(tmp_path)
    cfg = json.loads((root / "config" / "gamma.json").read_text())
    cfg["streaming"]["websocket"] = {"enabled": True, "requirement_ref": None}
    (root / "config" / "gamma.json").write_text(json.dumps(cfg))
    assert [f.path for f in websocket_findings(root)] == ["config/gamma.json"]
    cfg["streaming"]["websocket"]["requirement_ref"] = "design.md#D9 voice"
    (root / "config" / "gamma.json").write_text(json.dumps(cfg))
    assert websocket_findings(root) == []
    beta = json.loads((root / "config" / "beta.json").read_text())
    beta["provider_kind_allowed"] = ["fixture", "bedrock"]
    (root / "config" / "beta.json").write_text(json.dumps(beta))
    assert [f.path for f in provider_kind_findings(root)] == ["config/beta.json"]


# ------------------------------------------------------------------ L3: image carries its dependencies
def test_runtime_image_is_arm64_and_built_from_the_lock():
    from scripts.runtime_image import IMAGE_IMPORTS, PLATFORM

    text = (ROOT / "container" / "Dockerfile").read_text()
    assert PLATFORM == "linux/arm64"
    assert "uv sync --frozen --no-dev --no-install-project" in text and "uv sync --frozen --no-dev --no-editable" in text
    assert 'CMD ["python", "-m", "finplan_agent"]' in text and "COPY config/" in text and "USER 10001" in text
    ignore = (ROOT / "container" / "Dockerfile.dockerignore").read_text().splitlines()
    assert ignore[1] == "*" and not any(line.startswith("!tests") for line in ignore)
    for mod in ("finplan_agent.runtime.app", "finplan_contracts", "langgraph.graph", "langgraph_checkpoint_aws", "bedrock_agentcore.runtime", "boto3"):
        assert mod in IMAGE_IMPORTS


def test_image_import_list_resolves_in_the_locked_environment():
    import importlib

    from scripts.runtime_image import IMAGE_IMPORTS

    for mod in IMAGE_IMPORTS:
        importlib.import_module(mod)


def test_failed_image_import_check_pushes_nothing():
    from scripts.runtime_image import ImageError, build_and_push

    calls = []

    def run(cmd, *, cwd, input=None, env=None):  # noqa: A002
        calls.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return "public.ecr.aws/docker/library/python@sha256:" + "a" * 64 + "\nghcr.io/astral-sh/uv@sha256:" + "b" * 64
        if cmd[:2] == ["docker", "run"]:
            raise ImageError("No module named 'langgraph'")
        return ""

    with pytest.raises(ImageError, match="nothing pushed"):
        build_and_push(release_id="rel_01JABCDEFGHJKMNPQRSTVWXYZ0", source_commit="c", account="0" * 12, region="us-east-2", repository="r", ecr=object(), run=run)
    assert not any(c[:2] == ["docker", "push"] for c in calls)
    build = next(c for c in calls if c[:2] == ["docker", "build"])
    assert build[build.index("--platform") + 1] == "linux/arm64"


# ------------------------------------------------------------------ L4: regional SigV4 S3 only
def test_s3_clients_only_through_aws_clients():
    from finplan_agent.core.aws_clients import client, s3_client

    pat = re.compile(r"""(client|resource)\(\s*["']s3["']""")
    offenders = []
    for sub in ("agent", "scripts", "tests"):
        for p in (ROOT / sub).rglob("*.py"):
            if p.name == "aws_clients.py" or p == Path(__file__):
                continue
            if pat.search(p.read_text()):
                offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []
    s3 = s3_client("us-east-2")
    assert s3.meta.endpoint_url == "https://s3.us-east-2.amazonaws.com" and s3.meta.config.signature_version == "s3v4"
    with pytest.raises(ValueError):
        client("s3")


# ------------------------------------------------------------------ L5: offline vs deployed suites
def test_offline_suites_use_fake_credentials_only():
    import os

    from tests.harness import OFFLINE_MARKER

    assert os.environ[OFFLINE_MARKER] == "1" and os.environ["AWS_ACCESS_KEY_ID"].startswith("testing")
    assert "AWS_PROFILE" not in os.environ and os.environ["AWS_SHARED_CREDENTIALS_FILE"] == os.devnull


def test_deployed_suites_never_install_the_offline_harness():
    for d in ("integration_beta", "gamma", "smoke"):
        text = (ROOT / "tests" / d / "conftest.py").read_text()
        assert "harness" not in text and "_EXECUTED" in text and "session.exitstatus = 1" in text
    assert "deployed suites must run with the stage role's real credentials" in (ROOT / "tests" / "deployed.py").read_text()


def test_runtime_invocation_url_encodes_the_arn():
    from tests.deployed import parse_sse, runtime_invocation_url

    url = runtime_invocation_url("us-east-2", "a" + "rn:aws:bedrock-agentcore:us-east-2:" + "0" * 12 + ":runtime/x")
    assert url.startswith("https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/arn%3Aaws%3A") and url.endswith("/invocations?qualifier=DEFAULT")
    assert parse_sse(b'data: {"type": "final"}\n\n') == [{"type": "final"}]


# ------------------------------------------------------------------ hygiene
def test_no_leaks_in_repository_files():
    from finplan_contracts.leak_scan import scan_paths

    _, findings = scan_paths([ROOT], exclude_dirs=(".venv", ".git", "node_modules", "cdk.out", "vendor", "openspec", ".claude", "__pycache__", ".pytest_cache"))
    assert [str(f) for f in findings] == []


def test_contract_conformance_consumer_mode():
    """CS-01/CS-10 consumer side: pinned version served, no copied contract schema in this repo."""
    from finplan_contracts.conformance import run_consumer

    report = run_consumer(repo=ROOT, expect_version=json.loads((ROOT / "contracts-pin.json").read_text())["version"])
    assert report.ok, [str(p) for p in report.problems]


def test_leak_scan_and_copied_id_detector_fail_on_planted_violations(tmp_path):
    """Task 1.4: the leak scan (ENV-08) and the copied-``$id`` detector (CS-01) reject fixture violations."""
    import shutil

    from finplan_contracts.conformance import run_consumer
    from finplan_contracts.leak_scan import scan_paths
    from finplan_contracts.schemas import load_store

    (tmp_path / "leak.py").write_text("ROLE = 'arn:aws:iam::" + "4" * 12 + ":role/planted'\n", encoding="utf-8")
    _, findings = scan_paths([tmp_path])
    assert [f.rule for f in findings] == ["arn"]
    (tmp_path / "leak.py").unlink()
    for name in ("pyproject.toml", "uv.lock", "contracts-pin.json"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "copied.json").write_text(json.dumps({"$id": load_store().get("core/v1/caller").id, "type": "object"}), encoding="utf-8")
    report = run_consumer(repo=tmp_path, expect_version=json.loads((ROOT / "contracts-pin.json").read_text())["version"])
    assert not report.ok and any("CS-01" in str(p) for p in report.problems)
