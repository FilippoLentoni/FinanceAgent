"""AWS client construction for the agent (mirrors FinancialPlanning ``core/aws_clients.py``).

Platform lesson L4: S3 clients must sign SigV4 against the REGIONAL endpoint; a default
``boto3.client("s3")`` presigns SigV2 on the global host, which S3 rejects for KMS-encrypted objects.
The agent does not use S3 today, but every S3 client must come from :func:`s3_client` so a later one
cannot regress (a unit test refuses any other S3 client construction in ``agent/``, ``scripts/`` and
the deployed suites).

Every other client is regional too (:func:`client`), with standard retries, so nothing silently falls
back to a global endpoint or another Region.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["DEFAULT_REGION", "client", "region", "s3_client", "s3_endpoint"]

DEFAULT_REGION = "us-east-2"


def region(explicit: str | None = None) -> str:
    return explicit or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION


def client(service: str, region_name: str | None = None, *, session: Any = None, read_timeout: int = 60, max_attempts: int = 4) -> Any:
    """A regional boto3 client with standard retry mode. ``service`` must not be ``s3``."""
    if service == "s3":
        raise ValueError("use s3_client() for S3 (regional SigV4 endpoint, lesson L4)")
    import boto3
    from botocore.config import Config

    reg = region(region_name)
    session = session or boto3.session.Session(region_name=reg)
    return session.client(service, region_name=reg, config=Config(retries={"mode": "standard", "max_attempts": max_attempts}, read_timeout=read_timeout, connect_timeout=10))


def s3_endpoint(region_name: str) -> str:
    if not region_name:
        raise RuntimeError("S3 clients need the bucket's region")
    return f"https://s3.{region_name}.amazonaws.com"


def s3_client(region_name: str | None = None, *, session: Any = None) -> Any:
    """An S3 client that signs (and presigns) SigV4 on the regional virtual-hosted endpoint."""
    import boto3
    from botocore.config import Config

    reg = region(region_name)
    session = session or boto3.session.Session(region_name=reg)
    return session.client("s3", region_name=reg, endpoint_url=s3_endpoint(reg), config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
