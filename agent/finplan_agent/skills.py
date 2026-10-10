"""Versioned, deployable skill instructions loaded by the hosted LangGraph agent."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml


def recommendation_mcp_description(description: str, root=None) -> str:
    """Expose the exact packaged recommendation recipe through tools/list, without a new target."""
    directory = Path(root or os.environ.get('FINPLAN_SKILLS_DIR', Path(__file__).resolve().parents[2]/'skills'))
    manifest = directory/'recommend-portfolio'/'skill.yaml'
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
