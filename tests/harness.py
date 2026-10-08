"""Offline test harness (FinanceModel/FinancialPlanning pattern; lesson L5). Installed for every
offline suite (``tests/unit``, ``tests/graph``, ``tests/contract``); never for the deployed suites
(``tests/integration_beta``, ``tests/gamma``, ``tests/smoke``), which the stage runner starts with
``FINPLAN_TARGET_ENV`` set and the stage role's REAL credentials.

While installed:

* **No network** except loopback (local HTTP tests of the Runtime app and the MCP transport).
* **No real AWS call**: botocore's HTTP sender raises :class:`AwsCallBlocked`; moto still answers
  in-process.
* **No Bedrock model invocation at all** (FA-PRV-14, "No Bedrock calls in CI"): every
  ``bedrock-runtime`` operation raises :class:`AwsCallBlocked`, even through moto, and is counted in
  :data:`BEDROCK_ATTEMPTS`. Provider tests use a stubbed client object instead of botocore.
* **Fake credentials only**: profile, container, web-identity and file credential sources are removed.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest

__all__ = ["OFFLINE_MARKER", "AwsCallBlocked", "NetworkBlocked", "BEDROCK_ATTEMPTS", "install", "uninstall"]

OFFLINE_MARKER = "FINPLAN_OFFLINE_TESTS"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "", "testserver"}
_STATE: dict[str, Any] = {"installed": False}
_ORIG: dict[Any, Any] = {}
#: Every attempted Bedrock Runtime operation (must stay empty: FA-PRV-14).
BEDROCK_ATTEMPTS: list[str] = []


class NetworkBlocked(RuntimeError):
    pass


class AwsCallBlocked(RuntimeError):
    pass


def _host_of(address: Any) -> str:
    return str(address[0]) if isinstance(address, tuple) and address else str(address)


def _guarded_connect(orig: Any) -> Any:
    def connect(self: socket.socket, address: Any) -> Any:
        if getattr(socket, "AF_UNIX", None) is not None and self.family == socket.AF_UNIX:
            return orig(self, address)
        host = _host_of(address)
        if host in _LOCAL_HOSTS:
            return orig(self, address)
        raise NetworkBlocked(f"network access is blocked in tests (attempted connection to {host})")

    return connect


def _guarded_getaddrinfo(orig: Any) -> Any:
    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is None or str(host) in _LOCAL_HOSTS:
            return orig(host, *args, **kwargs)
        raise NetworkBlocked(f"DNS resolution is blocked in tests ({host})")

    return getaddrinfo


def install() -> None:
    if _STATE["installed"]:
        return
    from tests.offline_env import CREDENTIAL_SOURCES, apply_offline_environment

    for k in (
        *CREDENTIAL_SOURCES,
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_EC2_METADATA_DISABLED",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "FINPLAN_RELEASE_BUILD",
        OFFLINE_MARKER,
        "FINPLAN_ENV",
        "FINPLAN_MEMORY_ID",
        "FINPLAN_GATEWAY_URL",
        "FINPLAN_RELEASE_ID",
    ):
        _ORIG.setdefault(("env", k), os.environ.get(k))
    apply_offline_environment()
    for k in ("FINPLAN_ENV", "FINPLAN_MEMORY_ID", "FINPLAN_GATEWAY_URL", "FINPLAN_RELEASE_ID"):
        os.environ.pop(k, None)

    _ORIG["connect"] = socket.socket.connect
    _ORIG["connect_ex"] = socket.socket.connect_ex
    _ORIG["getaddrinfo"] = socket.getaddrinfo
    socket.socket.connect = _guarded_connect(_ORIG["connect"])  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect(_ORIG["connect_ex"])  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo(_ORIG["getaddrinfo"])  # type: ignore[assignment]

    import botocore.client
    import botocore.httpsession

    _ORIG["send"] = botocore.httpsession.URLLib3Session.send

    def blocked_send(self: Any, request: Any) -> Any:
        raise AwsCallBlocked("real AWS HTTP request blocked in tests")

    botocore.httpsession.URLLib3Session.send = blocked_send  # type: ignore[method-assign]
    _ORIG["make_api_call"] = botocore.client.BaseClient._make_api_call

    def guarded(self: Any, operation_name: str, api_params: Any) -> Any:
        service = self.meta.service_model.service_name
        if service in ("bedrock-runtime", "bedrock"):
            BEDROCK_ATTEMPTS.append(f"{service}:{operation_name}")
            raise AwsCallBlocked(f"Bedrock call blocked by the test harness: {operation_name} (CI never calls Bedrock; use a stubbed client)")
        return _ORIG["make_api_call"](self, operation_name, api_params)

    botocore.client.BaseClient._make_api_call = guarded  # type: ignore[method-assign]
    _STATE["installed"] = True


def uninstall() -> None:
    if not _STATE["installed"]:
        return
    socket.socket.connect = _ORIG.pop("connect")  # type: ignore[method-assign]
    socket.socket.connect_ex = _ORIG.pop("connect_ex")  # type: ignore[method-assign]
    socket.getaddrinfo = _ORIG.pop("getaddrinfo")  # type: ignore[assignment]
    import botocore.client
    import botocore.httpsession

    botocore.httpsession.URLLib3Session.send = _ORIG.pop("send")  # type: ignore[method-assign]
    botocore.client.BaseClient._make_api_call = _ORIG.pop("make_api_call")  # type: ignore[method-assign]
    for key in [k for k in _ORIG if isinstance(k, tuple) and k[0] == "env"]:
        val = _ORIG.pop(key)
        if val is None:
            os.environ.pop(key[1], None)
        else:
            os.environ[key[1]] = val
    _STATE["installed"] = False


def pytest_configure(config: pytest.Config) -> None:
    install()


def pytest_unconfigure(config: pytest.Config) -> None:
    uninstall()


@pytest.fixture(autouse=True)
def _offline_harness() -> Iterator[None]:
    install()
    yield
