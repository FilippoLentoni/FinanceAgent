"""Optional bounded completion evidence and Langfuse OTLP export.

No headers, caller claims or raw exception messages leave the runtime. The source
answer remains authoritative: audit/export failure never changes its contract.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
import urllib.request
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("finplan_agent.audit")
_CURRENT: ContextVar[Turn | None] = ContextVar("finplan_audit_turn", default=None)
_PRIVATE = re.compile(r"authorization|bearer|password|secret|token|api.?key|credential|cookie|private.?key|client.?secret", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_KEY = re.compile(r"\b(?:sk-(?:proj-|lf-)?[A-Za-z0-9_-]{10,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16})\b")
MAX_SPANS = 80


def clean(value: Any, depth: int = 0) -> Any:
    """Allow bounded JSON primitives only; redact credentials at every depth."""
    if depth > 6:
        return "[depth limit]"
    if isinstance(value, str):
        text = _KEY.sub("[redacted]", _JWT.sub("[redacted]", _BEARER.sub("[redacted]", value)))
        return text if len(text) <= 4096 else text[:4080] + "[truncated]"
    if isinstance(value, float) and not math.isfinite(value):
        return "[non-finite value]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        result = {str(k)[:80]: "[redacted]" if _PRIVATE.search(str(k)) else clean(v, depth + 1) for k, v in list(value.items())[:40] if str(k).lower() not in ("caller", "headers", "request_headers")}
        if len(value) > 40 or any(len(str(k)) > 80 for k in value):
            result["__audit_truncated__"] = True
        return result
    if isinstance(value, (list, tuple)):
        return [clean(v, depth + 1) for v in value[:24]] + ([{"__audit_truncated__": True}] if len(value) > 24 else [])
    return "[unsupported value]"


class Turn:
    def __init__(self, payload: Any, environment: str, release: str | None, runtime_arn: str) -> None:
        self.start = time.time_ns()
        self.trace_id = secrets.token_hex(16)
        self.root_id = secrets.token_hex(8)
        self.environment, self.release, self.runtime_arn = environment, release, runtime_arn
        p = payload if isinstance(payload, dict) else {}
        self.input = clean({k: p[k] for k in ("prompt", "action", "tool_request", "explanation", "recommendation", "approve") if k in p})
        self.synthetic = p.get("synthetic_demo") is True
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.finished = False
        self.skills_available: Any = []

    def record(self, name: str, kind: str, start: int, inputs: Any, outputs: Any, *, error: str | None = None, model: str | None = None, usage: Any = None, cost: float | None = None) -> None:
        event = {"id": secrets.token_hex(8), "name": name[:120], "kind": kind, "start": start, "end": time.time_ns(), "input": clean(inputs), "output": clean(outputs), "error": clean(error), "model": model, "usage": usage, "cost": cost}
        with self.lock:
            if len(self.events) < MAX_SPANS:
                self.events.append(event)

    def document(self, final: dict[str, Any], *, cancelled: bool = False) -> dict[str, Any]:
        answer = final.get("answer") if isinstance(final.get("answer"), dict) else {}
        metadata = clean(
            {
                "source_runtime_arn": self.runtime_arn,
                "contract_source_commit": "d4ff644cdad9260936d13a76f6be32604baa25dd",
                "correlation_id": final.get("correlation_id"),
                "outcome": "cancelled" if cancelled else final.get("status"),
                "synthetic_demo": self.synthetic,
                "skills_used": answer.get("skills_used", []),
                "skills_available": self.skills_available,
                "skill_evidence_semantics": "declared instructions and routed skill labels, not script execution",
                "claim_check": answer.get("claim_check", {}),
                "evidence_truncated": len(self.events) >= MAX_SPANS,
                "capture_policy": "bounded-credential-redaction-v1",
            }
        )
        document = {
            "schema_version": 1,
            "trace_id": self.trace_id,
            "source_runtime_arn": self.runtime_arn,
            "root_id": self.root_id,
            "start": self.start,
            "end": time.time_ns(),
            "environment": self.environment,
            "release": self.release,
            "session_id": final.get("session_id"),
            "input": self.input,
            "output": clean({"status": final.get("status"), "narrative": answer.get("narrative"), "evidence": answer.get("evidence", []), "skills_used": answer.get("skills_used", []), "error_code": (final.get("error") or {}).get("code")}),
            "metadata": metadata,
            "events": list(self.events),
        }
        # Apply an aggregate cap as well as per-field/depth caps. Retain every
        # boundary's name/status/timing; omit large payloads with explicit hashes.
        for event in sorted(document["events"], key=lambda e: len(json.dumps(e)), reverse=True):
            if len(json.dumps(document).encode()) <= 196608:
                break
            for field in ("input", "output"):
                raw = json.dumps(event[field]).encode()
                if len(raw) > 256:
                    event[field] = {"omitted_for_size": True, "sha256": hashlib.sha256(raw).hexdigest()}
            metadata["evidence_truncated"] = True
        if len(json.dumps(document).encode()) > 196608:
            document["output"]["evidence"] = {"omitted_for_size": True}
            metadata["evidence_truncated"] = True
        if "[truncated]" in json.dumps(document) or "[depth limit]" in json.dumps(document) or "__audit_truncated__" in json.dumps(document):
            metadata["evidence_truncated"] = True
        return document


def wrap_provider(provider: Any) -> Any:
    if _CURRENT.get() is None:
        return provider

    class Provider:
        def __getattr__(self, name: str) -> Any:
            return getattr(provider, name)

        def generate(self, request: Any, on_token: Any = None) -> Any:
            turn = _CURRENT.get()
            if turn is None:
                return provider.generate(request) if on_token is None else provider.generate(request, on_token=on_token)
            start = time.time_ns()
            try:
                result = provider.generate(request) if on_token is None else provider.generate(request, on_token=on_token)
            except BaseException as exc:
                turn.record("provider-" + request.purpose, "GENERATION", start, {"purpose": request.purpose}, None, error=type(exc).__name__, model=provider.model_id)
                raise
            turn.record(
                "provider-" + request.purpose,
                "GENERATION" if provider.kind != "fixture" else "SPAN",
                start,
                {"purpose": request.purpose, "messages": request.messages},
                {"text": result.text, "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in result.tool_calls], "stop_reason": result.stop_reason, "provider_kind": provider.kind},
                model=result.model_id,
                usage={"input": result.usage.input_tokens, "output": result.usage.output_tokens, "cache_read": result.usage.cache_read_tokens, "cache_write": result.usage.cache_write_tokens},
                cost=result.estimated_cost_usd,
            )
            return result

    return Provider()


def wrap_tools(tools: Any) -> Any:
    if _CURRENT.get() is None:
        return tools

    class Tools:
        def __getattr__(self, name: str) -> Any:
            return getattr(tools, name)

        def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            turn = _CURRENT.get()
            if turn is None:
                return tools.call_tool(name, arguments)
            start = time.time_ns()
            try:
                result = tools.call_tool(name, arguments)
            except BaseException as exc:
                turn.record(name, "TOOL", start, arguments, None, error=type(exc).__name__)
                raise
            turn.record(name, "TOOL", start, arguments, {"ok": result.ok, "result": result.result, "error_code": (result.error or {}).get("code")}, error=None if result.ok else (result.error or {}).get("code", "tool_failure"))
            return result

    return Tools()


def otlp(document: dict[str, Any]) -> dict[str, Any]:
    def attr(key: str, value: Any) -> dict[str, Any]:
        return {"key": key, "value": {"stringValue": value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))}}

    common = [attr("session.id", document.get("session_id") or ""), attr("langfuse.release", document.get("release") or ""), attr("langfuse.environment", document["environment"])]
    common += [attr("langfuse.trace.metadata." + k, v) for k, v in document["metadata"].items()]

    def span(event: dict[str, Any], root: bool = False) -> dict[str, Any]:
        attributes = common + [attr("langfuse.observation.type", event["kind"].lower()), attr("langfuse.observation.input", event["input"]), attr("langfuse.observation.output", event["output"])]
        if event.get("model"):
            attributes.append(attr("langfuse.observation.model.name", event["model"]))
        if event.get("usage"):
            attributes.append(attr("langfuse.observation.usage_details", event["usage"]))
        if event.get("cost") is not None:
            attributes.append(attr("langfuse.observation.cost_details", {"total": event["cost"]}))
        if event.get("error"):
            attributes += [attr("langfuse.observation.level", "ERROR"), attr("langfuse.observation.status_message", event["error"])]
        out = {
            "traceId": document["trace_id"],
            "spanId": event["id"],
            "name": event["name"],
            "kind": 1,
            "startTimeUnixNano": str(event["start"]),
            "endTimeUnixNano": str(event["end"]),
            "attributes": attributes,
            "status": {"code": 2 if event.get("error") else 1},
        }
        if not root:
            out["parentSpanId"] = document["root_id"]
        return out

    root = {
        "id": document["root_id"],
        "name": "FinanceAgent turn",
        "kind": "SPAN",
        "start": document["start"],
        "end": document["end"],
        "input": document["input"],
        "output": document["output"],
        "error": "cancelled" if document["metadata"].get("outcome") == "cancelled" else None,
    }
    return {"resourceSpans": [{"resource": {"attributes": [attr("service.name", "financeagent")]}, "scopeSpans": [{"scope": {"name": "finplan-audit", "version": "1"}, "spans": [span(root, True)] + [span(e) for e in document["events"]]}]}]}


class Publisher:
    def __init__(self, env: Mapping[str, str] | None = None, *, clients: Any = None, post: Any = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.enabled = self.env.get("FINPLAN_JUDGE_ENABLED", "false").lower() == "true"
        self._clients = clients
        self._post = post
        self._secret: dict[str, str] | None = None
        self._lock = threading.Lock()
        self._secret_loaded = float("-inf")

    def client(self, service: str) -> Any:
        if self._clients is not None:
            return self._clients(service)
        from ..core.aws_clients import client, s3_client

        region = self.env.get("AWS_REGION", "us-east-2")
        if service == "s3":
            return s3_client(region, connect_timeout=2, read_timeout=3, max_attempts=1)
        return client(service, region, connect_timeout=2, read_timeout=3, max_attempts=1)

    def credentials(self) -> dict[str, str]:
        with self._lock:
            if self._secret is None or time.monotonic() - self._secret_loaded > 300:
                value = self.client("secretsmanager").get_secret_value(SecretId=self.env["FINPLAN_LANGFUSE_SECRET_ARN"])
                self._secret = json.loads(value["SecretString"])
                self._secret_loaded = time.monotonic()
            return self._secret

    def export(self, document: dict[str, Any]) -> None:
        credentials = self.credentials()
        host = credentials["LANGFUSE_BASE_URL"].rstrip("/")
        parsed = urlsplit(host)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Langfuse base URL must be an HTTPS origin")
        authorization = base64.b64encode((credentials["LANGFUSE_PUBLIC_KEY"] + ":" + credentials["LANGFUSE_SECRET_KEY"]).encode()).decode()
        request = urllib.request.Request(
            host + "/api/public/otel/v1/traces",
            data=json.dumps(otlp(document), separators=(",", ":")).encode(),
            headers={"Authorization": "Basic " + authorization, "Content-Type": "application/json", "x-langfuse-ingestion-version": "4"},
            method="POST",
        )
        if self._post:
            self._post(request)
        else:

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args: Any, **kwargs: Any) -> Any:
                    raise ValueError("telemetry redirect refused")

            with urllib.request.build_opener(NoRedirect()).open(request, timeout=4) as response:
                if response.status // 100 != 2:
                    raise ValueError("telemetry rejected")
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("telemetry response too large")
                result = json.loads(raw) if raw else {}
                partial = result.get("partialSuccess") or result.get("partial_success") or {}
                if int(partial.get("rejectedSpans", partial.get("rejected_spans", 0))):
                    raise ValueError("telemetry partially rejected")

    def publish(self, turn: Turn, final: dict[str, Any], *, cancelled: bool = False) -> None:
        if turn.finished:
            return
        turn.finished = True
        try:
            document = turn.document(final, cancelled=cancelled)
            bucket = self.env.get("FINPLAN_JUDGE_OUTBOX_BUCKET")
            key = "completions/" + document["trace_id"] + ".json"
            if bucket and not cancelled:
                self.client(service="s3").put_object(Bucket=bucket, Key=key, Body=json.dumps(document).encode(), ContentType="application/json", ServerSideEncryption="AES256", IfNoneMatch="*")
            self.export(document)
            if not cancelled:
                self.client("sqs").send_message(QueueUrl=self.env["FINPLAN_JUDGE_QUEUE_URL"], MessageBody=json.dumps({"trace_id": document["trace_id"], "source_runtime_arn": document["source_runtime_arn"]}))
        except Exception:
            log.warning("optional completion audit failed; outbox reconciliation may be required")


class AuditedService:
    def __init__(self, service: Any, publisher: Publisher) -> None:
        self.service, self.publisher = service, publisher

    def _publish(self, turn: Turn, final: dict[str, Any], *, cancelled: bool = False) -> None:
        try:
            self.publisher.publish(turn, final, cancelled=cancelled)
        except Exception:
            turn.finished = True
            log.warning("optional completion audit failed before export")

    def handle(self, payload: Any, headers: Any, session_id: Any) -> Any:
        if not self.publisher.enabled or (isinstance(payload, dict) and payload.get("action") in ("describe", "delete_session")):
            return self.service.handle(payload, headers, session_id)
        try:
            settings = self.service.deps.settings
            turn = Turn(payload, settings.environment, settings.release_id, self.publisher.env.get("FINPLAN_RUNTIME_ARN", ""))
            turn.skills_available = clean(self.service.deps.skills)
        except Exception:
            log.warning("optional completion audit initialization failed")
            return self.service.handle(payload, headers, session_id)
        token = _CURRENT.set(turn)
        try:
            response = self.service.handle(payload, headers, session_id)
        finally:
            _CURRENT.reset(token)
        if isinstance(response, dict):
            if response.get("type") == "final":
                self._publish(turn, response)
            return response
        return self._iterate(response, turn, session_id)

    def _iterate(self, response: Iterator[Any], turn: Turn, session_id: Any) -> Iterator[Any]:
        iterator = iter(response)
        try:
            while True:
                token = _CURRENT.set(turn)
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                finally:
                    _CURRENT.reset(token)
                if isinstance(item, dict) and item.get("type") == "final":
                    self._publish(turn, item)
                yield item
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()
            if not turn.finished:
                self._publish(turn, {"session_id": session_id, "status": "cancelled", "answer": None}, cancelled=True)
