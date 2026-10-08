"""Explanation-provider configuration: schema, validation and SSM loader (design D4; task 3.1).

Sources (per environment, all owned by FinanceAgent and written by its pipeline/bootstrap):

* ``/finplan/<env>/financeagent/config/explanation-provider``: the provider kind, exactly ``bedrock``
  or ``fixture`` (the registered contract value shape is a plain provider key);
* ``/finplan/<env>/financeagent/config/explanation-model-id``: the Bedrock model or inference-profile
  ID (only read when the kind is ``bedrock``; never in code, tests outside configuration fixtures or
  skills);
* ``/finplan/<env>/financeagent/config/explanation-guards``: JSON with the caps and rates::

      {"max_tokens_invocation": 1024, "max_tokens_turn": 4096, "max_tokens_session": 32768,
       "max_tool_calls_turn": 6, "max_cost_session_usd": 0.5, "temperature": null,
       "prompt_caching": "enabled" | "disabled",
       "rates": {"input_per_1k_usd": ..., "output_per_1k_usd": ...,
                 "cache_write_per_1k_usd": ..., "cache_read_per_1k_usd": ...,
                 "source": "configured" | "aws_price_list", "retrieved_at": "YYYY-MM-DD"}}

  Fields absent from it take ``config/<env>.json`` ``guard_defaults``. Rates are never defaulted.

Validation (any failure raises :class:`ProviderConfigError`, so the process refuses to start and the
Runtime health check fails):

* kind not in {bedrock, fixture} (``openai`` included: the OpenAI adapter does not exist);
* kind not allowed in the environment (beta is ``fixture`` only: no Bedrock calls in CI);
* model ID missing for ``bedrock``, an ARN, a ``global.`` inference profile, any ``qwen`` identifier
  or any FinanceModel / SageMaker reference (Qwen is a FinanceModel strategy benchmark only);
* ``max_tokens_invocation`` above ``max_tokens_turn`` (or the turn cap above the session cap);
* ``bedrock`` without input/output rates, a rate ``source`` or ``retrieved_at``.

``prompt_caching: enabled`` without cache rates keeps caching DISABLED (``caching_effective``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..core.errors import AgentError
from .settings import ENVIRONMENTS, SsmNames, load_repo_config

__all__ = [
    "PROVIDER_KINDS",
    "ProviderConfig",
    "ProviderConfigError",
    "Rates",
    "model_id_problems",
    "build_provider_config",
    "load_provider_config",
]

PROVIDER_KINDS = ("bedrock", "fixture")
RATE_SOURCES = ("configured", "aws_price_list")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
#: Denied explanation-model patterns (spec explanation-provider "Qwen is not an explanation provider",
#: "Claude Opus 5 through the US inference profile"): checked case-insensitively.
DENIED_MODEL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^global\.", re.I), "global. inference profiles are not allowed (inference stays in US regions)"),
    (re.compile(r"qwen", re.I), "Qwen models are FinanceModel strategy benchmark providers, never the explanation model"),
    (re.compile(r"financemodel|sagemaker|endpoint/|inference-component", re.I), "FinanceModel-hosted or SageMaker models are not explanation providers"),
    (re.compile(r"^arn:", re.I), "the model ID is a Bedrock model or inference-profile ID, never an ARN"),
)


class ProviderConfigError(AgentError):
    def __init__(self, message: str, pointer: str = "") -> None:
        super().__init__("VALIDATION_FAILED", message, {"pointer": pointer})


@dataclass(frozen=True)
class Rates:
    input_per_1k_usd: float
    output_per_1k_usd: float
    source: str
    retrieved_at: str
    cache_write_per_1k_usd: float | None = None
    cache_read_per_1k_usd: float | None = None

    @property
    def has_cache_rates(self) -> bool:
        return self.cache_write_per_1k_usd is not None and self.cache_read_per_1k_usd is not None

    def cost(self, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
        usd = input_tokens / 1000 * self.input_per_1k_usd + output_tokens / 1000 * self.output_per_1k_usd
        # Cache tokens without cache rates are priced at the (higher or equal) input rate: conservative.
        usd += cache_read_tokens / 1000 * (self.cache_read_per_1k_usd if self.cache_read_per_1k_usd is not None else self.input_per_1k_usd)
        usd += cache_write_tokens / 1000 * (self.cache_write_per_1k_usd if self.cache_write_per_1k_usd is not None else self.input_per_1k_usd)
        return usd

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class ProviderConfig:
    environment: str
    kind: str
    model_id: str | None
    max_tokens_invocation: int
    max_tokens_turn: int
    max_tokens_session: int
    max_tool_calls_turn: int
    max_cost_session_usd: float
    prompt_caching: str = "disabled"
    temperature: float | None = None
    rates: Rates | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def caching_effective(self) -> bool:
        return self.kind == "bedrock" and self.prompt_caching == "enabled" and self.rates is not None and self.rates.has_cache_rates

    def describe(self) -> dict[str, Any]:
        """Non-secret summary reported by ``describe`` (no prices beyond the rate source and date)."""
        out: dict[str, Any] = {"kind": self.kind, "max_tokens_invocation": self.max_tokens_invocation, "prompt_caching": "enabled" if self.caching_effective else "disabled"}
        if self.kind == "bedrock":
            out["model_id"] = self.model_id
            if self.rates is not None:
                out["rates_source"] = self.rates.source
                out["rates_retrieved_at"] = self.rates.retrieved_at
        return out


def model_id_problems(model_id: Any) -> list[str]:
    if not isinstance(model_id, str) or not model_id.strip():
        return ["the explanation model ID is missing"]
    problems = [msg for pat, msg in DENIED_MODEL_PATTERNS if pat.search(model_id)]
    if not _MODEL_ID_RE.match(model_id):
        problems.append("the model ID is not a Bedrock model or inference-profile ID")
    return problems


def _int(doc: Mapping[str, Any], key: str) -> int:
    v = doc.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ProviderConfigError(f"{key} must be a positive integer", f"/{key}")
    return v


def _num(doc: Mapping[str, Any], key: str, pointer: str, *, allow_none: bool = False) -> float | None:
    v = doc.get(key)
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise ProviderConfigError(f"{key} must be a non-negative number", pointer)
    return float(v)


def _date(value: Any) -> str:
    if not isinstance(value, str):
        raise ProviderConfigError("rates.retrieved_at is required (ISO date or timestamp)", "/rates/retrieved_at")
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderConfigError("rates.retrieved_at must be an ISO date or timestamp", "/rates/retrieved_at") from exc
    return value


def _rates(doc: Any) -> Rates:
    if not isinstance(doc, Mapping):
        raise ProviderConfigError("a bedrock provider needs per-token rates (rates.input_per_1k_usd, rates.output_per_1k_usd)", "/rates")
    for key in ("input_per_1k_usd", "output_per_1k_usd"):
        if doc.get(key) is None:
            raise ProviderConfigError(f"rates.{key} is required for the bedrock provider", f"/rates/{key}")
    source = doc.get("source")
    if source not in RATE_SOURCES:
        raise ProviderConfigError("rates.source must be 'configured' or 'aws_price_list'", "/rates/source")
    return Rates(
        input_per_1k_usd=_num(doc, "input_per_1k_usd", "/rates/input_per_1k_usd"),  # type: ignore[arg-type]
        output_per_1k_usd=_num(doc, "output_per_1k_usd", "/rates/output_per_1k_usd"),  # type: ignore[arg-type]
        cache_write_per_1k_usd=_num(doc, "cache_write_per_1k_usd", "/rates/cache_write_per_1k_usd", allow_none=True),
        cache_read_per_1k_usd=_num(doc, "cache_read_per_1k_usd", "/rates/cache_read_per_1k_usd", allow_none=True),
        source=source,
        retrieved_at=_date(doc.get("retrieved_at")),
    )


def build_provider_config(environment: str, kind: Any, model_id: Any, guards: Mapping[str, Any] | None, *, guard_defaults: Mapping[str, Any] | None = None, allowed_kinds: tuple[str, ...] | list[str] | None = None) -> ProviderConfig:
    """Validate the three configuration values and return the effective :class:`ProviderConfig`."""
    if environment not in ENVIRONMENTS:
        raise ProviderConfigError(f"unknown environment {environment!r}", "/environment")
    if kind not in PROVIDER_KINDS:
        raise ProviderConfigError(f"explanation provider kind {kind!r} is not allowed; allowed kinds are exactly 'bedrock' and 'fixture'", "/kind")
    if allowed_kinds is not None and kind not in allowed_kinds:
        raise ProviderConfigError(f"provider kind {kind!r} is not allowed in {environment} (allowed: {', '.join(allowed_kinds)})", "/kind")
    doc: dict[str, Any] = {**(guard_defaults or {}), **(guards or {})}
    unknown = sorted(set(doc) - {"max_tokens_invocation", "max_tokens_turn", "max_tokens_session", "max_tool_calls_turn", "max_cost_session_usd", "temperature", "prompt_caching", "rates", "$comment"})
    if unknown:
        raise ProviderConfigError(f"unknown explanation-guards field(s): {', '.join(unknown)}", f"/{unknown[0]}")
    inv, turn, sess = _int(doc, "max_tokens_invocation"), _int(doc, "max_tokens_turn"), _int(doc, "max_tokens_session")
    if inv > turn:
        raise ProviderConfigError("max_tokens_invocation must not exceed max_tokens_turn", "/max_tokens_invocation")
    if turn > sess:
        raise ProviderConfigError("max_tokens_turn must not exceed max_tokens_session", "/max_tokens_turn")
    tool_calls = _int(doc, "max_tool_calls_turn")
    max_cost = _num(doc, "max_cost_session_usd", "/max_cost_session_usd")
    caching = doc.get("prompt_caching", "disabled")
    if caching not in ("enabled", "disabled"):
        raise ProviderConfigError("prompt_caching must be 'enabled' or 'disabled'", "/prompt_caching")
    temperature = doc.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 1):
        raise ProviderConfigError("temperature must be null or between 0 and 1", "/temperature")
    rates: Rates | None = None
    mid: str | None = None
    if kind == "bedrock":
        problems = model_id_problems(model_id)
        if problems:
            raise ProviderConfigError("; ".join(problems), "/model_id")
        mid = model_id
        rates = _rates(doc.get("rates"))
    elif doc.get("rates") is not None:
        rates = _rates(doc["rates"])
    return ProviderConfig(
        environment=environment,
        kind=kind,
        model_id=mid,
        max_tokens_invocation=inv,
        max_tokens_turn=turn,
        max_tokens_session=sess,
        max_tool_calls_turn=tool_calls,
        max_cost_session_usd=float(max_cost or 0.0),
        prompt_caching=caching,
        temperature=float(temperature) if temperature is not None else None,
        rates=rates,
    )


def _get_parameters(ssm: Any, names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    resp = ssm.get_parameters(Names=names)
    for p in resp.get("Parameters", []):
        out[p["Name"]] = p["Value"]
    return out


def load_provider_config(environment: str, ssm: Any) -> ProviderConfig:
    """Read the provider kind, model ID and guards of ``environment`` from SSM and validate them."""
    names = SsmNames(environment)
    repo = load_repo_config(environment)
    values = _get_parameters(ssm, [names.explanation_provider, names.explanation_model_id, names.explanation_guards])
    kind = values.get(names.explanation_provider)
    if kind is None:
        raise ProviderConfigError(f"{names.explanation_provider} is not set", "/kind")
    kind = kind.strip()
    if kind.startswith("{"):
        raise ProviderConfigError("explanation-provider holds the provider key only ('bedrock' or 'fixture'); caps and rates belong in explanation-guards", "/kind")
    guards_raw = values.get(names.explanation_guards)
    try:
        guards = json.loads(guards_raw) if guards_raw else {}
    except json.JSONDecodeError as exc:
        raise ProviderConfigError("explanation-guards is not valid JSON", "/") from exc
    if not isinstance(guards, dict):
        raise ProviderConfigError("explanation-guards must be a JSON object", "/")
    model_id = values.get(names.explanation_model_id) if kind == "bedrock" else None
    return build_provider_config(environment, kind, model_id, guards, guard_defaults=repo.get("guard_defaults"), allowed_kinds=tuple(repo.get("provider_kind_allowed", PROVIDER_KINDS)))
