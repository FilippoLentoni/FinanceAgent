"""Gateway MCP client (tool-call node transport; FA-GW-06 agent side, FA-POL-01 single path)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from finplan_agent.core.errors import AgentError
from finplan_agent.tools.catalog import ToolCatalog, load_tool_catalog
from finplan_agent.tools.mcp_client import GatewayMcpClient, HttpResponse
from tests.fakes.agent import PV, catalog_document

URL = "https://gw.example.invalid/mcp"


class FakeGateway:
    def __init__(self, *, sse=False, status=200, tool_payload=None, oversized=False):
        self.requests = []
        self.sse, self.status, self.oversized = sse, status, oversized
        self.tool_payload = tool_payload if tool_payload is not None else {"plan_version_id": PV, "checksum": "sha256:" + "ab" * 32}

    def __call__(self, url, headers, body, timeout):
        msg = json.loads(body)
        self.requests.append((dict(headers), msg))
        if self.status != 200:
            return HttpResponse(self.status, {}, b'{"error":"denied"}')
        if "id" not in msg:
            return HttpResponse(202, {}, b"")
        m = msg["method"]
        if m == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}
        elif m == "tools/list":
            if not msg["params"].get("cursor"):
                result = {"tools": [{"name": "plans___get_plan_version", "description": "Read", "inputSchema": {"type": "object"}}], "nextCursor": "p2"}
            else:
                result = {"tools": [{"name": "caps___describe_capabilities", "description": "Caps", "inputSchema": {"type": "object"}}]}
        elif m == "tools/call":
            text = json.dumps(self.tool_payload)
            if self.oversized:
                text = "x" * 300000
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            return HttpResponse(200, {"content-type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}}).encode())
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        if self.sse:
            return HttpResponse(200, {"content-type": "text/event-stream", "mcp-session-id": "sess-1"}, f"event: message\ndata: {json.dumps(reply)}\n\n".encode())
        return HttpResponse(200, {"content-type": "application/json"}, json.dumps(reply).encode())


def client(gw, token="tok-user-a"):
    return GatewayMcpClient(URL, lambda: token, transport=gw, max_response_bytes=262144)


@pytest.mark.parametrize("sse", [False, True])
def test_list_and_call_with_user_token(sse):
    gw = FakeGateway(sse=sse)
    c = client(gw)
    assert [t.name for t in c.list_tools()] == ["get_plan_version", "describe_capabilities"]
    out = c.call_tool("get_plan_version", {"plan_version_id": PV})
    assert out.ok and out.result["plan_version_id"] == PV
    methods = [m["method"] for _, m in gw.requests]
    assert methods[:2] == ["initialize", "notifications/initialized"] and methods.count("tools/list") == 2
    call = [m for _, m in gw.requests if m["method"] == "tools/call"][0]
    assert call["params"]["name"] == "plans___get_plan_version"
    for headers, _ in gw.requests:
        assert headers["Authorization"] == "Bearer tok-user-a" and headers["MCP-Protocol-Version"] == "2025-11-25"
        assert "text/event-stream" in headers["Accept"]
    if sse:
        assert gw.requests[-1][0].get("Mcp-Session-Id") == "sess-1"


def test_tool_error_envelope_is_a_failed_outcome():
    env = {"code": "FORBIDDEN", "message": "no", "retryable": False, "details": {}, "correlation_id": "corr-12345678", "contract_version": "1.0.0"}
    out = client(FakeGateway(tool_payload=env)).call_tool("get_plan_version", {})
    assert not out.ok and out.error["code"] == "FORBIDDEN"


@pytest.mark.parametrize("status,code", [(401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (429, "RATE_LIMITED"), (503, "DEPENDENCY_UNAVAILABLE")])
def test_http_errors_map_to_contract_codes(status, code):
    with pytest.raises(AgentError) as e:
        client(FakeGateway(status=status)).list_tools()
    assert e.value.code == code


def test_oversized_result_is_refused():
    out = client(FakeGateway(oversized=True)).call_tool("get_plan_version", {})
    assert not out.ok and out.error["details"]["reason"] == "response_too_large"


def test_unknown_tool_and_missing_token():
    assert client(FakeGateway()).call_tool("place_order", {}).error["code"] == "NOT_FOUND"
    with pytest.raises(AgentError) as e:
        client(FakeGateway(), token="").list_tools()
    assert e.value.code == "UNAUTHORIZED"


def test_https_required():
    with pytest.raises(ValueError):
        GatewayMcpClient("http://gw.example.invalid/mcp", lambda: "t")


def test_urllib_transport_over_loopback():
    gw = FakeGateway()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            resp = gw(self.path, dict(self.headers), body, 5)
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            self.wfile.write(resp.body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = GatewayMcpClient(f"http://127.0.0.1:{srv.server_port}/mcp", lambda: "tok")
        assert c.call_tool("get_plan_version", {"plan_version_id": PV}).ok
    finally:
        srv.shutdown()


def test_catalog_validation_and_state_changing_flags():
    cat = ToolCatalog.from_document(catalog_document("gamma"), environment="gamma")
    assert cat.state_changing("publish_plan_version") and not cat.state_changing("get_plan")
    assert cat.state_changing("unknown_tool")  # fail safe
    assert cat.is_denied("place_live_order") and cat.is_denied("wallet_transfer")
    with pytest.raises(AgentError) as e:
        ToolCatalog.from_document(catalog_document("beta"), environment="gamma")
    assert e.value.code == "DEPENDENCY_UNAVAILABLE"
    bad = catalog_document("gamma")
    bad["tools"][0]["input_schema_id"] = "https://contracts.finplan.invalid/core/v1/tools/nope.json"
    with pytest.raises(AgentError):
        ToolCatalog.from_document(bad, environment="gamma")


# ------------------------------------------------------------------ catalog pointer (deployed FinanceLambdasTool format)
_FAKE_ACCOUNT = "0" * 12  # obviously fake, built at run time (no 12-digit literal in files)


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class _FakeS3:
    """``get_object`` over an in-memory store (no network); records every read."""

    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects, self.reads = objects, []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 casing
        self.reads.append((Bucket, Key))
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[(Bucket, Key)])}


class _FakeSsm:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get_parameter(self, Name):  # noqa: N803 - boto3 casing
        return {"Parameter": {"Value": self.values[Name]}}


def _pointer_setup(env="gamma", *, account=_FAKE_ACCOUNT, mutate=None):
    import hashlib

    full = catalog_document(env)
    full["release_id"] = "rel_01M4DEV4W1NTDNPV1E0DCWQ0JJ"
    body = json.dumps(full).encode()
    bucket = f"finplan-shared-financelambdastool-pipeline-store-{account}"
    key = f"releases/{full['release_id']}/tool-catalog/{env}.json"
    pointer = {"kind": "tool-catalog-pointer", "environment": env, "release_id": full["release_id"], "s3_uri": f"s3://{bucket}/{key}", "sha256": hashlib.sha256(body).hexdigest(), "tools": sorted(t["name"] for t in full["tools"])}
    if mutate:
        mutate(pointer)
    s3 = _FakeS3({(bucket, key): body})
    ssm = _FakeSsm({f"/finplan/{env}/financelambdastool/contract/tool-catalog": json.dumps(pointer)})
    return ssm, s3, full


def test_catalog_pointer_is_followed_digest_checked_and_validated():
    ssm, s3, full = _pointer_setup()
    cat = load_tool_catalog(ssm, "gamma", s3=s3)
    assert set(cat.entries) == {t["name"] for t in full["tools"]} and cat.release_id == full["release_id"]
    assert cat.state_changing("publish_plan_version") and not cat.state_changing("get_plan")
    assert len(s3.reads) == 1


@pytest.mark.parametrize(
    "case,mutate",
    [
        ("digest", lambda p: p.update(sha256="0" * 64)),
        ("other-env-object", lambda p: p.update(s3_uri=p["s3_uri"].replace("/gamma.json", "/beta.json"))),
        ("other-bucket", lambda p: p.update(s3_uri=p["s3_uri"].replace("financelambdastool-pipeline-store", "financeagent-pipeline-store"))),
        ("other-release", lambda p: p.update(release_id="rel_01M4DEV4W1NTDNPV1E0DCWQ0JK")),
        ("names", lambda p: p.update(tools=p["tools"][1:])),
        ("pointer-env", lambda p: p.update(environment="beta")),
    ],
)
def test_catalog_pointer_mismatch_is_dependency_unavailable(case, mutate):
    ssm, s3, _full = _pointer_setup(mutate=mutate)
    with pytest.raises(AgentError) as e:
        load_tool_catalog(ssm, "gamma", s3=s3)
    assert e.value.code == "DEPENDENCY_UNAVAILABLE", case


def test_catalog_pointer_without_s3_client_fails_closed():
    ssm, _s3, _full = _pointer_setup()
    with pytest.raises(AgentError) as e:
        load_tool_catalog(ssm, "gamma")
    assert e.value.code == "DEPENDENCY_UNAVAILABLE"
