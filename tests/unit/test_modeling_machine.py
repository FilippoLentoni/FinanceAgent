import io
import json

import pytest

from finplan_agent.modeling import machine

ENDPOINT = "https://d.auth.us-east-2.amazoncognito.com/oauth2/" + "token"


class _SSM:
    def __init__(self):
        self.names = []

    def get_parameter(self, Name):
        self.names.append(Name)
        if Name.endswith("authorizer-metadata-ref"):
            return {"Parameter": {"Value": json.dumps({"token_endpoint": ENDPOINT})}}
        return {"Parameter": {"Value": "finplan/beta/financeagent/" + Name.rsplit("/", 1)[1]}}


class _Secrets:
    def __init__(self):
        self.ids = []

    def get_secret_value(self, SecretId):
        self.ids.append(SecretId)
        return {"SecretString": json.dumps({"client_id": "cid", "client_secret": "csecret"})}


class _Opener:
    def open(self, request, timeout):
        return io.BytesIO(json.dumps({"token_type": "Bearer", "access_token": "tok"}).encode())


@pytest.mark.parametrize("client, env_value, expected", [
    (None, None, "ci-test-client"),
    ("modeling-runner-client", None, "modeling-runner-client"),
    (None, "modeling-runner-client", "modeling-runner-client"),
    ("ci-test-client", "modeling-runner-client", "ci-test-client"),
])
def test_machine_client_is_selectable(monkeypatch, client, env_value, expected):
    if env_value:
        monkeypatch.setenv(machine.CLIENT_ENV, env_value)
    else:
        monkeypatch.delenv(machine.CLIENT_ENV, raising=False)
    ssm, secrets = _SSM(), _Secrets()
    token, sensitive = machine.machine_token(ssm, secrets, "beta", _Opener(), client=client)
    assert token == "tok" and "csecret" in sensitive
    assert f"/finplan/beta/financeagent/secret-ref/{expected}" in ssm.names
    assert secrets.ids == [f"finplan/beta/financeagent/{expected}"]


def test_unknown_machine_client_is_refused(monkeypatch):
    monkeypatch.delenv(machine.CLIENT_ENV, raising=False)
    with pytest.raises(ValueError, match="unknown machine client"):
        machine.machine_token(_SSM(), _Secrets(), "beta", _Opener(), client="other")
    monkeypatch.setenv(machine.CLIENT_ENV, "other")
    with pytest.raises(ValueError, match="unknown machine client"):
        machine.machine_client()
