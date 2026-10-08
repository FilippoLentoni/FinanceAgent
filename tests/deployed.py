"""Helpers of the deployed suites (``tests/integration_beta``, ``tests/gamma``, ``tests/smoke``). They run
only in the pipeline's stage projects with ``FINPLAN_TARGET_ENV`` set and the stage role's REAL
credentials (lesson L5); offline every deployed test is skipped and the offline harness is absent.

They send REAL payloads to the deployed environment, exactly as a client would:

* a ``ci_test`` access token from the environment's Cognito pool (client-credentials grant; the client
  secret is read from Secrets Manager by the NAME in ``/finplan/<env>/financeagent/secret-ref/ci-test-client``,
  the token endpoint from ``authorizer-metadata-ref``);
* the hosted agent through the AgentCore Runtime OAuth invocation endpoint
  ``https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded runtime ARN>/invocations?qualifier=DEFAULT``
  (Runtime ARN from ``runtime-ref``), with the session header, parsing the SSE stream;
* the Gateway over MCP with the same token (endpoint from ``gateway-endpoint-ref``), through the
  agent's own :class:`~finplan_agent.tools.mcp_client.GatewayMcpClient`.

Nothing here hard-codes an account, ARN, pool, client or endpoint.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pytest

__all__ = ["DeployedEnv", "deployed", "requires_deployed", "runtime_invocation_url", "parse_sse"]

requires_deployed = pytest.mark.skipif(not os.environ.get("FINPLAN_TARGET_ENV"), reason="deployed suite: runs only in the pipeline stage (FINPLAN_TARGET_ENV)")
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


def runtime_invocation_url(region: str, runtime_arn: str, qualifier: str = "DEFAULT") -> str:
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{urllib.parse.quote(runtime_arn, safe='')}/invocations?qualifier={qualifier}"


def parse_sse(body: bytes) -> list[dict[str, Any]]:
    out = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


@dataclass
class DeployedEnv:
    env: str
    session: Any
    _token: str | None = field(default=None, repr=False)

    @property
    def region(self) -> str:
        return str(self.session.region_name or "us-east-2")

    @property
    def names(self) -> Any:
        from finplan_agent.config.settings import SsmNames

        return SsmNames(self.env)

    def param(self, name: str) -> str:
        value = self.session.client("ssm").get_parameter(Name=name)["Parameter"]["Value"]
        assert value, f"{name} is empty"
        return value

    def ci_token(self) -> str:
        if self._token:
            return self._token
        meta = json.loads(self.param(self.names.authorizer_metadata_ref))
        secret_name = self.param(self.names.ci_test_client_secret_ref)
        secret = json.loads(self.session.client("secretsmanager").get_secret_value(SecretId=secret_name)["SecretString"])
        basic = base64.b64encode(f"{secret['client_id']}:{secret['client_secret']}".encode()).decode()
        req = urllib.request.Request(meta["token_endpoint"], data=b"grant_type=client_credentials", headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            self._token = json.loads(resp.read())["access_token"]
        return self._token

    def invoke_agent(self, payload: dict[str, Any], session_id: str, *, token: str | None = None) -> tuple[int, Any]:
        url = runtime_invocation_url(self.region, self.param(self.names.runtime_ref))
        headers = {"Authorization": f"Bearer {token or self.ci_token()}", "Content-Type": "application/json", "Accept": "text/event-stream, application/json", SESSION_HEADER: session_id}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:  # noqa: S310
                body, ctype, status = resp.read(), resp.headers.get("content-type", ""), resp.status
        except urllib.error.HTTPError as exc:
            return exc.code, (exc.read() or b"").decode("utf-8", errors="replace")
        return status, parse_sse(body) if "text/event-stream" in ctype else json.loads(body)

    def own(self, category: str, name: str) -> str:
        return f"/finplan/{self.env}/financeagent/{category}/{name}"

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.param(self.own("release", "manifest")))

    def registered_targets(self) -> dict[str, str]:
        """Tool -> Lambda reference registered by the deployed release (``none`` = not registered)."""
        return {t: v for t, v in json.loads(self.param(self.own("agent", "gateway-targets"))).items() if v != "none"}

    def control(self) -> Any:
        """Read-only AgentCore control-plane client (stage role: Get/List on its own environment only)."""
        return self.session.client("bedrock-agentcore-control")

    def raw_gateway_post(self, body: dict[str, Any], token: str | None) -> int:
        """HTTP status of one raw MCP POST (for the unauthenticated checks)."""
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.param(self.names.gateway_endpoint_ref), data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    def gateway(self, *, token: str | None = None) -> Any:
        from finplan_agent.tools.mcp_client import GatewayMcpClient

        tok = token or self.ci_token()
        return GatewayMcpClient(self.param(self.names.gateway_endpoint_ref), lambda: tok)


def deployed() -> DeployedEnv:
    import boto3

    from tests.harness import OFFLINE_MARKER

    assert not os.environ.get(OFFLINE_MARKER), "deployed suites must run with the stage role's real credentials, never the offline fakes (lesson L5)"
    env = os.environ["FINPLAN_TARGET_ENV"]
    assert env in ("beta", "gamma", "prod")
    return DeployedEnv(env=env, session=boto3.session.Session(region_name=os.environ.get("AWS_REGION", "us-east-2")))
