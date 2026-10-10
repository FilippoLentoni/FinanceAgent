"""Archive completed hosted turns independently of expiring conversation checkpoints."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from .. import GRAPH_VERSION
from ..providers.base import text_of

_SECRET = re.compile(r"authorization|bearer|password|secret|access.token|refresh.token|id.token|user.token|download.grant|presigned|credential", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_PRIVATE_LOCATION = re.compile(r"s3://[^\s\"]+|arn:[a-z0-9-]*:[^\s\"]+|https?://[^\s\"]*amazonaws\.com[^\s\"]*", re.I)
_SIGNED_URL = re.compile(r"https?://[^\s\"]*(?:X-Amz-|Signature=)[^\s\"]*", re.I)


def sanitize(value):
    if isinstance(value, Mapping):
        return {str(k):"<redacted>" if _SECRET.search(str(k)) else sanitize(v) for k,v in value.items()}
    if isinstance(value,list):
        return [sanitize(v) for v in value]
    if isinstance(value,str):
        return _PRIVATE_LOCATION.sub("<redacted-private-location>",_SIGNED_URL.sub("<redacted-download-grant>",_JWT.sub("<redacted-token>",value)))
    return value


def archive_turn(state,ctx,status,usage):
    user=next((m for m in reversed(state.get("messages",[])) if m.get("role")=="user" and any("text" in b for b in m.get("content",[]))),{})
    cid=state.get("correlation_id", "unknown-correlation")
    payload={"prompt":text_of(user),"narrative":state.get("narrative",""),"status":status,
             "skills_used":state.get("skills_used",[]),"tool_results":state.get("tool_results",[]),
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
