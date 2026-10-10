"""The Gateway tool policy (task 5.4; FA-POL-03, FA-POL-05, FA-POL-06): ``policy/tool-policy.yaml``
rendered to Cedar for the AgentCore policy engine attached to each environment's Gateway.

Verified against the AgentCore Policy documentation (2026-10-08):

* with a JWT (``CUSTOM_JWT``) Gateway the principal is ``AgentCore::OAuthUser``; every JWT claim is a
  STRING tag (an array claim such as ``cognito:groups`` becomes its JSON text), so group membership is
  matched with ``like "*\\"<group>\\"*"`` after ``hasTag``;
* an MCP target yields one action per tool, ``AgentCore::Action::"<target>___<tool>"``;
* a policy naming specific actions must name the specific Gateway ARN (``resource ==``);
* the engine denies by default: a request is allowed only when a ``permit`` applies and no ``forbid``
  overrides it; ``tools/list`` shows each caller only the tools it may call.

The rendered statements carry the placeholder :data:`GATEWAY_ARN_VAR`; the stack substitutes the
deployed Gateway ARN with ``Fn::Sub``. :func:`policy_digest` hashes the rendered policy set (with the
placeholder), so the digest is identical in every environment and recorded in each release manifest
(drift check, FA-POL-06).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import naming as n

__all__ = ["GATEWAY_ARN_VAR", "POLICY_FILE", "CedarPolicy", "PolicyError", "allowed_roles", "decide", "load_policy", "policy_digest", "render"]

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = ROOT / "policy" / "tool-policy.yaml"
CLASSICAL_POLICY_FILE = ROOT / "policy" / "classical-tool-policy.yaml"
GATEWAY_ARN_VAR = "${GatewayArn}"
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}\Z")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CedarPolicy:
    tool: str
    kind: str  # permit | forbid
    name: str  # suffix of the policy resource name
    statement: str


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_policy(path: Path | None = None) -> dict[str, Any]:
    doc = _load(str(path or POLICY_FILE))
    problems = validate_policy(doc)
    if problems:
        raise PolicyError("; ".join(problems))
    return doc


_GRANT_RE = re.compile(r"^(?P<tool>[a-z][a-z0-9_]{1,63})(?:\((?P<arg>[a-z][a-z0-9_]*)=(?P<values>[A-Za-z0-9_-]+(?:\|[A-Za-z0-9_-]+)*)\))?\Z")


def _parse_grant(entry: Any) -> tuple[str, tuple[str, tuple[str, ...]] | None] | None:
    """``tool`` or ``tool(arg=v1|v2)`` (the grant applies only when the argument has one of the values)."""
    m = _GRANT_RE.match(str(entry))
    if not m:
        return None
    limit = (m["arg"], tuple(m["values"].split("|"))) if m["arg"] else None
    return m["tool"], limit


def grants(doc: Mapping[str, Any] | None = None) -> dict[str, dict[str, tuple[str, tuple[str, ...]] | None]]:
    """``{tool: {role: argument limit or None}}``."""
    doc = doc or load_policy()
    out: dict[str, dict[str, tuple[str, tuple[str, ...]] | None]] = {}
    for role, spec in (doc.get("roles") or {}).items():
        for entry in (spec or {}).get("tools") or []:
            parsed = _parse_grant(entry)
            if parsed:
                out.setdefault(parsed[0], {})[str(role)] = parsed[1]
    return out


def validate_policy(doc: Mapping[str, Any]) -> list[str]:
    problems = []
    roles = doc.get("roles") or {}
    words = [str(w) for w in doc.get("denied_tool_words") or []]
    if not words:
        problems.append("denied_tool_words must list the live-financial words")
    for role, spec in roles.items():
        if not _ROLE_RE.match(str(role)):
            problems.append(f"role {role!r} is not a Cognito group name")
        for entry in (spec or {}).get("tools") or []:
            parsed = _parse_grant(entry)
            if parsed is None:
                problems.append(f"{role}: {entry!r} is not a tool name or tool(arg=value|...) grant")
                continue
            hit = [w for w in words if w in parsed[0]]
            if hit and parsed[0] != "list_executions":
                problems.append(f"{role}: tool {parsed[0]!r} names a denied capability ({', '.join(hit)}); only paper and research operations may be allowed (FA-POL-05)")
    for role in doc.get("machine_roles") or {}:
        if role not in roles:
            problems.append(f"machine role {role!r} has no tool list")
    return problems


def allowed_roles(tool: str, doc: Mapping[str, Any] | None = None) -> list[str]:
    """Roles granted ``tool`` (fully or for limited argument values)."""
    return sorted(grants(doc).get(tool, {}))


def decide(tool: str, *, groups: Iterable[str] = (), scopes: Iterable[str] = (), arguments: Mapping[str, Any] | None = None, doc: Mapping[str, Any] | None = None) -> bool:
    """Reference evaluation of the policy (the fixture test matrix and the gamma parity suite use it).

    Mirrors the rendered Cedar: default deny; permit when a caller group (or a machine scope) is
    granted the tool (and, for a limited grant, the argument has an allowed value); forbid when a
    denied argument value is present. The channel is not an input.
    """
    doc = doc or load_policy()
    words = [str(w) for w in doc.get("denied_tool_words") or []]
    if tool != "list_executions" and any(w in tool for w in words):
        return False
    args = dict(arguments or {})
    for arg, values in (doc.get("denied_arguments") or {}).items():
        if arg in args and str(args.get(arg)) in [str(v) for v in values]:
            return False
    machine = doc.get("machine_roles") or {}
    held = set(groups) | {role for role, scope in machine.items() if f"{n.RESOURCE_SERVER}/{scope}" in set(scopes)}
    for role, limit in grants(doc).get(tool, {}).items():
        if role not in held:
            continue
        value = args.get(limit[0]) if limit else None
        normalized = json.dumps(value) if isinstance(value, bool) else str(value)
        if limit is None or normalized in limit[1]:
            return True
    return False


def _action(tool: str) -> str:
    return f'AgentCore::Action::"{n.target_name(tool)}___{tool}"'


def _head(kind: str, tool: str) -> str:
    return f'{kind}(\n  principal is AgentCore::OAuthUser,\n  action == {_action(tool)},\n  resource == AgentCore::Gateway::"{GATEWAY_ARN_VAR}"\n)'


def _who(roles: list[str], machine: Mapping[str, str]) -> str:
    conds = []
    human = [r for r in roles if r not in machine]
    if human:
        groups = " || ".join(f'principal.getTag("cognito:groups") like "*\\"{r}\\"*"' for r in human)
        conds.append(f'(principal.hasTag("cognito:groups") && ({groups}))')
    for r in roles:
        if r in machine:
            conds.append(f'(principal.hasTag("scope") && principal.getTag("scope") like "*{n.RESOURCE_SERVER}/{machine[r]}*")')
    return " ||\n  ".join(conds)


def _contract_input_properties(tools: Iterable[str]) -> dict[str, Mapping[str, Any]]:
    from finplan_contracts.schemas import SchemaNotFound

    from .tool_schemas import tool_definition

    out: dict[str, Mapping[str, Any]] = {}
    for t in tools:
        try:
            out[t] = tool_definition(t).input_schema.get("properties", {})
        except SchemaNotFound:
            out[t] = {}
    return out


def render(tools: Iterable[str], input_properties: Mapping[str, Mapping[str, Any]] | None = None, doc: Mapping[str, Any] | None = None) -> list[CedarPolicy]:
    """Cedar policies for the registered ``tools``: one permit per tool for its full grants, one per
    argument-limited grant, plus one forbid per denied argument the tool's input schema carries."""
    doc = doc or load_policy()
    tools = sorted(set(tools))
    if input_properties is None:
        input_properties = _contract_input_properties(tools)
    words = [str(w) for w in doc.get("denied_tool_words") or []]
    machine = doc.get("machine_roles") or {}
    table = grants(doc)
    out: list[CedarPolicy] = []
    for tool in sorted(set(tools)):
        if tool != "list_executions" and any(w in tool for w in words):
            continue  # never permitted (default deny)
        by_limit: dict[Any, list[str]] = {}
        for role, limit in sorted(table.get(tool, {}).items()):
            by_limit.setdefault(limit, []).append(role)
        props = (input_properties or {}).get(tool) or {}
        for limit, roles in sorted(by_limit.items(), key=lambda kv: (kv[0] is not None, str(kv[0]))):
            who = _who(roles, machine)
            if limit is None:
                out.append(CedarPolicy(tool, "permit", "allow", _head("permit", tool) + "\nwhen {\n  " + who + "\n};"))
                continue
            arg, values = limit
            argument_type = (props.get(arg) or {}).get("type")
            if argument_type == "boolean" and all(v in ("true", "false") for v in values):
                vals = " || ".join(f"context.input.{arg} == {v}" for v in values)
            elif argument_type == "string":
                vals = " || ".join(f"context.input.{arg} == {json.dumps(v)}" for v in values)
            else:
                raise PolicyError(f"{tool}: limited grant on {arg!r}, which is not a supported string or boolean argument of the tool's input schema")
            out.append(CedarPolicy(tool, "permit", f"allow_{arg}_{'_'.join(values)}", _head("permit", tool) + f"\nwhen {{\n  ({who}) &&\n  context.input has {arg} && ({vals})\n}};"))
        for arg, values in sorted((doc.get("denied_arguments") or {}).items()):
            if (props.get(arg) or {}).get("type") != "string":
                continue
            vals = " || ".join(f"context.input.{arg} == {json.dumps(str(v))}" for v in values)
            out.append(CedarPolicy(tool, "forbid", f"deny_{arg}", _head("forbid", tool) + f"\nwhen {{\n  context.input has {arg} && ({vals})\n}};"))
    return out


def policy_digest(policies: Iterable[CedarPolicy]) -> str:
    body = json.dumps([[p.tool, p.kind, p.name, p.statement] for p in sorted(policies, key=lambda p: (p.tool, p.kind, p.name))], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
