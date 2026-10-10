"""The runtime picks up a changed provider configuration without a restart (regression: the first
gamma deploy kept calling the previous model because the runtime started before the release
published the new explanation-model-id)."""

from __future__ import annotations

import dataclasses

from finplan_agent.providers.fixture import FixtureProvider
from tests.fakes.agent import make_service


def test_provider_config_is_refreshed_after_the_ttl():
    svc, _ = make_service()
    old = svc.deps.provider_config
    new = dataclasses.replace(old, model_id="new-model") if hasattr(old, "model_id") else old
    calls = []

    def loader():
        calls.append(1)
        return new, FixtureProvider()

    svc.deps.provider_loader = loader
    svc.deps.provider_refresh_seconds = 0.0
    svc.refresh_provider()
    assert calls and svc.deps.provider_config == new


def test_a_failing_refresh_keeps_the_current_provider():
    svc, _ = make_service()
    before = (svc.deps.provider_config, svc.deps.provider)

    def loader():
        raise RuntimeError("invalid configuration")

    svc.deps.provider_loader = loader
    svc.deps.provider_refresh_seconds = 0.0
    svc.refresh_provider()
    assert (svc.deps.provider_config, svc.deps.provider) == before


def test_refresh_is_rate_limited():
    svc, _ = make_service()
    calls = []
    svc.deps.provider_loader = lambda: (calls.append(1), (svc.deps.provider_config, svc.deps.provider))[1]
    svc.deps.provider_refresh_seconds = 3600.0
    svc.refresh_provider()
    svc.refresh_provider()
    assert calls == [1]


def test_first_describe_picks_up_provider_published_after_process_start():
    from finplan_agent.providers.bedrock import BedrockProvider
    from tests.fakes.agent import StubBedrockClient, bedrock_config

    svc, _ = make_service()
    assert svc.deps.provider_config.kind == "fixture"
    current = bedrock_config("beta")
    provider = BedrockProvider(current, StubBedrockClient())
    svc.deps.provider_loader = lambda: (current, provider)
    assert svc.describe()["provider"]["kind"] == "bedrock"
    assert svc.deps.provider is provider and svc.guard.config == current
