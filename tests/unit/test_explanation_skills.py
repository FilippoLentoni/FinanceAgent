"""Explanation skill bundle validation (add-explanation-workflows tasks 2.4, 3.7, 4.7; agent-skills
"Skill tool lists bounded by policy")."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finplan_agent.config.settings import load_repo_config
from finplan_agent.explanations.workflows import EXPLANATION_TYPES
from tests.fakes.agent import TOOLS
from tests.fakes.explanations import EXPLAIN_READ_TOOLS, catalog

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ("explain-performance", "explain-recommendation-change", "explain-sensitivity")
#: The only state-changing tools an explanation skill may list; both run only after confirmation.
CONFIRMED_ONLY = {"submit_experiment", "create_override_version"}


def bundle(name):
    return yaml.safe_load((ROOT / "skills" / name / "skill.yaml").read_text())


@pytest.mark.parametrize("name", SKILLS)
def test_skill_bundle_shape_and_tool_list(name):
    b = bundle(name)
    assert b["name"] == name and b["version"].count(".") == 2 and b["purpose"]
    assert b["explanation_type"] in EXPLANATION_TYPES
    assert (ROOT / "skills" / name / "SKILL.md").read_text().startswith(f"# {name}")
    known = {n for n, _, _ in TOOLS} | set(EXPLAIN_READ_TOOLS)
    cat = catalog()
    for tool in b["tools"]:
        assert tool in known, f"{name} lists {tool}, absent from the tool catalog"
        assert not cat.is_denied(tool), f"{name} lists the denied tool {tool}"
        if cat.state_changing(tool):
            assert tool in CONFIRMED_ONLY, f"{name} lists the state-changing tool {tool}"
    assert b["output_contract"]["envelope"] == "core/v1/explanation-result.json"
    assert set(b["output_contract"]["narrative_statuses"]) == {"generated", "unavailable", "budget_exceeded"}


@pytest.mark.parametrize("name", SKILLS)
def test_skill_narration_cap_matches_configuration_and_carries_no_model_or_price(name):
    b = bundle(name)
    assert b["narration"]["budget_category"] == "bedrock_explanations"
    for env in ("beta", "gamma", "prod"):
        assert b["narration"]["max_tokens_invocation"] == load_repo_config(env)["explanations"]["narration_max_tokens_invocation"]
    text = (ROOT / "skills" / name / "skill.yaml").read_text() + (ROOT / "skills" / name / "SKILL.md").read_text()
    assert "per_1k" not in text and "arn:" not in text and "amazonaws.com" not in text


def test_model_identifier_scan_covers_the_skill_bundles():
    from scripts.agent_gates import model_id_findings

    assert [f for f in model_id_findings() if "skills/" in str(f)] == []


def test_only_sensitivity_lists_the_plan_tool():
    assert ["explain-sensitivity"] == [s for s in SKILLS if "create_override_version" in bundle(s)["tools"]]
