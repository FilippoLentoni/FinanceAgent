"""Durable per-call tool I/O records (phase 2 exit criterion 3).

:class:`ToolCallRecorder` wraps the per-request :class:`~finplan_agent.tools.mcp_client.ToolClient`
(graph, explanation subgraph, lifecycle workflows) and the brokerage reader's client. After EVERY tool
call except ``record_agent_activity`` itself it calls ``record_agent_activity`` with
``event_kind = "tool_call"`` through the UNWRAPPED client, so recording never consumes the per-turn
tool-call budget, the explanation call bound or the input-capture cap, and never passes the in-graph
policy checks (the Gateway policy grants ``record_agent_activity`` to every role).

Event payload (``payload_format`` ``finplan.tool_call.v1``)::

    {"tool", "call_id", "sequence", "gateway", "ok", "arguments", "result", "error",
     "started_at", "finished_at", "duration_ms", "size_bytes", "mcp_size_bytes",
     "arguments_sha256", "result_sha256", "error_sha256", "exact", "redactions",
     "representation", "release_id", "parts"}

``*_sha256`` are ``sha256:<hex>`` of the RFC 8785 (JCS) canonical JSON of the value EXACTLY as the
agent sent / received it (the parsed MCP result), computed before any transformation; JCS is also
what the platform stores, so numerically identical values (``0.0`` / ``0``) hash identically. Values are stored as received, except:

* strings the activity contract would refuse or the archive tool would rewrite (storage URIs, ARNs,
  path-like or ``http://`` values, ``amazonaws.com`` URLs) become
  ``{"representation": "encoded_string", "encoding": "base64url_utf8", "value": ...}``;
* a genuine object of that same shape becomes ``{"representation": "encoded_json",
  "encoding": "base64url_canonical_json", "value": ...}``;
* credentials (bearer/JWT/AWS keys, signed URLs, values under credential-named keys) are replaced by
  ``"<redacted>"`` / ``"<redacted-credential>"`` and listed in ``redactions`` (JSON-pointer segment lists);
  ``exact`` is then false.

Decoding the two representations reproduces the original value byte-for-byte in canonical form, so
``exact: true`` records are verifiable against their checksums.

Content larger than :data:`MAX_EVENT_CONTENT_BYTES` (platform event cap assumed 4 MiB) is stored in
additional ``tool_call_part`` events: the head event keeps all metadata with ``arguments`` /
``result`` / ``error`` set to ``null`` and ``parts`` = ``{"count", "content_sha256", "encoding"}``;
part ``i`` carries ``content.value`` = base64url of bytes ``[i*chunk, (i+1)*chunk)`` of the RFC 8785
canonical JSON ``{"arguments", "error", "result"}`` of the stored (encoded) values.

Idempotency keys: ``tc-<sha256(session|correlation|sequence)[:60]>``; part ``i`` uses
``p<sha256(<head key>|i)[:63]>``.
A recording failure is logged and reported per call in the receipts (``recording_status``); the
turn still answers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .mcp_client import ToolOutcome

__all__ = ["write_activity", "MAX_EVENT_CONTENT_BYTES", "PART_CHUNK_BYTES", "ToolCallRecorder", "canonical", "decode_stored", "encode_for_activity", "sha256_of"]

log = logging.getLogger("finplan_agent.tools.recording")

RECORD_TOOL = "record_agent_activity"
EVENT_KIND = "tool_call"
PART_EVENT_KIND = "tool_call_part"
PAYLOAD_FORMAT = "finplan.tool_call.v1"
#: Inline content above this many bytes is split into part events (platform cap assumed 4 MiB).
MAX_EVENT_CONTENT_BYTES = 3 * 1024 * 1024
#: Raw bytes per part (base64url adds a third: 2 MiB -> ~2.7 MiB per event).
PART_CHUNK_BYTES = 2 * 1024 * 1024

# Mirrors of finplan_contracts ``no_storage_locations`` and FinanceLambdasTool ``core.activity.sanitize``.
_STORAGE_URI = re.compile(r"(?i)(\b(s3a?|s3n|gs|hdfs|file|ftp)://|\barn:[a-z0-9-]*:|\.s3[.-]([a-z0-9-]+\.)?amazonaws\.com)")
_PATH_LIKE = re.compile(r"(?i)(^\s*[a-z][a-z0-9+.-]*://|^\s*(/|~/|\.{1,2}/)|\\)")
_PRIVATE_LOCATION = re.compile(r"s3://[^\s\"]+|arn:[a-z0-9-]*:[^\s\"]+|https?://[^\s\"]*amazonaws\.com[^\s\"]*", re.I)
_SIGNED_URL = re.compile(r"https?://[^\s\"]*(?:X-Amz-|Signature=)[^\s\"]*", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_CREDENTIAL = re.compile(r"Bearer\s+[A-Za-z0-9._~-]{16,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|X-Amz-(?:Credential|Signature)=", re.I)
_SECRET_KEY = re.compile(r"authorization|bearer|password|secret|access.token|refresh.token|id.token|user.token|download.grant|presigned|credential", re.I)
_MARKERS = frozenset({"encoded_string", "encoded_json"})


def canonical(value: Any) -> bytes:
    """RFC 8785 (JCS) canonical UTF-8 bytes, the form FinancialPlanning stores (``0.0`` -> ``0``).

    A reader can decode a stored value, re-serialize it with any RFC 8785 implementation and obtain
    the recorded hash. Values that are not plain JSON (e.g. tuples, dates) are first converted the
    way they were sent (``json`` with ``str`` for unknown types).
    """
    from finplan_contracts.canonical import canonicalize

    try:
        return canonicalize(value)
    except Exception:  # noqa: BLE001 - non-JSON leaf types: normalize, then canonicalize
        return canonicalize(json.loads(json.dumps(value, default=str)))


def sha256_of(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pointer(path: tuple[Any, ...]) -> list[str]:
    """JSON-pointer segments as a list (a ``/``-joined string would look path-like to the contract)."""
    return [str(p) for p in path]


def _unsafe(text: str) -> bool:
    """True when the activity contract refuses ``text`` under ``payload`` or the archive tool rewrites it."""
    if _STORAGE_URI.search(text) or _PRIVATE_LOCATION.search(text):
        return True
    return bool(_PATH_LIKE.search(text)) and not text.startswith("https://")


def encode_for_activity(value: Any, path: tuple[Any, ...] = ()) -> tuple[Any, list[list[str]]]:
    """(storable value, redaction pointers). See the module docstring for the representations."""
    redactions: list[list[str]] = []

    def walk(node: Any, at: tuple[Any, ...]) -> Any:
        if isinstance(node, Mapping):
            if set(node) == {"representation", "encoding", "value"} and node.get("representation") in _MARKERS:
                return {"representation": "encoded_json", "encoding": "base64url_canonical_json", "value": _b64(canonical(node))}
            out = {}
            for key, item in node.items():
                key = str(key)
                if _SECRET_KEY.search(key):
                    redactions.append(_pointer(at + (key,)))
                    out[key] = "<redacted>"
                else:
                    out[key] = walk(item, at + (key,))
            return out
        if isinstance(node, (list, tuple)):
            return [walk(item, at + (i,)) for i, item in enumerate(node)]
        if isinstance(node, str):
            if _CREDENTIAL.search(node) or _JWT.search(node) or _SIGNED_URL.search(node):
                redactions.append(_pointer(at))
                return "<redacted-credential>"
            if _unsafe(node):
                return {"representation": "encoded_string", "encoding": "base64url_utf8", "value": _b64(node.encode("utf-8"))}
        return node

    return walk(value, path), redactions


def decode_stored(value: Any) -> Any:
    """Inverse of :func:`encode_for_activity` for records without redactions."""
    if isinstance(value, Mapping):
        if set(value) == {"representation", "encoding", "value"}:
            if value.get("representation") == "encoded_string" and value.get("encoding") == "base64url_utf8":
                return _unb64(value["value"]).decode("utf-8")
            if value.get("representation") == "encoded_json" and value.get("encoding") == "base64url_canonical_json":
                return json.loads(_unb64(value["value"]))
        return {k: decode_stored(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_stored(v) for v in value]
    return value


def write_activity(sink: Any, *, event_kind: str, session_id: str | None, correlation_id: str, synthetic: bool, idempotency_key: str,
                   metadata: dict[str, Any], content: dict[str, Any], links: Mapping[str, Any] | None = None,
                   max_event_bytes: int = MAX_EVENT_CONTENT_BYTES, chunk_bytes: int = PART_CHUNK_BYTES) -> dict[str, Any]:
    """Write ``metadata`` + already encoded ``content`` as one activity event, or as a head event plus
    ``<event_kind>_part`` events when the canonical content exceeds ``max_event_bytes``.

    Returns ``{"activity_event_id", "checksum", "recorded_at", "part_activity_event_ids"?}``; raises
    ``RuntimeError(<code>)`` when a write is refused.
    """
    def send(kind: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        body = {"event_kind": kind, "correlation_id": correlation_id, "synthetic": synthetic, "payload": payload, "idempotency_key": key, **(links or {})}
        if session_id:
            body["session_id"] = session_id
        outcome = sink.call_tool(RECORD_TOOL, body)
        receipt = outcome.result if outcome.ok and isinstance(outcome.result, dict) else None
        if not receipt or not receipt.get("activity_event_id"):
            code = (outcome.error or {}).get("code") if not outcome.ok else "RECEIPT_MISSING"
            raise RuntimeError(code or "RECORD_FAILED")
        return receipt

    content_bytes = canonical(content)
    if len(content_bytes) <= max_event_bytes:
        head = send(event_kind, {**metadata, "representation": "inline", "parts": None, **content}, idempotency_key)
        return {k: head.get(k) for k in ("activity_event_id", "checksum", "recorded_at")}
    chunks = [content_bytes[i:i + chunk_bytes] for i in range(0, len(content_bytes), chunk_bytes)]
    parts = {"count": len(chunks), "content_sha256": "sha256:" + hashlib.sha256(content_bytes).hexdigest(),
             "encoding": "base64url_canonical_json_bytes", "event_kind": event_kind + "_part", "content_keys": sorted(content)}
    head = send(event_kind, {**metadata, "representation": "parts", "parts": parts, **{k: None for k in content}}, idempotency_key)
    ids = []
    for index, chunk in enumerate(chunks):
        part = {"payload_format": metadata.get("payload_format"), "head_activity_event_id": head["activity_event_id"], "part": index, "count": len(chunks),
                "offset": index * chunk_bytes, "content": {"encoding": "base64url", "value": _b64(chunk)}}
        for key in ("tool", "call_id", "sequence", "boundary"):
            if key in metadata:
                part[key] = metadata[key]
        key = "p" + hashlib.sha256(f"{idempotency_key}|{index}".encode()).hexdigest()[:63]
        ids.append(send(event_kind + "_part", part, key)["activity_event_id"])
    return {**{k: head.get(k) for k in ("activity_event_id", "checksum", "recorded_at")}, "part_activity_event_ids": ids}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ToolCallRecorder:
    """Per-request recorder. ``wrap(client)`` returns a recording view of ``client``."""

    def __init__(self, sink: Any, *, session_id: str, correlation_id: str, synthetic: bool = False, release_id: str | None = None,
                 max_event_bytes: int = MAX_EVENT_CONTENT_BYTES, chunk_bytes: int = PART_CHUNK_BYTES) -> None:
        self._sink = sink  # unwrapped client that offers record_agent_activity
        self.session_id = session_id
        self.correlation_id = correlation_id
        self.synthetic = synthetic
        self.release_id = release_id
        self.max_event_bytes = max_event_bytes
        self.chunk_bytes = chunk_bytes
        self.sequence = 0
        self.receipts: list[dict[str, Any]] = []

    @property
    def sink(self) -> Any:
        """The unwrapped client that offers ``record_agent_activity``."""
        return self._sink

    # ------------------------------------------------------------------ wrapping
    def wrap(self, client: Any, *, gateway: str | None = None) -> _RecordingClient:
        return _RecordingClient(self, client, gateway)

    def summary(self) -> dict[str, Any]:
        failed = [r["call_id"] for r in self.receipts if r["recording_status"] != "recorded"]
        return {"status": "complete" if not failed else "incomplete", "recorded": len(self.receipts) - len(failed), "failed": len(failed), "failed_call_ids": failed}

    # ------------------------------------------------------------------ recording
    def _key(self, sequence: int) -> str:
        raw = f"{self.session_id}|{self.correlation_id}|{sequence}"
        return "tc-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:60]

    def record(self, *, tool: str, arguments: Any, outcome: ToolOutcome | None, error: dict[str, Any] | None, gateway: str | None,
               started_at: str, finished_at: str, duration_ms: float) -> dict[str, Any]:
        self.sequence += 1
        sequence = self.sequence
        call_id = f"{self.correlation_id}-{sequence}"
        ok = bool(outcome and outcome.ok)
        result = outcome.result if outcome is not None else None
        error = error if error is not None else (outcome.error if outcome is not None else None)
        receipt: dict[str, Any] = {"tool": tool, "call_id": call_id, "sequence": sequence, "gateway": gateway, "ok": ok,
                                   "activity_event_id": None, "recording_status": "failed"}
        try:
            arguments_sha, result_sha, error_sha = sha256_of(arguments), sha256_of(result), sha256_of(error)
            stored_args, r1 = encode_for_activity(arguments, ("arguments",))
            stored_result, r2 = encode_for_activity(result, ("result",))
            stored_error, r3 = encode_for_activity(error, ("error",))
            redactions = r1 + r2 + r3
            content = {"arguments": stored_args, "error": stored_error, "result": stored_result}
            metadata: dict[str, Any] = {
                "payload_format": PAYLOAD_FORMAT, "tool": tool, "call_id": call_id, "sequence": sequence, "gateway": gateway, "ok": ok,
                "started_at": started_at, "finished_at": finished_at, "duration_ms": round(duration_ms, 3),
                "size_bytes": len(canonical(result)), "mcp_size_bytes": outcome.size_bytes if outcome is not None else 0,
                "arguments_sha256": arguments_sha, "result_sha256": result_sha, "error_sha256": error_sha,
                "exact": not redactions, "redactions": redactions[:200], "release_id": self.release_id,
            }
            written = write_activity(self._sink, event_kind=EVENT_KIND, session_id=self.session_id, correlation_id=self.correlation_id,
                                     synthetic=self.synthetic, idempotency_key=self._key(sequence), metadata=metadata, content=content,
                                     links=_links(arguments, result), max_event_bytes=self.max_event_bytes, chunk_bytes=self.chunk_bytes)
            receipt.update(activity_event_id=written["activity_event_id"], checksum=written.get("checksum"), recorded_at=written.get("recorded_at"))
            if written.get("part_activity_event_ids"):
                receipt["part_activity_event_ids"] = written["part_activity_event_ids"]
            receipt.update(recording_status="recorded", exact=not redactions, arguments_sha256=arguments_sha, result_sha256=result_sha)
        except Exception as exc:  # noqa: BLE001 - recording never fails the turn; the receipt says so
            receipt["recording_error"] = str(exc)[:200] if isinstance(exc, RuntimeError) else type(exc).__name__
            log.warning("tool call recording failed (tool=%s, correlation_id=%s, reason=%s)", tool, self.correlation_id, receipt["recording_error"])
        self.receipts.append(receipt)
        return receipt


def _links(arguments: Any, result: Any) -> dict[str, Any]:
    """Typed index fields of the event (portfolio, decision, snapshot) when the call names them."""
    try:
        from finplan_contracts.validate import validate
    except Exception:  # noqa: BLE001
        return {}
    sources: list[Any] = [arguments, result]
    if isinstance(result, dict):
        for key in ("recommendation", "decision"):
            if isinstance(result.get(key), dict):
                sources.append(result[key])
                if isinstance(result[key].get("portfolio_state"), dict):
                    sources.append(result[key]["portfolio_state"])
    out: dict[str, Any] = {}
    for key in ("portfolio_id", "decision_id", "input_snapshot_id"):
        for source in sources:
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and validate({key: value}, "identifiers").valid:
                out[key] = value
                break
    return out


class _RecordingClient:
    def __init__(self, recorder: ToolCallRecorder, inner: Any, gateway: str | None) -> None:
        self._recorder = recorder
        self._inner = inner
        self._gateway = gateway

    def __getattr__(self, name: str) -> Any:  # list_tools and anything else the inner client offers
        return getattr(self._inner, name)

    def list_tools(self):  # noqa: ANN201
        return self._inner.list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name == RECORD_TOOL:
            return self._inner.call_tool(name, arguments)
        try:
            sent = json.loads(canonical(arguments))  # exactly what is handed to the client
        except (TypeError, ValueError):
            sent = arguments
        started, clock = _now(), time.monotonic()
        try:
            outcome = self._inner.call_tool(name, arguments)
        except Exception as exc:
            error = {"code": getattr(exc, "code", type(exc).__name__), "message": str(getattr(exc, "message", "") or "")[:500], "raised": True}
            self._recorder.record(tool=name, arguments=sent, outcome=None, error=error, gateway=self._gateway, started_at=started, finished_at=_now(), duration_ms=(time.monotonic() - clock) * 1000)
            raise
        gateway = outcome.extra.get("gateway") or self._gateway
        receipt = self._recorder.record(tool=name, arguments=sent, outcome=outcome, error=None, gateway=gateway, started_at=started, finished_at=_now(), duration_ms=(time.monotonic() - clock) * 1000)
        return replace(outcome, extra={**outcome.extra, "tool_call_receipt": {k: receipt.get(k) for k in ("call_id", "activity_event_id", "recording_status")}})
