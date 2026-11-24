import json
from concurrent.futures import ThreadPoolExecutor

from finplan_agent.runtime.audit import AuditedService, Publisher, clean, otlp
from tests.fakes.agent import PV, SESSION, SESSION_B, auth, make_service


class Sink(Publisher):
    def __init__(self):
        super().__init__(
            {"FINPLAN_JUDGE_ENABLED": "true", "FINPLAN_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-2:" + ("0" * 12) + ":runtime/main-123", "FINPLAN_JUDGE_QUEUE_URL": "https://sqs.us-east-2.amazonaws.com/" + ("0" * 12) + "/judge"},
            clients=lambda service: self,
        )
        self.documents = []
        self.messages = []

    def export(self, document):
        self.documents.append(document)

    def send_message(self, **kwargs):
        self.messages.append(json.loads(kwargs["MessageBody"]))


def question(stream=False):
    return {"prompt": "Explain plan version " + PV, "stream": stream}


def test_disabled_preserves_baseline_and_never_reads_secret():
    service, _ = make_service()
    sink = Publisher({"FINPLAN_JUDGE_ENABLED": "false"}, clients=lambda s: (_ for _ in ()).throw(AssertionError("network")))
    out = AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert out["status"] == "completed"
    assert out["answer"]["evidence"][0]["tool"] == "get_plan_version"


def test_real_graph_completion_exports_actual_provider_tool_and_final_evidence():
    service, _ = make_service()
    sink = Sink()
    out = AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert len(sink.documents) == len(sink.messages) == 1
    d = sink.documents[0]
    assert d["session_id"] == out["session_id"]
    assert d["metadata"]["correlation_id"] == out["correlation_id"]
    assert d["metadata"]["outcome"] == "completed"
    assert d["output"]["narrative"] == out["answer"]["narrative"]
    assert {e["name"] for e in d["events"]} >= {"provider-plan", "provider-narrate", "get_plan_version"}
    tool = next(e for e in d["events"] if e["kind"] == "TOOL")
    assert tool["input"]["plan_version_id"] == PV and tool["output"]["ok"] is True
    assert sink.messages[0]["trace_id"] == d["trace_id"]
    spans = otlp(d)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert sum("parentSpanId" not in x for x in spans) == 1
    assert all(x["parentSpanId"] == d["root_id"] for x in spans[1:])


def test_stream_does_not_export_before_final_and_never_leaks_context():
    service, _ = make_service()
    sink = Sink()
    wrapped = AuditedService(service, sink)
    stream = wrapped.handle(question(True), auth(), SESSION)
    assert sink.documents == []
    assert next(stream)["stage"] == "accepted"
    assert sink.documents == []
    events = list(stream)
    assert sum(e["type"] == "final" for e in events) == 1
    assert len(sink.documents) == 1
    assert sink.documents[0]["metadata"]["outcome"] == "completed"
    # A later unaudited graph turn must not append to the prior request's spans.
    count = len(sink.documents[0]["events"])
    service.handle(question(), auth(), SESSION_B)
    assert len(sink.documents[0]["events"]) == count


def test_early_stream_close_is_cancelled_and_never_queued_as_answer():
    service, _ = make_service()
    sink = Sink()
    stream = AuditedService(service, sink).handle(question(True), auth(), SESSION)
    next(stream)
    stream.close()
    assert len(sink.documents) == 1
    assert sink.documents[0]["metadata"]["outcome"] == "cancelled"
    assert sink.messages == []


def test_concurrent_turns_keep_tool_evidence_and_ids_separate():
    service, _ = make_service()
    sink = Sink()
    wrapped = AuditedService(service, sink)

    def run(session):
        return wrapped.handle(question(), auth(), session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        finals = list(pool.map(run, [SESSION, SESSION_B]))
    assert len(sink.documents) == 2
    assert {d["session_id"] for d in sink.documents} == {f["session_id"] for f in finals}
    assert len({d["trace_id"] for d in sink.documents}) == 2
    for d in sink.documents:
        assert sum(e["kind"] == "TOOL" for e in d["events"]) == 1


def test_export_failure_preserves_answer_and_never_logs_secret(caplog):
    service, _ = make_service()
    sink = Sink()

    def fail(document):
        raise RuntimeError("seeded-secret-must-not-be-logged")

    sink.export = fail
    final = AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert final["status"] == "completed" and sink.messages == []
    assert "seeded-secret" not in caplog.text
    assert "optional completion audit failed" in caplog.text


def test_recursive_redaction_and_bounds():
    token = ".".join(["eyJhbGciOiJub25lIn0", "eyJzdWIiOiJzZWVkIn0", "signature"])
    value = {
        "Authorization": "Bearer do-not-export",
        "nested": [{"api_key": "secret", "text": "Bearer another-secret " + token + " sk-proj-" + "abcdefghijklmnop"}],
        "headers": {"other": "secret"},
        "caller": {"sub": "private"},
        "huge": "x" * 100000,
    }
    result = clean(value)
    serialized = json.dumps(result)
    for forbidden in ("do-not-export", "another-secret", token, "sk-proj-" + "abcdefghijklmnop", "private"):
        assert forbidden not in serialized
    assert len(result["huge"]) <= 4096 and result["huge"].endswith("[truncated]")
    assert "headers" not in result and "caller" not in result


def test_outbox_is_written_before_export_and_queue():
    service, _ = make_service()
    sink = Sink()
    order = []
    sink.env["FINPLAN_JUDGE_OUTBOX_BUCKET"] = "test-outbox"

    def store(**kwargs):
        assert kwargs["IfNoneMatch"] == "*" and kwargs["ServerSideEncryption"] == "AES256"
        order.append("outbox")

    sink.put_object = store
    sink.export = lambda d: order.append("export")
    sink.send_message = lambda **kwargs: order.append("queue")
    AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert order == ["outbox", "export", "queue"]


def test_document_failure_cannot_change_answer(monkeypatch, caplog):
    from finplan_agent.runtime.audit import Turn

    service, _ = make_service()
    sink = Sink()

    def broken(*args, **kwargs):
        raise ValueError("seeded-document-error")

    monkeypatch.setattr(Turn, "document", broken)
    final = AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert final["status"] == "completed" and sink.messages == []
    assert "seeded-document-error" not in caplog.text


def test_private_download_grants_are_absent_across_root_and_boundary_payloads():
    from finplan_agent.runtime.audit import Turn

    host = "https://cdn.example.com/file?"
    grant = host + "Signature=" + "seededgrant" + "&Expires=123&Key-Pair-Id=key"
    userinfo = "https://user:" + "seededpassword" + "@example.com/file"
    awsgrant = "https://bucket." + "s3.us-east-2.amazonaws.com/a?X-Amz-Signature=" + "seededawsgrant"
    runtime = "arn:aws:bedrock-agentcore:us-east-2:" + ("0" * 12) + ":runtime/main-123"
    turn = Turn({"prompt": grant}, "beta", None, runtime)
    turn.record("provider", "GENERATION", turn.start, {"text": userinfo}, {"text": grant})
    turn.record("tool", "TOOL", turn.start, {"download_url": awsgrant}, {"nested": {"url": grant}})
    document = turn.document({"session_id": SESSION, "status": "completed", "answer": {"narrative": userinfo}})
    encoded = json.dumps(document)
    for secret in ("seededgrant", "seededpassword", "seededawsgrant"):
        assert secret not in encoded
    assert document["metadata"]["source_runtime_arn"] == runtime
    assert document["metadata"]["expected_observation_count"] == len(otlp(document)["resourceSpans"][0]["scopeSpans"][0]["spans"])


def test_boundary_recorder_failure_preserves_success_and_original_error(monkeypatch):
    from finplan_agent.runtime.audit import _CURRENT, Turn, wrap_tools

    service, _ = make_service()
    sink = Sink()

    def broken(*args, **kwargs):
        raise RuntimeError("seeded-capture-error")

    monkeypatch.setattr(Turn, "record", broken)
    final = AuditedService(service, sink).handle(question(), auth(), SESSION)
    assert final["status"] == "completed"
    assert sink.documents[0]["metadata"]["evidence_capture_failed"] is True

    class BrokenTool:
        def call_tool(self, name, arguments):
            raise original

    original = ValueError("original tool error")
    token = _CURRENT.set(Turn({}, "beta", None, "test-runtime"))
    try:
        tool = wrap_tools(BrokenTool())
        try:
            tool.call_tool("check", {})
        except ValueError as caught:
            assert caught is original
        else:
            raise AssertionError("original error was swallowed")
    finally:
        _CURRENT.reset(token)


def test_confirmation_output_preserves_approval_request_without_executing_tool():
    from tests.fakes.agent import PL

    service, tools = make_service()
    sink = Sink()
    final = AuditedService(service, sink).handle({"prompt": "Publish it", "tool_request": {"name": "publish_plan_version", "arguments": {"plan_id": PL, "plan_version_id": PV, "expected_revision": 3}}, "stream": False}, auth(), SESSION)
    assert final["status"] == "awaiting_confirmation"
    assert tools.calls == []
    assert sink.documents[0]["output"]["confirmation"] == final["confirmation"]
    assert not any(e["kind"] == "TOOL" for e in sink.documents[0]["events"])
