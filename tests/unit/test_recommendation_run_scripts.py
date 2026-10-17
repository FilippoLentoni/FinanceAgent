"""Operator scripts: on-demand trigger invocation and read-only run-log queries (fakes only)."""
from __future__ import annotations

import io
import json

import pytest

from scripts import recommend_now, recommendation_runs
from tests.fakes.run_log import PF, PF_B, FakeRunTable, method, run_item

PD = "pd_01JA2B3C4D5E6F7G8H9JKMNPQR"


class FakeLambda:
    def __init__(self, response, *, function_error=None, error=None):
        self.response, self.function_error, self.error, self.calls = response, function_error, error, []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        out = {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(self.response).encode())}
        if self.function_error:
            out["FunctionError"] = self.function_error
        return out


RESULT = {"run_id": "od-01JABCDEFGHJKMNPQRSTVWXYZ6", "status": "partial", "portfolios": [
    {"portfolio_id": PF, "status": "partial", "methods": {
        "ppo": {"status": "issued", "decision_id": PD, "error_code": None},
        "cvar": {"status": "failed", "decision_id": None, "error_code": "DEPENDENCY_UNAVAILABLE"}}}]}


def test_payload_construction():
    assert recommend_now.build_payload() == {"trigger": "on_demand"}
    assert recommend_now.build_payload([PF, PF, PF_B], ["ppo", "cvar", "ppo"]) == {"trigger": "on_demand", "portfolio_ids": [PF, PF_B], "methods": ["ppo", "cvar"]}
    with pytest.raises(ValueError):
        recommend_now.build_payload(["pf_bad"])
    with pytest.raises(ValueError):
        recommend_now.build_payload(None, ["sac"])


def test_invoke_is_synchronous_and_summarised(capsys):
    client = FakeLambda(RESULT)
    code = recommend_now.main(["--env", "beta", "--portfolio-id", PF, "--method", "ppo", "--method", "cvar"], client=client)
    call = client.calls[0]
    assert call["FunctionName"] == "finplan-beta-financeagent-recommendation-trigger" and call["InvocationType"] == "RequestResponse"
    assert json.loads(call["Payload"]) == {"trigger": "on_demand", "portfolio_ids": [PF], "methods": ["ppo", "cvar"]}
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "run od-01JABCDEFGHJKMNPQRSTVWXYZ6: partial"
    assert PD in out and "DEPENDENCY_UNAVAILABLE" in out


@pytest.mark.parametrize("response,kwargs,expected", [
    ({**RESULT, "status": "completed"}, {}, 0),
    ({**RESULT, "status": "failed", "error_code": "AUTH_FAILED"}, {}, 1),
    ({"errorType": "Runtime.ExitError", "errorMessage": "x"}, {"function_error": "Unhandled"}, 1),
    ({"run_id": "x", "status": "cap_reached"}, {}, 2),
])
def test_exit_codes(response, kwargs, expected, capsys):
    assert recommend_now.main([], client=FakeLambda(response, **kwargs)) == expected
    if kwargs:
        assert "FUNCTION_ERROR" in capsys.readouterr().out


def test_invocation_error_reports_code_only(capsys):
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException", "Message": "caller identity secret-detail"}}

    assert recommend_now.main([], client=FakeLambda({}, error=Denied())) == 1
    err = capsys.readouterr().err
    assert "AccessDeniedException" in err and "secret-detail" not in err


def test_client_read_timeout_covers_lambda_timeout():
    assert recommend_now.READ_TIMEOUT_SECONDS >= 900
    client = recommend_now.lambda_client("us-east-2")
    assert client.meta.config.read_timeout >= 900 and client.meta.config.retries["total_max_attempts"] == 1


def _items():
    items = [run_item(h) for h in range(8, 14)]
    items.append(run_item(14, portfolio=PF_B, trigger="on_demand", status="partial",
                          methods={"ppo": method(n=1), "cvar": method(status="failed", error_code="DEPENDENCY_UNAVAILABLE")}))
    items.append(run_item(9, day=15))
    return items


def test_latest_lists_newest_first(capsys):
    table = FakeRunTable(_items())
    assert recommendation_runs.main(["latest", "-n", "3"], table=table) == 0
    out = capsys.readouterr().out
    runs = [line for line in out.splitlines() if line[:1].isdigit()]
    assert [r.split()[2] for r in runs] == ["od-2026-10-17T14-x14", "sched-2026-10-17T13:00:00Z", "sched-2026-10-17T12:00:00Z"]
    assert table.calls[0]["ScanIndexForward"] is False and table.calls[0]["IndexName"] == "recent"


def test_latest_filters_and_json(capsys):
    table = FakeRunTable(_items())
    assert recommendation_runs.main(["latest", "--method", "cvar", "--trigger", "on_demand", "--portfolio-id", PF_B, "--json"], table=table) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [i["run_id"] for i in doc["items"]] == ["od-2026-10-17T14-x14"] and doc["more"] is False
    assert recommendation_runs.main(["latest", "--since", "2026-10-15", "--until", "2026-10-15", "--json"], table=FakeRunTable(_items())) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [i["run_id"] for i in doc["items"]] == ["sched-2026-10-15T09:00:00Z"]
    assert isinstance(doc["items"][0]["methods"]["ppo"]["targets"]["VTI"], float)


def test_get_and_missing(capsys):
    table = FakeRunTable(_items())
    assert recommendation_runs.main(["get", "sched-2026-10-17T10:00:00Z"], table=table) == 0
    out = capsys.readouterr().out
    assert out.startswith("Run sched-2026-10-17T10:00:00Z:") and "decision pd_" in out
    assert table.calls[0]["KeyConditionExpression"] == "run_id = :r" and "IndexName" not in table.calls[0]
    assert recommendation_runs.main(["get", "nope"], table=table) == 1


def test_bad_arguments_rejected():
    with pytest.raises(SystemExit):
        recommendation_runs.main(["latest", "-n", "21"], table=FakeRunTable())
    with pytest.raises(SystemExit):
        recommendation_runs.main(["latest", "--since", "15/10/2026"], table=FakeRunTable())


def test_table_resolved_from_ssm():
    class Session:
        def __init__(self):
            self.names = []

        def client(self, name):
            assert name == "ssm"
            session = self

            class Ssm:
                def get_parameter(self, Name):
                    session.names.append(Name)
                    return {"Parameter": {"Value": "finplan-beta-financeagent-recommendation-runs\n"}}
            return Ssm()

        def resource(self, name):
            assert name == "dynamodb"

            class Resource:
                def Table(self, table):
                    return ("table", table)
            return Resource()

    session = Session()
    assert recommendation_runs.resolve_table(session, "beta") == ("table", "finplan-beta-financeagent-recommendation-runs")
    assert session.names == ["/finplan/beta/financeagent/config/recommendation-runs-table"]
