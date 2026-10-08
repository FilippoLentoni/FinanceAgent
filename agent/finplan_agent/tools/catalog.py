"""The tool catalog view the graph uses (design D1: the catalog's ``state_changing`` flag drives the
confirm node).

Source: the environment's FinanceLambdasTool catalog at
``/finplan/<env>/financelambdastool/contract/tool-catalog`` (contract ``core/v1/tool-catalog.json``),
read-only. It is validated against the pinned contract schema and must name the same environment.
The full catalog exceeds the SSM size limit, so the deployed FinanceLambdasTool publishes a
``tool-catalog-pointer`` there instead (``s3_uri`` in its pipeline store, ``sha256``, ``release_id``,
sorted tool ``names``); :func:`resolve_catalog_value` follows the pointer, verifies the digest and that
the object is this environment's catalog of that release, then validates it like an inline catalog.
A tool offered by the Gateway but absent from the catalog is treated as STATE-CHANGING (fail safe: it
always needs confirmation). Tools naming live execution, trading, orders, payments or wallets are
never offered to the model, whatever any catalog says (spec "No live financial actions").
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..core.errors import AgentError
from ..providers.base import ToolSpec

__all__ = ["CATALOG_POINTER_KIND", "DENIED_TOOL_WORDS", "CatalogEntry", "ToolCatalog", "catalog_object_key_pattern", "load_tool_catalog", "resolve_catalog_value"]

DENIED_TOOL_WORDS = ("execute", "execution", "trade", "trading", "order", "payment", "wallet", "live", "broker", "coinbase", "transfer")
_DENIED_RE = re.compile("|".join(DENIED_TOOL_WORDS), re.I)
#: Exact read-tool names exempt from the denied words when the catalog marks them read-only.
READ_ONLY_EXCEPTIONS = frozenset({"get_execution", "list_executions"})
CATALOG_POINTER_KIND = "tool-catalog-pointer"
#: The FinanceLambdasTool pipeline store (account-level, environment ``shared``) holding the catalog objects.
CATALOG_BUCKET_PREFIX = "finplan-shared-financelambdastool-pipeline-store-"


def catalog_object_key_pattern(environment: str) -> re.Pattern[str]:
    """``s3://<FinanceLambdasTool store>/releases/<release_id>/tool-catalog/<env>.json`` (group 1 = account, 2 = release)."""
    return re.compile(rf"^s3://{re.escape(CATALOG_BUCKET_PREFIX)}([0-9]{{12}})/releases/(rel_[0-9A-Z]{{26}})/tool-catalog/{re.escape(environment)}\.json$")


def resolve_catalog_value(doc: Mapping[str, Any], environment: str, *, s3: Any, account: str | None = None, error: Any = None) -> dict[str, Any]:
    """The full catalog document for ``doc`` (inline catalog, or a ``tool-catalog-pointer`` followed to S3).

    ``error(message, **details)`` builds the exception raised on any mismatch (default
    :meth:`AgentError.dependency`). ``s3`` must come from :func:`finplan_agent.core.aws_clients.s3_client`
    (regional SigV4, lesson L4). Schema validation of the returned document is the caller's.
    """
    fail = error or AgentError.dependency
    if doc.get("kind") != CATALOG_POINTER_KIND:
        return dict(doc)
    if doc.get("environment") != environment:
        raise fail("the tool catalog pointer belongs to another environment", catalog_environment=doc.get("environment"))
    m = catalog_object_key_pattern(environment).match(str(doc.get("s3_uri") or ""))
    if not m:
        raise fail("the tool catalog pointer does not name this environment's catalog object in the FinanceLambdasTool store", environment=environment)
    if account is not None and m.group(1) != account:
        raise fail("the tool catalog pointer names a store in another account")
    if m.group(2) != doc.get("release_id"):
        raise fail("the tool catalog pointer's object belongs to another release", release_id=doc.get("release_id"))
    if s3 is None:
        raise fail("the tool catalog is published as a pointer and no S3 client is available")
    bucket, _, key = str(doc["s3_uri"]).removeprefix("s3://").partition("/")
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # AccessDenied, NoSuchKey, ...: no usable catalog here
        code = exc.response.get("Error", {}).get("Code", type(exc).__name__) if hasattr(exc, "response") else type(exc).__name__
        raise fail("the tool catalog object cannot be read", reason=code) from None
    want = str(doc.get("sha256") or "").removeprefix("sha256:")
    if hashlib.sha256(body).hexdigest() != want:
        raise fail("the tool catalog object does not match the pointer's sha256")
    try:
        full = json.loads(body)
    except json.JSONDecodeError:
        raise fail("the tool catalog object is not JSON") from None
    if not isinstance(full, dict) or full.get("environment") != environment or full.get("release_id") != doc.get("release_id"):
        raise fail("the tool catalog object is not this environment's catalog of the pointed release")
    names = sorted(str(t.get("name")) for t in full.get("tools") or [] if isinstance(t, dict))
    if names != sorted(str(t) for t in doc.get("tools") or []):
        raise fail("the tool catalog object lists other tools than its pointer")
    return full


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    state_changing: bool
    role_class: str
    description: str = ""


class ToolCatalog:
    def __init__(self, entries: Iterable[CatalogEntry], *, environment: str | None = None, release_id: str | None = None) -> None:
        self.entries = {e.name: e for e in entries}
        self.environment = environment
        self.release_id = release_id

    @classmethod
    def from_document(cls, doc: Mapping[str, Any], *, environment: str) -> ToolCatalog:
        from finplan_contracts.validate import validate

        res = validate(dict(doc), "tool-catalog")
        if not res.valid:
            raise AgentError.dependency("the tool catalog does not validate against the pinned contract", problems=[i.message for i in res.issues][:5])
        if doc.get("environment") != environment:
            raise AgentError.dependency("the tool catalog belongs to another environment", catalog_environment=doc.get("environment"))
        entries = [CatalogEntry(t["name"], bool(t["state_changing"]), t["role_class"], t.get("description", "")) for t in doc["tools"]]
        return cls(entries, environment=environment, release_id=doc.get("release_id"))

    def is_denied(self, name: str) -> bool:
        if name in READ_ONLY_EXCEPTIONS:
            # Reads of paper/simulated execution RECORDS (explanation workflow 1, EX-OQ-9) are allowed
            # only when the catalog lists them as non-state-changing; anything else stays denied.
            entry = self.entries.get(name)
            return entry is None or entry.state_changing
        return bool(_DENIED_RE.search(name))

    def state_changing(self, name: str) -> bool:
        entry = self.entries.get(name)
        return True if entry is None else entry.state_changing

    def annotate(self, specs: Iterable[ToolSpec]) -> list[ToolSpec]:
        """Gateway tool specs with the catalog's state-changing flag; denied tools removed."""
        out = []
        for s in specs:
            if self.is_denied(s.name):
                continue
            out.append(ToolSpec(name=s.name, description=s.description, input_schema=s.input_schema, state_changing=self.state_changing(s.name)))
        return out


def load_tool_catalog(ssm: Any, environment: str, *, s3: Any = None) -> ToolCatalog:
    name = f"/finplan/{environment}/financelambdastool/contract/tool-catalog"
    try:
        value = ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except Exception as exc:  # ParameterNotFound and friends: no compatible tool release here
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__) if hasattr(exc, "response") else type(exc).__name__
        raise AgentError.dependency("no FinanceLambdasTool tool catalog is published in this environment", parameter=name, reason=code) from None
    try:
        doc = json.loads(value)
    except json.JSONDecodeError:
        raise AgentError.dependency("the tool catalog parameter is not JSON", parameter=name) from None
    if not isinstance(doc, dict):
        raise AgentError.dependency("the tool catalog parameter is not a JSON object", parameter=name)
    return ToolCatalog.from_document(resolve_catalog_value(doc, environment, s3=s3), environment=environment)
