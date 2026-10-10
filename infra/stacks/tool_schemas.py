"""Gateway tool definitions generated from the PINNED contract package (task 5.3; FA-GW-04; design D2
"Registration").

The AgentCore Gateway ``SchemaDefinition`` carries only ``type``, ``description``, ``properties``,
``required`` and ``items`` (CloudFormation ``AWS::BedrockAgentCore::GatewayTarget`` reference,
verified 2026-10-08). Contract schemas use ``$ref``, ``$defs``, ``allOf``/``oneOf``/``anyOf``, ``enum``,
``const``, ``pattern``, ``format`` and bounds. :func:`project` resolves the references through the
pinned :class:`finplan_contracts.schemas.SchemaStore` and moves every constraint it cannot express
into the property ``description`` (a documented relaxation). The tool Lambda remains the
authoritative validator: an invalid identifier sent through the Gateway is still rejected by the
tool with ``INVALID_IDENTIFIER``.

Nothing here copies a contract schema into the repository (CS-01): definitions are generated at synth
time from the installed, digest-pinned package and live only in the synthesized template.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from finplan_contracts.schemas import SchemaStore, load_store

__all__ = ["CLASSICAL_IDENTITY_HEADER", "CLASSICAL_ONLY_TOOLS", "CLASSICAL_SHARED_TOOLS", "GATEWAY_TYPES", "MAX_DEPTH", "ToolDefinition", "classical_tools", "contract_tools", "primary_tools", "project", "target_metadata", "tool_definition", "to_cfn"]

CLASSICAL_IDENTITY_HEADER = "X-Finplan-User-Token"


def target_metadata(env: str, tool: str, *, classical: bool) -> dict[str, Any]:
    """Paid beta research and human portfolio resolutions receive a verification JWT.

    Header propagation transports untrusted credentials, not trusted group claims. The
    target verifies the signature and pinned Cognito issuer/client before authorizing.
    Paper resolutions propagate the same header through either independent Gateway.
    """
    if tool == "resolve_portfolio_decision" or env == "beta" and classical and tool in {"run_portfolio_research", "run_recursive_improvement"}:
        return {"allowedRequestHeaders": [CLASSICAL_IDENTITY_HEADER]}
    return {}

CLASSICAL_ONLY_TOOLS = frozenset({
    "recommend_classical_portfolio", "explain_classical_recommendation", "compare_classical_plans",
    "evaluate_classical_performance", "get_classical_analysis", "list_classical_analyses",
    "research_portfolio_models", "research_market_events", "run_portfolio_research", "run_recursive_improvement", "submit_portfolio_feedback",
})
CLASSICAL_SHARED_TOOLS = frozenset({
    "get_portfolio_history", "list_portfolio_decisions", "get_portfolio_decision", "resolve_portfolio_decision", "list_market_snapshots", "record_agent_activity", "list_agent_activity", "explain_portfolio_decision", "compare_portfolio_decisions", "evaluate_portfolio_decision",
    "query_market_data", "get_plan", "get_plan_version", "list_plan_versions", "get_performance_evidence",
    "get_job_status", "get_experiment_result", "submit_experiment", "describe_capabilities",
})

GATEWAY_TYPES = ("string", "number", "integer", "boolean", "object", "array")
#: Nesting depth carried into the Gateway schema; deeper structures become an undescribed object
#: (still validated by the tool).
MAX_DEPTH = 6
_DESC_MAX = 480
_CONSTRAINTS = ("pattern", "format", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "uniqueItems", "const", "multipleOf")
_REQ_SUFFIX = "-request"
_RESP_SUFFIX = "-response"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_schema_id: str
    output_schema_id: str


def contract_tools(store: SchemaStore | None = None) -> list[str]:
    """Tool names that have a request AND a response schema in the pinned contract package."""
    store = store or load_store()
    names = set(store.names())
    out = []
    for n in names:
        if n.startswith("tools/") and n.endswith(_REQ_SUFFIX):
            stem = n[len("tools/") : -len(_REQ_SUFFIX)]
            if f"tools/{stem}{_RESP_SUFFIX}" in names:
                out.append(stem.replace("-", "_"))
    return sorted(out)


def primary_tools(store: SchemaStore | None = None) -> list[str]:
    """Preserve the existing MCP catalog when the contracts package adds traditional tools."""
    return sorted(set(contract_tools(store)) - CLASSICAL_ONLY_TOOLS)


def classical_tools(store: SchemaStore | None = None) -> list[str]:
    """Traditional analysis catalog, with only the shared reads its workflows require."""
    return sorted(set(contract_tools(store)) & (CLASSICAL_ONLY_TOOLS | CLASSICAL_SHARED_TOOLS))


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _DESC_MAX else text[: _DESC_MAX - 3] + "..."


def _join_ref(base: str, ref: str) -> str:
    if ref.startswith("#"):
        return base.split("#", 1)[0] + ref
    if "://" in ref:
        return ref
    # relative file reference against the base URI's directory
    root = base.split("#", 1)[0].rsplit("/", 1)[0]
    return f"{root}/{ref}"


def _absolutize(node: Any, base: str) -> Any:
    """Copy of ``node`` with every ``$ref`` made absolute against ``base`` (so merged subschemas from
    other documents keep resolving after ``allOf``/``oneOf`` merging)."""
    if isinstance(node, Mapping):
        inner = str(node["$id"]) if isinstance(node.get("$id"), str) and "://" in str(node["$id"]) else base
        return {k: (_join_ref(inner, v) if k == "$ref" and isinstance(v, str) else _absolutize(v, inner)) for k, v in node.items()}
    if isinstance(node, list):
        return [_absolutize(v, base) for v in node]
    return node


def _literal_type(value: Any) -> str:
    """JSON literal type, with booleans distinguished from Python integers."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _implicit_type(node: Mapping[str, Any]) -> str:
    """Infer the type constrained by a literal before falling back to structural hints."""
    if "const" in node:
        return _literal_type(node["const"])
    if "enum" in node:
        types = {_literal_type(value) for value in node["enum"]} - {"null"}
        if len(types) == 1:
            return types.pop()
        if types and types <= {"integer", "number"}:
            return "number"
        # The Gateway cannot express a heterogeneous or null-only enum. Keep the
        # existing string relaxation; its full constraint remains in the description.
        return "string"
    if "properties" in node or "additionalProperties" in node:
        return "object"
    if "items" in node:
        return "array"
    return "string" if "pattern" in node or "format" in node else "object"


class _Projector:
    def __init__(self, store: SchemaStore) -> None:
        self.store = store

    def resolve(self, node: Mapping[str, Any], base: str) -> tuple[dict[str, Any], str]:
        seen = 0
        node = dict(node)
        while "$ref" in node and seen < 16:
            uri = _join_ref(base, str(node["$ref"]))
            target = _absolutize(self.store.resolve_pointer(uri), uri)
            rest = {k: v for k, v in node.items() if k != "$ref"}
            node = {**dict(target), **rest}
            base = uri
            seen += 1
        return node, base

    def project(self, node: Any, base: str, depth: int) -> dict[str, Any]:
        if not isinstance(node, Mapping):
            return {"type": "object", "description": "any JSON value (validated by the tool)"}
        node, base = self.resolve(node, base)
        notes: list[str] = []
        desc = str(node.get("description") or node.get("title") or "")
        # allOf: merge object members
        for sub in node.get("allOf") or []:
            sub, sbase = self.resolve(sub, base)
            node = {
                **sub,
                **{k: v for k, v in node.items() if k != "allOf"},
                "properties": {**(sub.get("properties") or {}), **(node.get("properties") or {})},
                "required": sorted(set(sub.get("required") or []) | set(node.get("required") or [])),
            }
        variants = node.get("oneOf") or node.get("anyOf")
        if variants:
            resolved = [self.resolve(v, base)[0] for v in variants]
            if all((v.get("type") == "object" or "properties" in v) for v in resolved):
                props: dict[str, Any] = {}
                req_sets = []
                for v in resolved:
                    props.update(v.get("properties") or {})
                    req_sets.append(set(v.get("required") or []))
                node = {**node, "type": "object", "properties": {**props, **(node.get("properties") or {})}, "required": sorted(set.intersection(*req_sets) | set(node.get("required") or [])) if req_sets else node.get("required")}
                notes.append(f"one of {len(resolved)} shapes; the tool validates the exact shape")
            else:
                variant_types = [v.get("type", _implicit_type(v)) for v in resolved]
                types = [t for variant_type in variant_types for t in ([variant_type] if isinstance(variant_type, str) else variant_type or []) if t != "null"]
                node = {**node, "type": types[0] if types else "string"}
                notes.append("one of: " + "; ".join(_short(v) for v in resolved))
        t = node.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            if len(t) > 1:
                notes.append("may be " + " or ".join(t))
            t = non_null[0] if non_null else "string"
        if t is None:
            t = _implicit_type(node)
        if t == "null":
            t = "string"
            notes.append("null")
        if "enum" in node:
            notes.append("one of " + ", ".join(json.dumps(v) for v in node["enum"]))
        for c in _CONSTRAINTS:
            if c in node:
                notes.append(f"{c} {json.dumps(node[c])}")
        out: dict[str, Any] = {"type": t}
        if t == "object" and depth < MAX_DEPTH:
            props = node.get("properties") or {}
            if props:
                out["properties"] = {k: self.project(v, base, depth + 1) for k, v in sorted(props.items())}
                req = [r for r in node.get("required") or [] if r in props]
                if req:
                    out["required"] = sorted(req)
            if node.get("additionalProperties") is False:
                notes.append("no other members")
        elif t == "object":
            notes.append("nested object (validated by the tool)")
        if t == "array":
            items = node.get("items")
            out["items"] = self.project(items, base, depth + 1) if (items is not None and depth < MAX_DEPTH) else {"type": "object", "description": "array element (validated by the tool)"}
        full = desc + (" [" + "; ".join(notes) + "]" if notes else "")
        if full.strip():
            out["description"] = _clip(full)
        return out


def _short(v: Mapping[str, Any]) -> str:
    if "const" in v:
        return json.dumps(v["const"])
    if "enum" in v:
        return "|".join(json.dumps(x) for x in v["enum"])
    return str(v.get("type") or v.get("title") or "value")


def project(schema_key: str, store: SchemaStore | None = None) -> dict[str, Any]:
    """The Gateway ``SchemaDefinition`` (lower-case keys) of contract schema ``schema_key``."""
    store = store or load_store()
    info = store.get(schema_key)
    out = _Projector(store).project(info.schema, info.id, 0)
    if out.get("type") != "object":  # tool input and output are always objects
        out = {"type": "object", "description": out.get("description", "")}
    return out


def tool_definition(tool: str, description: str | None = None, store: SchemaStore | None = None) -> ToolDefinition:
    store = store or load_store()
    stem = tool.replace("_", "-")
    request_name = "tools/recommend-portfolio-invocation-request" if tool == "recommend_portfolio" else f"tools/{stem}{_REQ_SUFFIX}"
    req = store.get(request_name)
    resp = store.get(f"tools/{stem}{_RESP_SUFFIX}")
    desc = description or str(req.schema.get("description") or tool)
    desc = _clip(desc)
    if tool == "recommend_portfolio":
        from finplan_agent.skills import recommendation_mcp_description
        desc = recommendation_mcp_description(desc)
    elif tool in CLASSICAL_ONLY_TOOLS or tool in CLASSICAL_SHARED_TOOLS:
        from finplan_agent.skills import classical_mcp_description
        desc = classical_mcp_description(tool, desc)
    return ToolDefinition(name=tool, description=desc, input_schema=project(req.name, store), output_schema=project(resp.name, store), input_schema_id=req.id, output_schema_id=resp.id)


_KEYS = {"type": "Type", "description": "Description", "properties": "Properties", "required": "Required", "items": "Items"}


def to_cfn(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Lower-case SchemaDefinition -> CloudFormation property casing (member names stay as they are)."""
    out: dict[str, Any] = {}
    for k, v in schema.items():
        key = _KEYS[k]
        if k == "properties":
            out[key] = {name: to_cfn(sub) for name, sub in v.items()}
        elif k == "items":
            out[key] = to_cfn(v)
        else:
            out[key] = v
    return out
