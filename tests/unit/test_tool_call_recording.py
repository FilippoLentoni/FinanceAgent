"""Every tool call's exact input and output is persisted as a tool_call activity event (phase 2 §2a item 3)."""
import base64
import hashlib
import json

from finplan_contracts.validate import validate

from finplan_agent.tools.mcp_client import ToolOutcome
from finplan_agent.tools.recording import ToolCallRecorder, canonical, decode_stored, encode_for_activity, sha256_of
from tests.fakes.agent import PL, SESSION, FakeToolClient, auth, make_service

RECEIPT = {"checksum": "sha256:" + "a" * 64, "recorded_at": "2026-10-19T10:00:00Z", "contract_version": "1.14.0"}


def store():
    events = []

    def record(args):
        assert validate(args, "tools/record-agent-activity-request").valid, validate(args, "tools/record-agent-activity-request").issues
        events.append(json.loads(json.dumps(args)))
        return {"activity_event_id": f"ae_{len(events):04d}", **RECEIPT}
    return events, record


def recording_service(results, **kw):
    events, record = store()
    tools = FakeToolClient({**results, "record_agent_activity": record}, extra_tools=["record_agent_activity", *kw.pop("extra_tools", [])])
    svc, tools = make_service(tools=tools, **kw)
    svc.deps.record_tool_calls = True
    return svc, tools, events


def test_every_graph_tool_call_is_followed_by_an_exact_tool_call_event():
    result_doc = {"plan_id": PL, "location": "s3:" + "//example-store/key.json", "nested": {"pointer": "/a/b", "url": "http://example.com/x"}}
    svc, tools, events = recording_service({"get_plan": lambda a: result_doc})
    final = svc.handle({"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False, "correlation_id": "corr-recording-1"}, auth(), SESSION)
    assert final["status"] == "completed"
    assert [name for name, _ in tools.calls] == ["get_plan", "record_agent_activity"]
    event = events[0]
    assert event["event_kind"] == "tool_call" and event["session_id"] == SESSION and event["correlation_id"] == "corr-recording-1"
    assert event["synthetic"] is False
    assert event["idempotency_key"] == "tc-" + hashlib.sha256(f"{SESSION}|corr-recording-1|1".encode()).hexdigest()[:60]
    payload = event["payload"]
    assert payload["tool"] == "get_plan" and payload["sequence"] == 1 and payload["ok"] is True and payload["gateway"] == "primary"
    assert payload["arguments"] == {"plan_id": PL}
    # Exact content: checksums of the received value, and the stored form decodes back to it.
    assert payload["exact"] is True and payload["redactions"] == []
    assert payload["result_sha256"] == sha256_of(result_doc) and payload["arguments_sha256"] == sha256_of({"plan_id": PL})
    assert decode_stored(payload["result"]) == result_doc
    assert "s3://" not in json.dumps(payload)
    receipts = final["answer"]["tool_call_receipts"]
    assert receipts == [{"tool": "get_plan", "call_id": "corr-recording-1-1", "sequence": 1, "gateway": "primary", "ok": True,
                         "activity_event_id": "ae_0001", "recording_status": "recorded", "checksum": RECEIPT["checksum"], "recorded_at": RECEIPT["recorded_at"],
                         "exact": True, "arguments_sha256": payload["arguments_sha256"], "result_sha256": payload["result_sha256"]}]
    assert final["answer"]["tool_call_recording"]["status"] == "complete"
    # The receipts do not appear as evidence (the modeling runner checks that exactly one tool ran).
    assert [e["tool"] for e in final["answer"]["evidence"]] == ["get_plan"]


def test_failed_tool_calls_and_errors_are_recorded_too():
    svc, tools, events = recording_service({})
    tools.errors["get_plan"] = {"code": "NOT_FOUND", "message": "no plan", "retryable": False, "details": {}}
    final = svc.handle({"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False}, auth(), SESSION)
    payload = events[0]["payload"]
    assert payload["ok"] is False and payload["error"]["code"] == "NOT_FOUND" and payload["result"] is None
    assert final["answer"]["tool_call_receipts"][0]["recording_status"] == "recorded"


def test_recording_failure_keeps_the_answer_and_reports_the_receipt_status():
    svc, tools, _ = recording_service({"get_plan": lambda a: {"plan_id": PL}})
    tools.errors["record_agent_activity"] = {"code": "DEPENDENCY_UNAVAILABLE", "message": "down", "retryable": True, "details": {}}
    final = svc.handle({"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False}, auth(), SESSION)
    assert final["status"] == "completed"
    receipt = final["answer"]["tool_call_receipts"][0]
    assert receipt["recording_status"] == "failed" and receipt["activity_event_id"] is None and receipt["recording_error"] == "DEPENDENCY_UNAVAILABLE"
    assert final["answer"]["tool_call_recording"] == {"status": "incomplete", "recorded": 0, "failed": 1, "failed_call_ids": [receipt["call_id"]]}


def test_recorder_does_not_consume_the_tool_call_budget_or_capture_cap():
    # Six graph calls are the per-turn cap; recording adds six more record calls without hitting it.
    svc, tools, events = recording_service({"get_plan": lambda a: {"plan_id": PL}})
    machine = auth("comparison-machine", groups=(), extra={"client_id": "comparison-machine"})
    final = svc.handle({"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False, "capture_inputs": True}, machine, SESSION)
    assert final["status"] == "completed" and final["usage"]["tool_calls"] == 1
    kinds = [args["event_kind"] for name, args in tools.calls if name == "record_agent_activity"]
    assert kinds.count("tool_call") == 1 and "modeling_input_capture" in kinds
    assert next(e for e in events if e["event_kind"] == "tool_call")["synthetic"] is True  # machine comparison client
    assert final["captured_inputs"] and final["answer"]["tool_call_receipts"][0]["recording_status"] == "recorded"


def test_credentials_are_redacted_with_explicit_markers_and_exact_false():
    value = {"note": "Bearer " + "x" * 40, "download_grant": "anything", "plain": "ok"}
    stored, redactions = encode_for_activity(value, ("result",))
    assert stored == {"note": "<redacted-credential>", "download_grant": "<redacted>", "plain": "ok"}
    assert sorted(redactions) == [["result", "download_grant"], ["result", "note"]]


def test_marker_shaped_objects_round_trip():
    value = {"representation": "encoded_string", "encoding": "base64url_utf8", "value": "abc"}
    stored, _ = encode_for_activity(value)
    assert stored["representation"] == "encoded_json" and decode_stored(stored) == value


def test_large_results_are_split_into_part_events_that_reassemble_exactly():
    events, record = store()
    sink = FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"])
    big = {"rows": ["x" * 1000 for _ in range(300)]}
    inner = FakeToolClient({"query_market_data": lambda a: big}, extra_tools=["query_market_data"])
    recorder = ToolCallRecorder(sink, session_id=SESSION, correlation_id="corr-big", max_event_bytes=50_000, chunk_bytes=40_000)
    outcome = recorder.wrap(inner, gateway="primary").call_tool("query_market_data", {"input_snapshot_id": "x"})
    assert outcome.ok and outcome.result == big
    head, parts = events[0], events[1:]
    assert head["event_kind"] == "tool_call" and head["payload"]["representation"] == "parts" and head["payload"]["result"] is None
    assert len(parts) == head["payload"]["parts"]["count"] > 1 and all(p["event_kind"] == "tool_call_part" for p in parts)
    data = b"".join(base64.urlsafe_b64decode(p["payload"]["content"]["value"] + "=" * (-len(p["payload"]["content"]["value"]) % 4)) for p in parts)
    assert "sha256:" + hashlib.sha256(data).hexdigest() == head["payload"]["parts"]["content_sha256"]
    content = json.loads(data)
    assert decode_stored(content["result"]) == big and canonical(decode_stored(content["result"])) == canonical(big)
    assert recorder.receipts[0]["part_activity_event_ids"] == [p for p in ("ae_0002", "ae_0003", "ae_0004", "ae_0005", "ae_0006", "ae_0007", "ae_0008", "ae_0009")[: len(parts)]]
    assert len({e["idempotency_key"] for e in events}) == len(events)


def test_raised_tool_errors_are_recorded_then_reraised():
    import pytest

    from finplan_agent.core.errors import AgentError

    events, record = store()
    sink = FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"])

    class Down:
        def list_tools(self):
            return []

        def call_tool(self, name, arguments):
            raise AgentError.dependency("gateway down")

    recorder = ToolCallRecorder(sink, session_id=SESSION, correlation_id="corr-down")
    with pytest.raises(AgentError):
        recorder.wrap(Down()).call_tool("get_plan", {"plan_id": PL})
    assert events[0]["payload"]["ok"] is False and events[0]["payload"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_brokerage_reader_calls_are_recorded():
    events, record = store()
    sink = FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"])

    class Reader:
        def __init__(self):
            self.client = FakeToolClient({"get_etf_connection_status": lambda a: {"authenticated": True}}, extra_tools=["get_etf_connection_status"])

    reader = Reader()
    recorder = ToolCallRecorder(sink, session_id=SESSION, correlation_id="corr-broker")
    reader.client = recorder.wrap(reader.client, gateway="brokerage")
    assert reader.client.call_tool("get_etf_connection_status", {}).ok
    assert events[0]["payload"]["gateway"] == "brokerage" and events[0]["payload"]["tool"] == "get_etf_connection_status"


def test_outcome_carries_its_receipt_reference():
    events, record = store()
    sink = FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"])
    inner = FakeToolClient({"get_plan": lambda a: {"plan_id": PL}})
    out = ToolCallRecorder(sink, session_id=SESSION, correlation_id="corr-x").wrap(inner).call_tool("get_plan", {"plan_id": PL})
    assert isinstance(out, ToolOutcome) and out.extra["tool_call_receipt"]["activity_event_id"] == "ae_0001"
