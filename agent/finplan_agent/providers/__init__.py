"""Explanation providers: the interface, the fixture adapter and the Amazon Bedrock adapter."""

from __future__ import annotations

from typing import Any

from ..config.provider import ProviderConfig
from .base import ExplanationProvider, GenerateRequest, GenerateResult, ToolCall, ToolSpec, Usage
from .fixture import FixtureProvider

__all__ = ["ExplanationProvider", "FixtureProvider", "GenerateRequest", "GenerateResult", "ToolCall", "ToolSpec", "Usage", "make_provider"]


def make_provider(config: ProviderConfig, *, bedrock_client: Any = None) -> ExplanationProvider:
    """The provider for a validated configuration. ``bedrock_client`` defaults to a regional
    ``bedrock-runtime`` client on the default credential chain (the Runtime role; no secret)."""
    if config.kind == "fixture":
        return FixtureProvider()
    if config.kind == "bedrock":
        from .bedrock import BedrockProvider

        if bedrock_client is None:
            from ..core.aws_clients import client

            bedrock_client = client("bedrock-runtime", read_timeout=300, max_attempts=2)
        return BedrockProvider(config, bedrock_client)
    raise ValueError(f"no adapter for provider kind {config.kind!r}")  # unreachable: config validation
