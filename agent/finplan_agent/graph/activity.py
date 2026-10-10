"""Archive completed hosted turns independently of expiring conversation checkpoints."""
from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping

from .. import GRAPH_VERSION
from ..providers.base import text_of

_SECRET = re.compile(r"authorization|bearer|password|secret|access.token|refresh.token|id.token|user.token|download.grant|presigned|credential", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_PRIVATE_LOCATION = re.compile(r"s3://[^\s\"]+|arn:[a-z0-9-]*:[^\s\"]+|https?://[^\s\"]*amazonaws\.com[^\s\"]*", re.I)
_SIGNED_URL = re.compile(r"https?://[^\s\"]*(?:X-Amz-|Signature=)[^\s\"]*", re.I)
_ACTIVITY_METADATA = ("activity_event_id", "checksum", "event_kind", "recorded_at", "portfolio_id", "decision_id", "input_snapshot_id", "session_id", "correlation_id", "caller", "contract_version", "synthetic")
_POINTER_KEYS = frozenset({"pointer", "json_pointer", "instance_pointer", "schema_pointer", "schema_path", "instancePath", "schemaPath"})


def _pointer_segments(pointer):
    """Preserve raw RFC 6901 UTF-8 segments without archiving a path-like string.

    Empty segments and ~ escapes roundtrip exactly. The empty pointer has no
    segments, whereas '/' has one empty segment. Base64url keeps arbitrary keys
    from looking like storage locations to the pinned activity contract.
    """
    return {"representation": "json_pointer", "encoding": "base64url_utf8_segments",
            "segments": [base64.urlsafe_b64encode(part.encode()).decode().rstrip("=") for part in pointer[1:].split("/")] if pointer else []}


def sanitize(value):
    if isinstance(value, Mapping):
        pointer = next((v for k, v in value.items() if k in _POINTER_KEYS and isinstance(v, str) and (not v or v.startswith("/"))), None)
        result = {}
        for k, v in value.items():
            key = str(k)
            if _SECRET.search(key):
                result[key] = "<redacted>"
            elif key in _POINTER_KEYS and isinstance(v, str) and (not v or v.startswith("/")):
                result[key] = _pointer_segments(v)
            elif key == "message" and pointer is not None and isinstance(v, str) and v.startswith((pointer or "/") + ":"):
                prefix = pointer or "/"
                result[key] = {"representation": "json_pointer_diagnostic", "pointer": _pointer_segments(prefix), "suffix": sanitize(v[len(prefix):])}
            else:
                result[key] = sanitize(v)
        return result
    if isinstance(value,list):
        return [sanitize(v) for v in value]
    if isinstance(value,str):
        return _PRIVATE_LOCATION.sub("<redacted-private-location>",_SIGNED_URL.sub("<redacted-download-grant>",_JWT.sub("<redacted-token>",value)))
    return value


def activity_history_references(response):
    """Retain exact immutable event joins and pagination without recursively embedding payloads."""
    if not isinstance(response, Mapping) or "events" not in response:
        return response
    events=[]
    for event in response["events"]:
        if not isinstance(event, Mapping) or not event.get("activity_event_id") or not event.get("checksum"):
            raise ValueError("activity history lacks an immutable event identifier or checksum")
        metadata={key:event[key] for key in _ACTIVITY_METADATA if key in event}
        payload=event.get("payload") or event.get("summary") or {}
        metadata["summary"]={key:payload[key] for key in ("tool","status","release_id","graph_version") if key in payload}
        if payload.get("representation")=="chunked_immutable_json":
            metadata["summary"].update({key:payload[key] for key in ("representation","offset","end_offset","total","payload_checksum") if key in payload})
        events.append(metadata)
    return {**response,"events":events,"evidence_representation":"immutable_activity_references"}


def archive_turn(state,ctx,status,usage):
    user=next((m for m in reversed(state.get("messages",[])) if m.get("role")=="user" and any("text" in b for b in m.get("content",[]))),{})
    cid=state.get("correlation_id", "unknown-correlation")
    results=[{**row,"result":activity_history_references(row.get("result"))} if row.get("tool")=="list_agent_activity" and row.get("ok") else row for row in state.get("tool_results",[])]
    payload={"prompt":text_of(user),"narrative":state.get("narrative",""),"status":status,
             "skills_used":state.get("skills_used",[]),"tool_results":results,
             "confirmation":state.get("confirmation"),"claim_check":state.get("claim_check"),
             "release_id":ctx.release_id,"graph_version":GRAPH_VERSION,"usage":usage,
             "caller":state.get("caller"),"error":state.get("error")}
    arguments={"event_kind":"agent_turn","session_id":ctx.session_id,"correlation_id":cid,"payload":sanitize(payload),
               "idempotency_key":"turn-"+hashlib.sha256((ctx.session_id+"|"+cid).encode()).hexdigest()}
    for row in reversed(state.get("tool_results",[])):
        doc=row.get("result") or {}
        sources=[doc,doc.get("decision",{}),doc.get("recommendation",{}),doc.get("recommendation",{}).get("portfolio_state",{})]
        for key in ("portfolio_id","decision_id","input_snapshot_id"):
            for source in sources:
                if isinstance(source,dict) and source.get(key):
                    arguments.setdefault(key,source[key])
                    break
    return ctx.tools.call_tool("record_agent_activity",arguments)
