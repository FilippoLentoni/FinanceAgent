"""Recommendation run log and trigger handler (OpenSpec add-recommendation-trigger-and-run-log 1.3, 2.1).

Hosted responses are minimal sanitized excerpts of recorded beta receipts
(``tests/fixtures/recommendation_runs/hosted_finals.json``); SSM, Secrets Manager, DynamoDB and HTTP
are fakes, so nothing leaves the process.
"""

from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import urllib.error
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from finplan_agent.recommendation_runs import runlog, trigger

ROOT = Path(__file__).resolve().parents[2]
FINALS = json.loads((ROOT / "tests/fixtures/recommendation_runs/hosted_finals.json").read_text())
PID = "pf_01M547WFC8S9W35652YX87Z204"
TOKEN = "tok-" + "z" * 40
SETTINGS = {"lookback_days": 60, "risk_aversion": 2.0, "cvar_alpha": 0.9, "max_weight": 0.6, "cash_weight": 0.0, "horizon_sessions": 21, "turnover_penalty": 0.001}
NOW = datetime(2026, 10, 17, 16, 0, 7, tzinfo=UTC)
COGNITO = "https://finplan-beta.auth.us-east-2.amazoncognito.com/oauth2/"


class ConditionalFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.writes: list[dict] = []

    def put_item(self, Item, ConditionExpression=None):  # noqa: N803
        key = (Item["pk"], Item["sk"])
        if ConditionExpression == "attribute_not_exists(pk)" and key in self.items:
            raise ConditionalFailed()
        assert not any(isinstance(v, float) for v in _leaves(Item)), "floats must be Decimal"
        self.items[key] = copy.deepcopy(Item)
        self.writes.append(copy.deepcopy(Item))

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):  # noqa: N803
        assert UpdateExpression == "ADD n :one" and ConditionExpression == "attribute_not_exists(n) OR n < :cap"
        item = self.items.setdefault((Key["pk"], Key["sk"]), dict(Key))
        if "n" in item and item["n"] >= ExpressionAttributeValues[":cap"]:
            raise ConditionalFailed()
        item["n"] = item.get("n", 0) + ExpressionAttributeValues[":one"]


def _leaves(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)
    else:
        yield value


class FakeSsm:
    VALUES = {
        "/finplan/beta/financeagent/agent/authorizer-metadata-ref": json.dumps(dict(token_endpoint=COGNITO + "token")),
        "/finplan/beta/financeagent/secret-ref/ci-test-client": "finplan/beta/financeagent/ci-test-client",
        "/finplan/beta/financeagent/agent/runtime-ref": "runtime-ref-placeholder",
    }

    def __init__(self):
        self.names: list[str] = []

    def get_parameter(self, Name):  # noqa: N803
        self.names.append(Name)
        return {"Parameter": {"Value": self.VALUES[Name]}}


class FakeSecrets:
    def get_secret_value(self, SecretId):  # noqa: N803
        assert SecretId == "finplan/beta/financeagent/ci-test-client"
        return {"SecretString": json.dumps({"client_id": "client-id-x", "client_secret": "client-secret-" + "q" * 30})}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Token endpoint plus the Runtime; ``answers`` maps method -> final (dict) or Exception."""

    def __init__(self, answers=None, token_error=None):
        self.answers = answers if answers is not None else {m: FINALS[m] for m in runlog.METHODS}
        self.token_error = token_error
        self.calls: list[dict] = []

    def open(self, request, timeout):
        if request.full_url.endswith("/oauth2/token"):
            assert request.headers["Authorization"].startswith("Basic ") and request.data == b"grant_type=client_credentials"
            if self.token_error:
                raise self.token_error
            return Response(json.dumps({"token_type": "Bearer", "access_token": TOKEN, "expires_in": 3600}).encode())
        payload = json.loads(request.data)
        args = payload["tool_request"]["arguments"]
        method = args.get("algorithm", "ppo")
        self.calls.append({"url": request.full_url, "payload": payload, "headers": dict(request.headers), "timeout": timeout})
        answer = self.answers[method]
        if isinstance(answer, Exception):
            raise answer
        return Response(json.dumps(answer).encode())


def cfg(**kw):
    return {"env": "beta", "table": "finplan-beta-financeagent-recommendation-runs", "portfolio_ids": [PID], "methods": list(runlog.METHODS),
            "daily_cap": 40, "excerpt_bytes": 2048, "settings": SETTINGS, "region": "us-east-2", **kw}


def run(event, table=None, opener=None, now=NOW, **kw):
    table = table if table is not None else FakeTable()
    opener = opener or FakeOpener()
    out = trigger.run(event, cfg(**kw), table, FakeSsm(), FakeSecrets(), opener, now)
    return out, table, opener


def stored(table, run_id):
    return table.items[(run_id, PID)]


# ------------------------------------------------------------------ run log helpers
def test_tool_requests_match_the_daily_report_shapes():
    assert runlog.tool_request("ppo", PID, SETTINGS) == ("recommend_portfolio", {"portfolio_id": PID})
    for m in ("min_variance", "mean_variance", "cvar"):
        assert runlog.tool_request(m, PID, SETTINGS) == ("recommend_classical_portfolio", {"portfolio_id": PID, "algorithm": m, "settings": SETTINGS})
    with pytest.raises(ValueError):
        runlog.tool_request("equal_weight", PID)


def test_scheduled_window_truncates_to_the_hour_in_utc():
    assert runlog.scheduled_window("2026-10-17T16:00:00Z", NOW) == "2026-10-17T16:00:00Z"
    assert runlog.scheduled_window("2026-10-17T12:59:59-04:00", NOW) == "2026-10-17T16:00:00Z"
    assert runlog.scheduled_window("<aws.scheduler.scheduled-time>", NOW) == "2026-10-17T16:00:00Z"
    assert runlog.on_demand_run_id(NOW, "0a1b2c3d") == "od-20261017T160007Z-0a1b2c3d"


def test_new_york_date_matches_the_tz_database_across_dst_changes():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for hours in range(0, 24 * 365 * 2, 1):
        at = start + timedelta(hours=hours)
        assert runlog.new_york_date(at) == at.astimezone(ZoneInfo("America/New_York")).date().isoformat(), at


def test_excerpt_is_bounded_in_utf8_bytes():
    text = "é" * 3000
    out = runlog.excerpt(text, 2049)
    assert len(out.encode()) <= 2049 and out == "é" * 1024


def test_method_entry_takes_input_identities_from_decision_provenance():
    e = runlog.method_entry("ppo", "recommend_portfolio", FINALS["ppo"], "sid-1", PID, 2048)
    rec = FINALS["ppo"]["answer"]["portfolio_decisions"][0]["recommendation"]
    assert e["status"] == "issued" and e["decision_id"] == FINALS["ppo"]["answer"]["portfolio_decisions"][0]["decision_id"]
    assert e["formation_at"] == rec["decision_at"] and e["completed_session"] == rec["as_of"] and e["input_snapshot_id"] == rec["input_snapshot_id"]
    prov = rec["diagnostics"]["news_evidence"]["provenance"]
    assert e["news"] == {"source_day": prov["source_day"], "window_start": prov["window_start"], "window_end": prov["window_end"],
                         "checksum": prov["document_checksum"], "quality": prov["quality_status"], "consumed": True}
    assert e["export_run_id"] == rec["export_run_id"] and e["policy_artifact_checksum"] == rec["policy_artifact_checksum"]
    assert e["portfolio_revision"] == rec["portfolio_state"]["revision"] and e["claim_check_passed"] is True
    assert "CASH" in e["targets"] and len(e["targets"]) <= runlog.MAX_TARGETS
    assert set(e["usage"]) == {"estimated_cost_usd", "input_tokens", "output_tokens"}
    classical = runlog.method_entry("cvar", "recommend_classical_portfolio", FINALS["cvar"], "sid-2", PID, 2048)
    assert classical["status"] == "issued" and classical["export_run_id"] is None and classical["policy_artifact_checksum"] is None


def test_method_entry_maps_not_due_and_mismatches():
    nd = runlog.method_entry("min_variance", "recommend_classical_portfolio", FINALS["not_due"], "s", PID, 2048)
    assert nd["status"] == "not_due" and nd["error_code"] == "morning_formation_not_due" and nd["decision_id"] is None
    wrong = runlog.method_entry("mean_variance", "recommend_classical_portfolio", FINALS["cvar"], "s", PID, 2048)
    assert wrong["status"] == "failed" and wrong["error_code"] == "requested_method_not_served" and wrong["decision_id"]
    other = runlog.method_entry("cvar", "recommend_classical_portfolio", FINALS["cvar"], "s", "pf_" + "1" * 26, 2048)
    assert other["status"] == "failed" and other["error_code"] == "portfolio_mismatch"
    empty = runlog.method_entry("ppo", "recommend_portfolio", {"status": "completed", "answer": {}}, "s", PID, 2048)
    assert empty["status"] == "failed" and empty["error_code"] == "HOSTED_EVIDENCE_CHECK_FAILED"


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["issued"] * 4, "completed"),
        (["issued", "failed", "issued", "issued"], "partial"),
        (["issued", "not_due", "not_due", "not_due"], "partial"),
        (["not_due"] * 4, "not_due"),
        (["not_due", "failed"], "failed"),
        (["failed"] * 4, "failed"),
        ([], "failed"),
    ],
)
def test_portfolio_status(states, expected):
    assert runlog.portfolio_status({str(i): {"status": s} for i, s in enumerate(states)}) == expected


def test_run_status_aggregates_portfolios():
    assert runlog.run_status(["completed", "completed"]) == "completed"
    assert runlog.run_status(["duplicate"]) == "duplicate"
    assert runlog.run_status(["completed", "failed"]) == "partial"
    assert runlog.run_status([]) == "failed"


# ------------------------------------------------------------------ handler: run outcomes
def test_completed_scheduled_run_records_four_decisions_with_inputs():
    out, table, opener = run({"trigger": "scheduled", "window": "2026-10-17T16:00:00Z"})
    run_id = "sched-2026-10-17T16:00:00Z"
    assert out["run_id"] == run_id and out["status"] == "completed"
    methods = out["portfolios"][0]["methods"]
    assert list(methods) == list(runlog.METHODS) and all(m["status"] == "issued" and m["decision_id"].startswith("pd_") for m in methods.values())
    # strictly sequential, one session per call, the exact report payloads, the bearer of the CI client
    assert [c["payload"]["tool_request"] for c in opener.calls] == [{"name": t, "arguments": a} for t, a in (runlog.tool_request(m, PID, SETTINGS) for m in runlog.METHODS)]
    assert all(c["payload"]["stream"] is False for c in opener.calls)
    sids = [c["headers"]["X-amzn-bedrock-agentcore-runtime-session-id"] for c in opener.calls]
    assert len(set(sids)) == 4 and all(len(s) >= 33 for s in sids)
    assert all(c["headers"]["Authorization"] == "Bearer " + TOKEN for c in opener.calls)
    assert opener.calls[0]["url"] == "https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/runtime-ref-placeholder/invocations?qualifier=DEFAULT"
    item = stored(table, run_id)
    assert item["status"] == "completed" and item["trigger"] == "scheduled" and item["window"] == "2026-10-17T16:00:00Z"
    assert item["env"] == "beta" and item["started_key"] == "2026-10-17T16:00:07Z#" + run_id and item["finished_at"]
    assert item["requested_methods"] == list(runlog.METHODS)
    ppo = item["methods"]["ppo"]
    assert isinstance(ppo["targets"]["CASH"], Decimal) and len(ppo["narrative_excerpt"].encode()) <= 2048
    assert ppo["session_id"] == sids[0] and ppo["tool"] == "recommend_portfolio"
    assert TOKEN not in json.dumps(table.writes, default=str) and "client-secret" not in json.dumps(table.writes, default=str)
    assert table.items[("cap#2026-10-17", "cap")]["n"] == 1


def test_repeated_runs_after_formation_record_the_same_formation_and_news():
    first, table, _ = run({"trigger": "scheduled", "window": "2026-10-17T14:00:00Z"})
    second, _, _ = run({"trigger": "scheduled", "window": "2026-10-17T15:00:00Z"}, table=table, now=NOW + timedelta(hours=1))
    a, b = stored(table, first["run_id"])["methods"], stored(table, second["run_id"])["methods"]
    for m in runlog.METHODS:
        assert (a[m]["formation_at"], a[m]["news"]) == (b[m]["formation_at"], b[m]["news"])


def test_partial_run_keeps_the_failing_method_error_code():
    answers = {m: FINALS[m] for m in runlog.METHODS}
    answers["mean_variance"] = urllib.error.HTTPError("u", 502, "bad gateway", {}, io.BytesIO(b"secret body"))
    out, table, _ = run({"trigger": "on_demand"}, opener=FakeOpener(answers))
    assert out["status"] == "partial" and out["run_id"].startswith("od-20261017T160007Z-")
    item = stored(table, out["run_id"])
    assert item["trigger"] == "on_demand" and item["window"] is None
    assert item["methods"]["mean_variance"] == {"tool": "recommend_classical_portfolio", "session_id": item["methods"]["mean_variance"]["session_id"], "decision_id": None,
                                                "error_code": "HOSTED_HTTP_502", "usage": {"estimated_cost_usd": None, "input_tokens": None, "output_tokens": None}, "status": "failed"}
    assert "secret body" not in json.dumps(table.writes, default=str)


def test_run_before_formation_is_not_due_without_substitutes():
    out, table, opener = run({"trigger": "scheduled", "window": "2026-10-17T12:00:00Z"}, opener=FakeOpener({m: FINALS["not_due"] for m in runlog.METHODS}))
    assert out["status"] == "not_due" and len(opener.calls) == 4
    item = stored(table, out["run_id"])
    assert all(m["status"] == "not_due" and m["error_code"] == "morning_formation_not_due" and m["decision_id"] is None for m in item["methods"].values())


def test_auth_failure_is_recorded_failed_without_invocation_or_secret():
    out, table, opener = run({"trigger": "on_demand"}, opener=FakeOpener(token_error=urllib.error.HTTPError("u", 400, "invalid_client client-secret-x", {}, io.BytesIO(b""))))
    assert out["status"] == "failed" and opener.calls == []
    item = stored(table, out["run_id"])
    assert item["error_code"] == "HOSTED_INITIALIZATION_UNAVAILABLE"
    assert all(m["error_code"] == "HOSTED_INITIALIZATION_UNAVAILABLE" for m in item["methods"].values())
    assert "client-secret" not in json.dumps(table.writes, default=str)


def test_duplicate_schedule_window_is_recorded_once_and_not_invoked():
    event = {"trigger": "scheduled", "window": "2026-10-17T16:00:00Z"}
    first, table, _ = run(event)
    opener = FakeOpener()
    again, _, _ = run(event, table=table, opener=opener)
    assert first["status"] == "completed" and again == {"run_id": first["run_id"], "status": "duplicate", "portfolios": [{"portfolio_id": PID, "status": "duplicate", "methods": {}}]}
    assert opener.calls == [] and table.items[("cap#2026-10-17", "cap")]["n"] == 1
    assert sum(1 for k in table.items if k[0] == first["run_id"]) == 1


def test_daily_cap_counts_on_demand_runs_and_refuses_without_invocation():
    table = FakeTable()
    first, _, _ = run({"trigger": "on_demand"}, table=table, daily_cap=1)
    opener = FakeOpener()
    second, _, _ = run({"trigger": "on_demand"}, table=table, opener=opener, daily_cap=1)
    assert first["status"] == "completed" and second["status"] == "cap_reached" and opener.calls == []
    assert first["run_id"] != second["run_id"] and stored(table, second["run_id"])["status"] == "cap_reached"
    # the counter is per America/New_York day: 03:30 UTC on Oct 18 is still Oct 17 in New York
    late, _, _ = run({"trigger": "on_demand"}, table=table, daily_cap=1, now=datetime(2026, 10, 18, 3, 30, tzinfo=UTC))
    assert late["status"] == "cap_reached"


def test_budget_refusal_fails_the_method_and_stops_further_calls():
    refusal = {"status": "failed", "error": {"code": "BUDGET_EXCEEDED", "message": "session cost cap"}, "answer": {"evidence": []}}
    answers = {m: FINALS[m] for m in runlog.METHODS}
    answers["ppo"] = refusal
    out, table, opener = run({"trigger": "on_demand"}, opener=FakeOpener(answers))
    methods = stored(table, out["run_id"])["methods"]
    assert len(opener.calls) == 1 and out["status"] == "failed"
    assert methods["ppo"]["error_code"] == "BUDGET_EXCEEDED"
    assert all(methods[m]["error_code"] == "SKIPPED_AFTER_BUDGET_EXCEEDED" for m in ("min_variance", "mean_variance", "cvar"))


def test_credential_echo_is_never_persisted():
    echo = copy.deepcopy(FINALS["ppo"])
    echo["answer"]["narrative"] = "token " + TOKEN
    answers = {m: FINALS[m] for m in runlog.METHODS}
    answers["ppo"] = echo
    out, table, _ = run({"trigger": "on_demand"}, opener=FakeOpener(answers))
    assert stored(table, out["run_id"])["methods"]["ppo"]["error_code"] == "CREDENTIAL_ECHO_REDACTED"
    assert TOKEN not in json.dumps(table.writes, default=str) and out["status"] == "partial"


def test_on_demand_methods_subset_and_order():
    out, table, opener = run({"trigger": "on_demand", "portfolio_ids": [PID], "methods": ["cvar", "ppo"]})
    assert list(out["portfolios"][0]["methods"]) == ["ppo", "cvar"] and len(opener.calls) == 2
    assert stored(table, out["run_id"])["requested_methods"] == ["ppo", "cvar"]


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"trigger": "manual"},
        {"trigger": "on_demand", "portfolio_ids": ["pf_" + "1" * 26]},
        {"trigger": "on_demand", "methods": ["equal_weight"]},
        {"trigger": "on_demand", "methods": "ppo"},
        {"trigger": "scheduled", "portfolio_ids": [PID]},
    ],
)
def test_invalid_requests_write_nothing(event):
    out, table, opener = run(event)
    assert out == {"run_id": None, "status": "failed", "error_code": "INVALID_REQUEST", "portfolios": []}
    assert table.writes == [] and opener.calls == []


def test_time_budget_skips_calls_that_cannot_finish():
    out = trigger.run({"trigger": "on_demand"}, cfg(), FakeTable(), FakeSsm(), FakeSecrets(), FakeOpener(), NOW, remaining_ms=lambda: 60_000)
    assert {m["error_code"] for m in out["portfolios"][0]["methods"].values()} == {"LAMBDA_TIME_EXHAUSTED"}


def test_handler_reads_its_configuration_from_the_environment(monkeypatch):
    table = FakeTable()
    clients = {"ssm": FakeSsm(), "secretsmanager": FakeSecrets()}
    fake_boto3 = SimpleNamespace(resource=lambda name, region_name: SimpleNamespace(Table=lambda n: table), client=lambda name, region_name: clients[name])
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setattr(trigger.urllib.request, "build_opener", lambda *_a: FakeOpener())
    for k, v in {"FINPLAN_ENV": "beta", "RUNS_TABLE": "t", "PORTFOLIO_IDS": json.dumps([PID]), "METHODS": json.dumps(["ppo"]), "DAILY_CAP": "40",
                 "EXCERPT_BYTES": "2048", "CLASSICAL_SETTINGS": json.dumps(SETTINGS), "AWS_REGION": "us-east-2"}.items():
        monkeypatch.setenv(k, v)
    out = trigger.handler({"trigger": "on_demand"}, SimpleNamespace(get_remaining_time_in_millis=lambda: 900_000))
    assert out["status"] == "completed" and list(out["portfolios"][0]["methods"]) == ["ppo"]


def test_token_endpoint_must_be_the_cognito_https_endpoint():
    ssm = FakeSsm()
    ssm.VALUES = {**FakeSsm.VALUES, "/finplan/beta/financeagent/agent/authorizer-metadata-ref": json.dumps(dict(token_endpoint=COGNITO.replace("https://finplan-beta.auth.us-east-2.amazoncognito.com", "http://evil.example") + "token"))}
    with pytest.raises(ValueError):
        trigger.machine_token(ssm, FakeSecrets(), "beta", FakeOpener())


# ------------------------------------------------------------------ deployable package
def test_lambda_package_is_deterministic_and_imports_with_stdlib_only(tmp_path):
    from infra.stacks.recommendation_runs import PACKAGE_FILES, code_key, lambda_package

    data, digest = lambda_package()
    assert lambda_package() == (data, digest) and code_key(digest).startswith("releases/lambda-code/recommendation-trigger-")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == sorted(f"recommendation_runs/{f}" for f in PACKAGE_FILES)
        zf.extractall(tmp_path)
    # isolated interpreter: no repository path, no finplan_agent, no boto3 needed at import time
    check = "import sys; import recommendation_runs.trigger as t; assert callable(t.handler); assert 'finplan_agent' not in sys.modules; assert 'boto3' not in sys.modules"
    subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); {check}"], check=True, cwd=tmp_path)
