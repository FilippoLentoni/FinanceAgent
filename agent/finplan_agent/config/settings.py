"""Deployment settings of one agent process: environment, release, wiring references, repo config.

The AgentCore Runtime is started by the per-environment Runtime stack with these environment variables
(set by IaC at deploy time; values never appear in repository files):

=========================  =====================================================================
``FINPLAN_ENV``            ``beta`` | ``gamma`` | ``prod`` (required)
``FINPLAN_RELEASE_ID``     the deployed ``release_id`` (``rel_`` + ULID; reported by ``describe``)
``FINPLAN_MEMORY_ID``      the environment's AgentCore Memory resource ID (checkpoints, sessions)
``FINPLAN_GATEWAY_URL``    the environment's AgentCore Gateway MCP endpoint (``https://.../mcp``)
``AWS_REGION``             set by the Runtime (``us-east-2``)
``FINPLAN_CONFIG_DIR``     the image's copy of ``config/`` (default: the repository ``config/``)
=========================  =====================================================================

The SSM parameter NAMES the agent reads are derived here from the environment and the contract SSM
convention (contracts D4); nothing else is configurable by name.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.errors import AgentError

__all__ = ["ENVIRONMENTS", "REPO", "Settings", "SsmNames", "load_repo_config", "config_dir"]

ENVIRONMENTS = ("beta", "gamma", "prod")
REPO = "financeagent"
_RELEASE_RE = re.compile(r"^rel_[0-7][0-9A-HJKMNP-TV-Z]{25}\Z")
_GATEWAY_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/mcp\Z")


def config_dir() -> Path:
    env = os.environ.get("FINPLAN_CONFIG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config"


@lru_cache(maxsize=8)
def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_repo_config(environment: str) -> dict[str, Any]:
    """The repository configuration of ``environment`` merged over ``shared.json``."""
    if environment not in ENVIRONMENTS:
        raise AgentError.validation(f"unknown environment {environment!r}", pointer="/environment")
    base = config_dir()
    shared = _load(str(base / "shared.json"))
    env = _load(str(base / f"{environment}.json"))
    return {**shared, **env}


@dataclass(frozen=True)
class SsmNames:
    """SSM parameter names the agent reads (all registered contract keys except ``explanation-guards``)."""

    environment: str

    @property
    def explanation_provider(self) -> str:
        return f"/finplan/{self.environment}/{REPO}/config/explanation-provider"

    @property
    def explanation_model_id(self) -> str:
        return f"/finplan/{self.environment}/{REPO}/config/explanation-model-id"

    @property
    def explanation_guards(self) -> str:
        # Not a registered contract key (CONTRACT GAP: the registered explanation-provider value shape
        # is a plain provider key, so the caps and rates of design D4 live in this FinanceAgent-owned
        # parameter of the same namespace).
        return f"/finplan/{self.environment}/{REPO}/config/explanation-guards"

    @property
    def explanation_limits(self) -> str:
        # Not a registered contract key (CONTRACT GAP): explanation request limits (design E7).
        return f"/finplan/{self.environment}/{REPO}/config/explanation-limits"

    # Published FinanceAgent references (written by the pipeline; read by clients and deployed suites).
    @property
    def runtime_ref(self) -> str:
        """The Runtime ARN reference (task 6.4)."""
        return f"/finplan/{self.environment}/{REPO}/agent/runtime-ref"

    @property
    def gateway_endpoint_ref(self) -> str:
        """The Gateway MCP endpoint URL (spec tool-gateway "Published Gateway references")."""
        return f"/finplan/{self.environment}/{REPO}/agent/gateway-endpoint-ref"

    @property
    def classical_gateway_endpoint_ref(self) -> str:
        return f"/finplan/{self.environment}/{REPO}/agent/classical-gateway-endpoint-ref"

    @property
    def user_pool_ref(self) -> str:
        return f"/finplan/{self.environment}/{REPO}/agent/user-pool-ref"

    @property
    def authorizer_metadata_ref(self) -> str:
        """JSON ``{"discovery_url", "issuer", "allowed_clients", "token_endpoint"}`` of the pool."""
        return f"/finplan/{self.environment}/{REPO}/agent/authorizer-metadata-ref"

    @property
    def ci_test_client_secret_ref(self) -> str:
        """Secret NAME of the ci_test client (secret JSON ``{"client_id", "client_secret"}``)."""
        return f"/finplan/{self.environment}/{REPO}/secret-ref/ci-test-client"

    @property
    def tool_catalog(self) -> str:
        return f"/finplan/{self.environment}/financelambdastool/contract/tool-catalog"

    budget_allocation: str = "/finplan/shared/financialplanning/config/budget-allocation"
    budget_state: str = "/finplan/shared/financialplanning/config/budget-state"
    cost_ceiling: str = "/finplan/shared/financialplanning/config/cost-ceiling-usd"


@dataclass(frozen=True)
class Settings:
    environment: str
    region: str
    release_id: str | None
    memory_id: str | None
    gateway_url: str | None
    repo_config: Mapping[str, Any] = field(default_factory=dict)
    classical_gateway_url: str | None = None

    @property
    def ssm(self) -> SsmNames:
        return SsmNames(self.environment)

    @property
    def websocket_enabled(self) -> bool:
        ws = self.repo_config.get("streaming", {}).get("websocket", {})
        return bool(ws.get("enabled")) and bool(ws.get("requirement_ref"))

    @property
    def gateway(self) -> Mapping[str, Any]:
        return self.repo_config.get("gateway", {})

    @property
    def usage_metrics(self) -> Mapping[str, Any]:
        return self.repo_config.get("usage_metrics", {})

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        environment = env.get("FINPLAN_ENV", "")
        if environment not in ENVIRONMENTS:
            raise AgentError.validation("FINPLAN_ENV must be beta, gamma or prod", pointer="/FINPLAN_ENV")
        release_id = env.get("FINPLAN_RELEASE_ID") or None
        if release_id is not None and not _RELEASE_RE.match(release_id):
            raise AgentError.validation("FINPLAN_RELEASE_ID is not a release_id", pointer="/FINPLAN_RELEASE_ID")
        gateway_url = env.get("FINPLAN_GATEWAY_URL") or None
        if gateway_url is not None and not _GATEWAY_URL_RE.match(gateway_url):
            raise AgentError.validation("FINPLAN_GATEWAY_URL must be the Gateway's https://<host>/mcp endpoint", pointer="/FINPLAN_GATEWAY_URL")
        classical_url = env.get("FINPLAN_CLASSICAL_GATEWAY_URL") or None
        if classical_url is not None and (not _GATEWAY_URL_RE.match(classical_url) or classical_url == gateway_url):
            raise AgentError.validation("FINPLAN_CLASSICAL_GATEWAY_URL must name a distinct HTTPS MCP endpoint", pointer="/FINPLAN_CLASSICAL_GATEWAY_URL")
        return cls(
            environment=environment,
            region=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-2",
            release_id=release_id,
            memory_id=env.get("FINPLAN_MEMORY_ID") or None,
            gateway_url=gateway_url,
            repo_config=load_repo_config(environment),
            classical_gateway_url=classical_url,
        )
