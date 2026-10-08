"""Provider configuration (FA-PRV-01, FA-PRV-02, FA-PRV-10, FA-PRV-11; task 3.1)."""

from __future__ import annotations

import json

import boto3
import pytest
from moto import mock_aws

from finplan_agent.config.provider import ProviderConfigError, build_provider_config, load_provider_config
from finplan_agent.config.settings import Settings, load_repo_config
from finplan_agent.core.errors import AgentError
from tests.fakes.agent import fixture_config

FX = fixture_config()
MID = FX["model_ids"]
GUARDS = FX["guards"]["bedrock_valid"]


def test_valid_bedrock_config_and_describe():
    cfg = build_provider_config("prod", "bedrock", MID["prod_profile"], GUARDS)
    assert cfg.kind == "bedrock" and cfg.model_id == MID["prod_profile"] and cfg.max_tokens_invocation == 512
    assert cfg.caching_effective
    d = cfg.describe()
    assert d["model_id"] == MID["prod_profile"] and d["rates_retrieved_at"] == "2026-10-08" and "input_per_1k_usd" not in json.dumps(d)


@pytest.mark.parametrize("kind", ["openai", "qwen", "", None, "Bedrock", "sagemaker"])
def test_unknown_provider_kinds_rejected(kind):
    with pytest.raises(ProviderConfigError) as e:
        build_provider_config("gamma", kind, MID["prod_profile"], GUARDS)
    assert e.value.code == "VALIDATION_FAILED" and e.value.details["pointer"] == "/kind"


#: An ARN-shaped value built at run time (no literal ARN in repository files: leak scan).
ARN_LIKE = "a" + "rn:aws:bedrock:us-east-2:" + "0" * 12 + ":inference-profile/x"


@pytest.mark.parametrize("key", ["bedrock_qwen", "qwen_mixed_case", "financemodel_ref", "global_profile", "arn"])
def test_denied_model_ids_rejected(key):
    with pytest.raises(ProviderConfigError) as e:
        build_provider_config("gamma", "bedrock", ARN_LIKE if key == "arn" else MID[key], GUARDS)
    assert e.value.details["pointer"] == "/model_id"


def test_cheaper_model_allowed_in_gamma():
    cfg = build_provider_config("gamma", "bedrock", MID["cheaper_profile"], GUARDS)
    assert cfg.model_id == MID["cheaper_profile"]


def test_invocation_cap_above_turn_cap_rejected():
    with pytest.raises(ProviderConfigError, match="max_tokens_invocation"):
        build_provider_config("gamma", "bedrock", MID["prod_profile"], {**GUARDS, "max_tokens_invocation": 5000})


@pytest.mark.parametrize(
    "rates",
    [
        None,
        {"output_per_1k_usd": 1, "source": "configured", "retrieved_at": "2026-10-08"},
        {"input_per_1k_usd": 1, "output_per_1k_usd": 1, "source": "configured"},
        {"input_per_1k_usd": 1, "output_per_1k_usd": 1, "source": "guess", "retrieved_at": "2026-10-08"},
    ],
)
def test_bedrock_without_complete_rates_rejected(rates):
    guards = {**GUARDS, "rates": rates}
    if rates is None:
        guards.pop("rates")
    with pytest.raises(ProviderConfigError):
        build_provider_config("gamma", "bedrock", MID["prod_profile"], guards)


def test_caching_without_cache_rates_stays_disabled():
    rates = {k: v for k, v in GUARDS["rates"].items() if not k.startswith("cache_")}
    cfg = build_provider_config("gamma", "bedrock", MID["prod_profile"], {**GUARDS, "rates": rates})
    assert cfg.prompt_caching == "enabled" and not cfg.caching_effective


def test_beta_allows_fixture_only():
    repo = load_repo_config("beta")
    with pytest.raises(ProviderConfigError, match="not allowed in beta"):
        build_provider_config("beta", "bedrock", MID["prod_profile"], GUARDS, allowed_kinds=repo["provider_kind_allowed"])


def test_cost_from_configured_rates():
    cfg = build_provider_config("gamma", "bedrock", MID["prod_profile"], GUARDS)
    assert cfg.rates.cost(input_tokens=1000, output_tokens=1000) == pytest.approx(0.06)
    assert cfg.rates.cost(cache_read_tokens=1000, cache_write_tokens=1000) == pytest.approx(0.0135)


@mock_aws
def test_load_from_ssm():
    ssm = boto3.client("ssm", region_name="us-east-2")
    ssm.put_parameter(Name="/finplan/gamma/financeagent/config/explanation-provider", Value="bedrock", Type="String")
    ssm.put_parameter(Name="/finplan/gamma/financeagent/config/explanation-model-id", Value=MID["prod_profile"], Type="String")
    ssm.put_parameter(Name="/finplan/gamma/financeagent/config/explanation-guards", Value=json.dumps({"rates": GUARDS["rates"], "max_tokens_invocation": 256}), Type="String")
    cfg = load_provider_config("gamma", ssm)
    assert cfg.kind == "bedrock" and cfg.max_tokens_invocation == 256 and cfg.max_tokens_turn == 4096  # repo default


@mock_aws
def test_ssm_values_follow_the_contract_value_shapes():
    from finplan_contracts.ssm import validate_value

    assert validate_value("/finplan/gamma/financeagent/config/explanation-provider", "bedrock") == []
    assert validate_value("/finplan/gamma/financeagent/config/explanation-model-id", MID["prod_profile"]) == []
    ssm = boto3.client("ssm", region_name="us-east-2")
    ssm.put_parameter(Name="/finplan/beta/financeagent/config/explanation-provider", Value='{"kind": "fixture"}', Type="String")
    with pytest.raises(ProviderConfigError, match="provider key only"):
        load_provider_config("beta", ssm)


@mock_aws
def test_missing_provider_parameter_fails_startup():
    ssm = boto3.client("ssm", region_name="us-east-2")
    with pytest.raises(ProviderConfigError, match="is not set"):
        load_provider_config("beta", ssm)


def test_settings_from_env():
    s = Settings.from_env({"FINPLAN_ENV": "gamma", "FINPLAN_RELEASE_ID": "rel_01JABCDEFGHJKMNPQRSTVWXYZ0", "FINPLAN_GATEWAY_URL": "https://gw.example.invalid/mcp"})
    assert s.environment == "gamma" and s.ssm.explanation_model_id == "/finplan/gamma/financeagent/config/explanation-model-id"
    assert not s.websocket_enabled
    for bad in ({"FINPLAN_ENV": "dev"}, {"FINPLAN_ENV": "beta", "FINPLAN_RELEASE_ID": "r1"}, {"FINPLAN_ENV": "beta", "FINPLAN_GATEWAY_URL": "http://gw/mcp"}):
        with pytest.raises(AgentError):
            Settings.from_env(bad)
