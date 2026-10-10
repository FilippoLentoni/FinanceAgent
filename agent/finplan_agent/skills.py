"""Versioned, deployable skill instructions loaded by the hosted LangGraph agent."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

from .providers.base import ToolSpec


def provider_tool_specs(specs):
    """The hosted prompt already contains packaged skills; avoid repeating them per tool."""
    return tuple(ToolSpec(name=s.name, description=s.description.split("\n\nSkill: ", 1)[0],
                          input_schema=s.input_schema, state_changing=s.state_changing) for s in specs)


def recommendation_mcp_description(description: str, root=None) -> str:
    """Expose the exact packaged recommendation recipe through tools/list, without a new target."""
    return _skill_description('recommend-portfolio', description, root)


CLASSICAL_SKILLS = {
    'recommend_classical_portfolio': 'recommend-classical-portfolio',
    'get_classical_analysis': 'recommend-classical-portfolio',
    'list_classical_analyses': 'recommend-classical-portfolio',
    'explain_classical_recommendation': 'explain-classical-recommendation',
    'compare_classical_plans': 'compare-classical-plans',
    'evaluate_classical_performance': 'evaluate-classical-performance',
    'research_market_events': 'evaluate-classical-performance',
    'research_portfolio_models': 'research-portfolio-models',
    'run_portfolio_research': 'research-portfolio-models',
    'submit_portfolio_feedback': 'research-portfolio-models',
}


def classical_mcp_description(tool: str, description: str, root=None) -> str:
    """The remote traditional MCP publishes the same versioned recipes as the hosted agent."""
    name = CLASSICAL_SKILLS.get(tool)
    return description if name is None else _skill_description(name, description, root)


def _skill_description(name: str, description: str, root=None) -> str:
    directory = Path(root or os.environ.get('FINPLAN_SKILLS_DIR', Path(__file__).resolve().parents[2]/'skills'))
    manifest = directory/name/'skill.yaml'
    doc = yaml.safe_load(manifest.read_text())
    text = manifest.with_name('SKILL.md').read_text()
    checksum = 'sha256:'+hashlib.sha256(text.encode()).hexdigest()
    return (description + f"\n\nSkill: {doc['name']}@{doc['version']}; instructions_checksum: {checksum}. "
            + "This is the same instruction recipe packaged with the hosted agent. Follow it when calling the MCP tools directly. "
            + "Declared tools: " + ', '.join(doc['tools']) + ".\n\n" + text)


def load_skills(root=None):
    directory=Path(root or os.environ.get('FINPLAN_SKILLS_DIR',Path(__file__).resolve().parents[2]/'skills'))
    inventory=[]
    instructions=[]
    for manifest in sorted(directory.glob('*/skill.yaml')):
        doc=yaml.safe_load(manifest.read_text())
        text=manifest.with_name('SKILL.md').read_text()
        inventory.append({'name':doc['name'],'version':doc['version'],'tools':doc['tools'],'instructions_checksum':'sha256:'+hashlib.sha256(text.encode()).hexdigest()})
        instructions.append(text)
    return tuple(inventory),tuple(instructions)
