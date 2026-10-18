"""Existing beta read-only machine authentication, with no credential persistence.

This uses the same client-credentials interface as the deployed verification and
daily report clients; it does not create users or broaden scopes.

The machine client is selectable: ``ci-test-client`` (default, the pipeline test principal) or
``modeling-runner-client`` (the FinanceModel modeling runner's dedicated client, same ``ci_test``
scope). Pass ``client=`` or set ``FINPLAN_AGENT_MACHINE_CLIENT``.
"""
import base64
import json
import os
import urllib.parse
import urllib.request

DEFAULT_CLIENT = "ci-test-client"
CLIENTS = ("ci-test-client", "modeling-runner-client")
CLIENT_ENV = "FINPLAN_AGENT_MACHINE_CLIENT"
REFS = {"meta": "/finplan/{}/financeagent/agent/authorizer-metadata-ref",
        "secret": "/finplan/{}/financeagent/secret-ref/ci-test-client",
        "runtime": "/finplan/{}/financeagent/agent/runtime-ref"}
SECRET_REF = "/finplan/{env}/financeagent/secret-ref/{client}"


def machine_client(client=None):
    """The selected machine client name (argument, then ``FINPLAN_AGENT_MACHINE_CLIENT``, then default)."""
    name = client or os.environ.get(CLIENT_ENV) or DEFAULT_CLIENT
    if name not in CLIENTS:
        raise ValueError(f"unknown machine client {name!r}; expected one of {', '.join(CLIENTS)}")
    return name


def secret_ref(env, client=None):
    return SECRET_REF.format(env=env, client=machine_client(client))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def machine_token(ssm, secrets, env, opener, client=None):
    if env != "beta":
        raise ValueError("automatic comparison machine access is beta only")
    meta = json.loads(ssm.get_parameter(Name=REFS["meta"].format(env))["Parameter"]["Value"])
    endpoint = urllib.parse.urlsplit(meta["token_endpoint"])
    if endpoint.scheme != "https" or not (endpoint.hostname or "").endswith(".amazoncognito.com") or endpoint.path != "/oauth2/token" or endpoint.query:
        raise ValueError("invalid token endpoint")
    secret_id = ssm.get_parameter(Name=secret_ref(env, client))["Parameter"]["Value"]
    secret = json.loads(secrets.get_secret_value(SecretId=secret_id)["SecretString"])
    basic = base64.b64encode((secret["client_id"] + ":" + secret["client_secret"]).encode()).decode()
    request = urllib.request.Request(meta["token_endpoint"], data=b"grant_type=client_credentials", headers={
        "Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(request, timeout=30) as response:
        token = json.load(response)
    if str(token.get("token_type", "")).lower() != "bearer" or not token.get("access_token"):
        raise ValueError("invalid machine token response")
    return token["access_token"], [secret["client_secret"], basic]
