"""Contract error envelopes (``core/v1/error.json``) raised and returned by the agent.

Every error the agent returns to a caller, in a stream ``error`` event or in a ``final`` payload, is a
contract error envelope: registered ``code``, ``retryable`` from the contract registry, ``details``,
``correlation_id`` and ``contract_version``. Messages never carry stack traces, credentials, tokens,
ARNs or storage locations.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

__all__ = ["AgentError", "contract_version", "error_envelope", "registered_codes", "retryable_default"]


@lru_cache(maxsize=1)
def _registry() -> dict[str, dict[str, Any]]:
    from finplan_contracts.schemas import load_store

    return dict(load_store().get("error-codes").schema["x-finplan-error-codes"])


@lru_cache(maxsize=1)
def contract_version() -> str:
    """The pinned contract package version (reported by ``describe`` and in every envelope)."""
    from finplan_contracts.schemas import load_store

    return load_store().version


def registered_codes() -> frozenset[str]:
    return frozenset(_registry())


def retryable_default(code: str) -> bool:
    return bool(_registry()[code]["retryable"])


def error_envelope(code: str, message: str, *, correlation_id: str, details: dict[str, Any] | None = None, retryable: bool | None = None) -> dict[str, Any]:
    """A contract error envelope; ``VALIDATION_FAILED`` always carries a ``details.pointer``."""
    if code not in _registry():
        raise ValueError(f"unregistered error code {code!r}")
    reg = _registry()[code]
    if retryable is None or reg.get("retryable_fixed"):
        retryable = bool(reg["retryable"])
    det = dict(details or {})
    if code == "VALIDATION_FAILED":
        det.setdefault("pointer", "")
    return {
        "code": code,
        "message": message[:2000] or code,
        "retryable": retryable,
        "details": json.loads(json.dumps(det, default=str)),
        "correlation_id": correlation_id,
        "contract_version": contract_version(),
    }


class AgentError(Exception):
    """An error with a registered contract code. ``to_envelope`` renders the contract envelope."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, *, retryable: bool | None = None) -> None:
        if code not in _registry():
            raise ValueError(f"unregistered error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = retryable

    def to_envelope(self, correlation_id: str) -> dict[str, Any]:
        return error_envelope(self.code, self.message, correlation_id=correlation_id, details=self.details, retryable=self.retryable)

    # Convenience constructors for the codes the agent raises itself.
    @classmethod
    def validation(cls, message: str, pointer: str = "", **details: Any) -> AgentError:
        return cls("VALIDATION_FAILED", message, {"pointer": pointer, **details})

    @classmethod
    def unauthorized(cls, message: str = "the caller could not be authenticated") -> AgentError:
        return cls("UNAUTHORIZED", message)

    @classmethod
    def forbidden(cls, message: str, **details: Any) -> AgentError:
        return cls("FORBIDDEN", message, details)

    @classmethod
    def not_permitted(cls, message: str, **details: Any) -> AgentError:
        return cls("OPERATION_NOT_PERMITTED", message, details)

    @classmethod
    def budget(cls, message: str, **details: Any) -> AgentError:
        return cls("BUDGET_EXCEEDED", message, details)

    @classmethod
    def dependency(cls, message: str, **details: Any) -> AgentError:
        return cls("DEPENDENCY_UNAVAILABLE", message, details)

    @classmethod
    def internal(cls, message: str = "unexpected agent failure") -> AgentError:
        return cls("INTERNAL", message)
