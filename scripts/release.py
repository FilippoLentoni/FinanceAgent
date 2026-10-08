#!/usr/bin/env python3
"""Release identity, artifact digest, release ledger, reference resolution and release publishing
(tasks 5.1, 5.2, 6.4, 7.1; FA-GW-02, FA-GW-03, FA-GW-05, FA-PL-03, FA-PRV-08, FA-PRV-09; contracts D4, D6).

Build stage (once per commit): :func:`mint_release_id`, :func:`assembly_digest` (assembly + Runtime
image digest, so every environment's manifest records the same ``artifact_digest``), :class:`ReleaseInfo`
(``release-info.json`` in BuildOutput) and the release ledger in the pipeline store
(:func:`store_build_output` / :func:`fetch_build_output`, used by rollback; digest re-verified).

``Resolve`` action (each environment, BEFORE its deploys; :func:`resolve`):

1. the FinanceLambdasTool **compatibility gate** (FA-GW-03): ``/finplan/<env>/financelambdastool/release/manifest``
   must exist and serve FinanceAgent's pinned contract major, else the stage fails with a
   dependency-missing / incompatibility message and nothing is deployed (previous targets unchanged);
2. **registration from released references** (FA-GW-02): the environment's tool catalog
   (``outputs["tool-catalog"]`` of that manifest, contract ``tool-catalog``, same environment) and,
   per catalog tool, its ``lambda_ref_parameter`` (``/finplan/<env>/financelambdastool/lambda/<tool>-arn``),
   which must be an alias-qualified Lambda ARN of THIS environment's FinanceLambdasTool function in
   the caller's own account. Tools absent from the catalog resolve to ``none`` (no target);
3. the **Bedrock grant** (FA-PRV-08): while ``config/<env>.json`` ``explanation.provider`` is
   ``fixture`` the Runtime gets no Bedrock permission at all (``none``); for ``bedrock`` the configured
   model ID is looked up read-only (``bedrock:GetInferenceProfile``) and the grant covers exactly the
   profile ARN plus the regional foundation-model ARNs it routes to (US regions only; ``global.``
   refused).

The resolved values are exported as pipeline variables (never an artifact, never a file in the repo).

``PublishRelease`` (:func:`publish_release`), as the ``pipeline`` writer bound to the environment
(:func:`finplan_contracts.ssm.check_write` on every write):

* ``agent/user-pool-ref``, ``agent/authorizer-metadata-ref`` (JSON ``discovery_url``, ``issuer``,
  ``allowed_clients``, ``token_endpoint``, ``scopes``), ``secret-ref/ci-test-client`` (secret NAME),
  ``agent/runtime-ref``, ``agent/gateway-endpoint-ref``, ``agent/gateway-principal-ref``,
  ``agent/policy-digest``, ``agent/gateway-targets``, ``agent/runtime-image``;
* ``config/explanation-provider``, ``config/explanation-model-id`` (from ``config/<env>.json``) and
  ``config/explanation-guards`` (caps from config; operator-entered ``rates`` are preserved, never
  invented: no price lives in this repository);
* ``config/budget-enforced-role-names`` (Runtime role first, FA-PRV-09);
* the validated release manifest (``outputs`` names every published parameter) and
  ``release/current-release-id``; the manifest is copied into the ledger.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from finplan_contracts import ssm as contract_ssm  # noqa: E402
from finplan_contracts.validate import validate  # noqa: E402

from infra.stacks import naming as n  # noqa: E402

__all__ = [
    "DependencyMissing",
    "ManifestError",
    "ReleaseInfo",
    "Resolution",
    "approval_record",
    "assembly_digest",
    "bedrock_grant",
    "build_manifest",
    "contract_pin",
    "fetch_build_output",
    "image_uri",
    "mint_release_id",
    "planned_parameters",
    "publish_release",
    "resolve",
    "resolve_targets",
    "stack_outputs",
    "store_build_output",
]

REPO = n.REPO
FLT = "financelambdastool"
RELEASES_PREFIX = "releases/"
ADVANCED_TIER_BYTES = 4096
APPROVAL_ACTION = "ApproveProd"
NONE = "none"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_GEO_PREFIXES = ("us.", "eu.", "apac.", "ap.", "jp.", "au.", "ca.", "us-gov.", "global.")


class ManifestError(ValueError):
    pass


class DependencyMissing(RuntimeError):
    """The environment's FinanceLambdasTool release is absent or incompatible (FA-GW-03)."""


# ===================================================================== build stage
def mint_release_id(now: datetime | None = None) -> str:
    """``rel_`` + ULID (48-bit millisecond time + 80 random bits, Crockford base32)."""
    ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    value = (ms << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "rel_" + "".join(reversed(chars))


def assembly_digest(assembly: str | Path, image_digest: str | None = None) -> str:
    root = Path(assembly)
    if not (root / "manifest.json").is_file():
        raise ManifestError(f"{root} is not a cloud assembly (manifest.json missing)")
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    if image_digest:
        h.update(b"image\0" + image_digest.encode("ascii") + b"\n")
    return "sha256:" + h.hexdigest()


def contract_pin(root: str | Path = ROOT) -> tuple[str, str]:
    pin = json.loads((Path(root) / "contracts-pin.json").read_text(encoding="utf-8"))
    return str(pin["version"]), "sha256:" + str(pin["sha256"])


def image_uri(account: str, region: str, repository: str, digest: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest or ""):
        raise ManifestError("the Runtime image must be referenced by digest")
    return f"{account}.dkr.ecr.{region}.amazonaws.com/{repository}@{digest}"


@dataclass
class ReleaseInfo:
    release_id: str
    source_commit: str
    artifact_digest: str
    contract_version: str
    contract_digest: str
    served_contract_majors: list[int]
    region: str
    built_at: str
    image_repository: str | None = None
    image_digest: str | None = None
    image_uri: str | None = None
    base_images: dict[str, str] = field(default_factory=dict)
    rollback: bool = False
    synthetic: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def load(cls, path: str | Path) -> ReleaseInfo:
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


def zip_dir(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(x for x in directory.rglob("*") if x.is_file()):
            info = zipfile.ZipInfo(p.relative_to(directory).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def store_build_output(s3: Any, bucket: str, info: ReleaseInfo, out_dir: str | Path) -> str:
    key = f"{RELEASES_PREFIX}{info.release_id}/build-output.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=zip_dir(Path(out_dir)), IfNoneMatch="*")
    s3.put_object(Bucket=bucket, Key=f"{RELEASES_PREFIX}{info.release_id}/release-info.json", Body=info.to_json().encode("utf-8"), IfNoneMatch="*")
    return key


def fetch_build_output(s3: Any, bucket: str, release_id: str, out_dir: str | Path) -> ReleaseInfo:
    if not release_id.startswith("rel_"):
        raise ManifestError(f"{release_id!r} is not a release_id")
    try:
        body = s3.get_object(Bucket=bucket, Key=f"{RELEASES_PREFIX}{release_id}/build-output.zip")["Body"].read()
    except Exception as exc:
        raise ManifestError(f"release {release_id} has no stored build output in the pipeline store") from exc
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ManifestError(f"unsafe path in stored build output: {member}")
        zf.extractall(out)
    info = ReleaseInfo.load(out / "release-info.json")
    if info.release_id != release_id:
        raise ManifestError("stored build output belongs to another release")
    if assembly_digest(out / "cdk.out", info.image_digest) != info.artifact_digest:
        raise ManifestError("stored build output digest does not match its release record")
    info.rollback = True
    (out / "release-info.json").write_text(info.to_json(), encoding="utf-8")
    return info


# ===================================================================== SSM helpers
def _get(ssm: Any, name: str) -> str | None:
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
        if code == "ParameterNotFound" or type(exc).__name__ == "ParameterNotFound":
            return None
        raise


def _put(ssm: Any, env: str, name: str, value: str) -> None:
    decision = contract_ssm.check_write(name, contract_ssm.Writer(REPO, "pipeline", env))
    if not decision:
        raise ManifestError("; ".join(decision.reasons))
    problems = contract_ssm.validate_value(name, value)
    if problems:
        raise ManifestError(f"{name}: " + "; ".join(problems))
    tier = "Advanced" if len(value.encode("utf-8")) > ADVANCED_TIER_BYTES else "Standard"
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True, Tier=tier)


def _name(env: str, category: str, name: str) -> str:
    return contract_ssm.build(env, REPO, category, name)


def load_env_config(env: str, root: Path = ROOT) -> dict[str, Any]:
    shared = json.loads((root / "config" / "shared.json").read_text(encoding="utf-8"))
    return {**shared, **json.loads((root / "config" / f"{env}.json").read_text(encoding="utf-8"))}


# ===================================================================== resolve
@dataclass
class Resolution:
    env: str
    targets: dict[str, str]  # tool -> alias-qualified Lambda ARN or "none"
    bedrock_arns: list[str]  # [] while the provider is fixture
    catalog_release_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def variables(self) -> dict[str, str]:
        from infra.stacks.agent import target_variable

        out = {target_variable(t): v for t, v in sorted(self.targets.items())}
        out["BEDROCK_INVOKE_ARNS"] = ",".join(self.bedrock_arns) if self.bedrock_arns else NONE
        return out

    @property
    def registered(self) -> list[str]:
        return sorted(t for t, v in self.targets.items() if v != NONE)


def _major(version: Any) -> int | None:
    m = re.match(r"^(0|[1-9][0-9]*)\.", str(version or ""))
    return int(m.group(1)) if m else None


def resolve_targets(ssm: Any, env: str, *, account: str, tools: list[str], pinned_major: int, s3: Any = None) -> tuple[dict[str, str], str | None, list[str]]:
    """Compatibility gate + registration from the environment's released FinanceLambdasTool references."""
    notes: list[str] = []
    manifest_name = f"/finplan/{env}/{FLT}/release/manifest"
    raw = _get(ssm, manifest_name)
    if not raw:
        raise DependencyMissing(f"dependency missing: no FinanceLambdasTool release in {env} ({manifest_name} is absent); Gateway registration skipped, nothing deployed")
    manifest = json.loads(raw)
    if manifest.get("environment") != env or manifest.get("repo") != FLT:
        raise DependencyMissing(f"{manifest_name} does not describe the {env} FinanceLambdasTool release")
    majors = {int(m) for m in manifest.get("served_contract_majors") or []} or ({_major(manifest.get("contract_version"))} - {None})
    if pinned_major not in majors:
        raise DependencyMissing(f"incompatible contract major: the {env} FinanceLambdasTool release serves {sorted(majors)}, FinanceAgent pins {pinned_major}; previous Gateway targets left unchanged")
    catalog_name = (manifest.get("outputs") or {}).get("tool-catalog") or f"/finplan/{env}/{FLT}/contract/tool-catalog"
    if catalog_name != f"/finplan/{env}/{FLT}/contract/tool-catalog":
        raise DependencyMissing(f"the {env} manifest names a tool catalog outside {env}: {catalog_name}")
    raw_cat = _get(ssm, catalog_name)
    if not raw_cat:
        raise DependencyMissing(f"dependency missing: {catalog_name} is absent")
    from finplan_agent.tools.catalog import resolve_catalog_value

    def _missing(message: str, **details: Any) -> DependencyMissing:
        return DependencyMissing(f"{catalog_name}: {message}" + (f" {details}" if details else ""))

    # The deployed FinanceLambdasTool publishes a tool-catalog-pointer (the full catalog exceeds the SSM
    # size limit): follow it to its pipeline store object, digest- and release-checked.
    catalog = resolve_catalog_value(json.loads(raw_cat), env, s3=s3, account=account, error=_missing)
    res = validate(catalog, "tool-catalog")
    if not res.valid:
        raise DependencyMissing("the tool catalog does not validate against the pinned contract: " + "; ".join(i.message for i in res.issues[:3]))
    if catalog.get("environment") != env:
        raise DependencyMissing(f"the tool catalog belongs to {catalog.get('environment')!r}, not {env}")
    from infra.stacks.tool_policy import load_policy

    words = [str(w) for w in load_policy().get("denied_tool_words") or []]
    arn_re = re.compile(rf"^arn:aws:lambda:[a-z0-9-]+:{re.escape(account)}:function:finplan-{env}-{FLT}-[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
    targets = {t: NONE for t in tools}
    for entry in catalog["tools"]:
        tool = entry["name"]
        if any(w in tool for w in words):
            notes.append(f"{tool}: names a denied capability; never registered")
            continue
        if tool not in targets:
            notes.append(f"{tool}: not in the pinned contract package's tool schemas; not registered")
            continue
        ref = entry["lambda_ref_parameter"]
        if not ref.startswith(f"/finplan/{env}/{FLT}/lambda/"):
            raise DependencyMissing(f"{tool}: Lambda reference parameter {ref} is not this environment's")
        arn = _get(ssm, ref)
        if not arn:
            raise DependencyMissing(f"dependency missing: {ref} is absent")
        if not arn_re.match(arn):
            raise DependencyMissing(f"{tool}: {ref} is not an alias-qualified {env} FinanceLambdasTool Lambda of this account")
        targets[tool] = arn
    if all(v == NONE for v in targets.values()):
        raise DependencyMissing(f"the {env} tool catalog registers no tool FinanceAgent can serve")
    return targets, catalog.get("release_id"), notes


def bedrock_grant(cfg: Mapping[str, Any], bedrock: Any, *, region: str) -> list[str]:
    """Resources of the Runtime role's Bedrock grant for ``cfg['explanation']`` (FA-PRV-08)."""
    expl = dict(cfg.get("explanation") or {})
    kind = expl.get("provider", "fixture")
    if kind == "fixture":
        return []
    if kind != "bedrock":
        raise ManifestError(f"explanation provider {kind!r} is not allowed (bedrock or fixture)")
    if kind not in (cfg.get("provider_kind_allowed") or []):
        raise ManifestError(f"provider {kind!r} is not allowed in {cfg.get('environment')}")
    from finplan_agent.config.provider import model_id_problems

    model_id = str(expl.get("model_id") or "")
    problems = model_id_problems(model_id)
    if problems:
        raise ManifestError("; ".join(problems))
    if not model_id.startswith(_GEO_PREFIXES):
        return [f"arn:aws:bedrock:{region}::foundation-model/{model_id}"]
    if not model_id.startswith("us."):
        raise ManifestError(f"{model_id}: only US cross-region inference profiles are allowed (inference stays in US regions)")
    prof = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
    if prof.get("status") not in (None, "ACTIVE"):
        raise ManifestError(f"inference profile {model_id} is {prof.get('status')}")
    arns = [str(prof["inferenceProfileArn"])]
    for m in prof.get("models") or []:
        arn = str(m.get("modelArn", ""))
        region_of = arn.split(":")[3] if arn.count(":") >= 5 else ""
        if not region_of.startswith("us-"):
            raise ManifestError(f"{model_id} routes outside US regions ({arn})")
        arns.append(arn)
    if len(arns) < 2:
        raise ManifestError(f"{model_id} lists no foundation model")
    return sorted(set(arns), key=arns.index)


def resolve(env: str, *, ssm: Any, bedrock: Any, account: str, region: str, root: Path = ROOT, recorded_targets: Mapping[str, str] | None = None, s3: Any = None) -> Resolution:
    from infra.stacks.tool_schemas import contract_tools

    version, _digest = contract_pin(root)
    major = _major(version)
    assert major is not None
    tools = contract_tools()
    if recorded_targets is not None:  # rollback: re-register the target set recorded with that release
        targets = {t: str(recorded_targets.get(t, NONE)) for t in tools}
        cat_rel, notes = None, ["rollback: recorded target set re-registered"]
    else:
        targets, cat_rel, notes = resolve_targets(ssm, env, account=account, tools=tools, pinned_major=major, s3=s3)
    cfg = load_env_config(env, root)
    return Resolution(env=env, targets=targets, bedrock_arns=bedrock_grant(cfg, bedrock, region=region), catalog_release_id=cat_rel, notes=notes)


# ===================================================================== publish
def stack_outputs(cfn: Any, env: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in (n.identity_stack_name(env), n.agent_stack_name(env)):
        try:
            stacks = cfn.describe_stacks(StackName=name)["Stacks"]
        except Exception as exc:  # noqa: BLE001
            raise ManifestError(f"stack {name} is not deployed ({type(exc).__name__})") from None
        for o in stacks[0].get("Outputs") or []:
            out[str(o["OutputKey"])] = str(o["OutputValue"])
    return out


REQUIRED_OUTPUTS = ("UserPoolId", "PkceClientId", "CiTestClientId", "DiscoveryUrl", "Issuer", "TokenEndpoint", "RuntimeArn", "GatewayUrl", "PolicyDigest")


def explanation_guards(cfg: Mapping[str, Any], existing: str | None) -> str:
    """Guard caps from configuration; ``rates`` only from the existing (operator-entered) document."""
    doc = dict(cfg.get("guard_defaults") or {})
    try:
        prev = json.loads(existing) if existing else {}
    except json.JSONDecodeError:
        prev = {}
    if isinstance(prev, dict) and isinstance(prev.get("rates"), dict):
        doc["rates"] = prev["rates"]
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def planned_parameters(env: str, info: ReleaseInfo, outputs: Mapping[str, str], cfg: Mapping[str, Any], *, targets: Mapping[str, str], existing_guards: str | None) -> dict[str, tuple[str, str]]:
    """``{manifest output key: (SSM name, value)}`` of every reference this deploy publishes."""
    expl = dict(cfg.get("explanation") or {})
    kind = str(expl.get("provider", "fixture"))
    if env == "beta" and kind != "fixture":
        raise ManifestError("beta allows only the fixture provider (no Bedrock calls in CI, FA-PRV-14)")
    meta = {
        "discovery_url": outputs["DiscoveryUrl"],
        "issuer": outputs["Issuer"],
        "allowed_clients": [outputs["PkceClientId"], outputs["CiTestClientId"]],
        "public_client_id": outputs["PkceClientId"],
        "token_endpoint": outputs["TokenEndpoint"],
        "authorize_endpoint": outputs["TokenEndpoint"].replace("/oauth2/token", "/oauth2/authorize"),
        "scopes": {"user": f"{n.RESOURCE_SERVER}/invoke", "ci_test": f"{n.RESOURCE_SERVER}/ci_test"},
    }
    plan: dict[str, tuple[str, str]] = {}

    def add(key: str, category: str, name: str, value: str | None) -> None:
        if value:
            plan[key] = (_name(env, category, name), value)

    add("user-pool-ref", "agent", "user-pool-ref", outputs["UserPoolId"])
    add("authorizer-metadata-ref", "agent", "authorizer-metadata-ref", json.dumps(meta, sort_keys=True, separators=(",", ":")))
    add("ci-test-client-secret-ref", "secret-ref", "ci-test-client", outputs.get("CiTestClientSecretName") or n.secret_name(env, "ci-test-client"))
    add("runtime-ref", "agent", "runtime-ref", outputs["RuntimeArn"])
    add("gateway-endpoint-ref", "agent", "gateway-endpoint-ref", outputs["GatewayUrl"])
    add("gateway-principal-ref", "agent", "gateway-principal-ref", n.gateway_role_name(env))
    add("policy-digest", "agent", "policy-digest", outputs["PolicyDigest"])
    add("gateway-targets", "agent", "gateway-targets", json.dumps({t: v for t, v in sorted(targets.items())}, sort_keys=True, separators=(",", ":")))
    add("runtime-image", "agent", "runtime-image", info.image_uri)
    add("explanation-provider", "config", "explanation-provider", kind)
    add("explanation-model-id", "config", "explanation-model-id", str(expl.get("model_id") or ""))
    add("explanation-guards", "config", "explanation-guards", explanation_guards(cfg, existing_guards))
    add("budget-enforced-role-names", "config", "budget-enforced-role-names", ",".join(n.budget_enforced_role_names(env)))
    return plan


def _ts(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def build_manifest(info: ReleaseInfo, env: str, *, deployed_at: str, previous_release_id: str | None, outputs: Mapping[str, str], approval: Mapping[str, str] | None = None, rolled_back_from: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "repo": REPO,
        "environment": env,
        "region": info.region,
        "release_id": info.release_id,
        "source_commit": info.source_commit,
        "artifact_digest": info.artifact_digest,
        "contract_version": info.contract_version,
        "contract_digest": info.contract_digest,
        "deployed_at": deployed_at,
        "previous_release_id": previous_release_id,
        "outputs": dict(outputs),
        "served_contract_majors": sorted(set(info.served_contract_majors)),
        "synthetic": info.synthetic,
    }
    if approval:
        doc["approved_by"] = approval["approved_by"]
        doc["approved_at"] = approval["approved_at"]
    if rolled_back_from is not None:
        doc["rolled_back_from"] = rolled_back_from
    res = validate(doc, "release-manifest")
    problems = [f"{i.pointer or '/'}: {i.message}" for i in res.issues]
    problems += contract_ssm.validate_value(_name(env, "release", "manifest"), json.dumps(doc))
    if problems:
        raise ManifestError("release manifest is invalid: " + "; ".join(dict.fromkeys(problems)))
    return doc


def approval_record(codepipeline: Any, pipeline_name: str, execution_id: str, action_name: str = APPROVAL_ACTION) -> dict[str, str]:
    token: str | None = None
    while True:
        kw: dict[str, Any] = {"pipelineName": pipeline_name, "filter": {"pipelineExecutionId": execution_id}}
        if token:
            kw["nextToken"] = token
        resp = codepipeline.list_action_executions(**kw)
        for d in resp.get("actionExecutionDetails") or []:
            if d.get("actionName") == action_name and d.get("status") == "Succeeded":
                who = d.get("updatedBy") or ((d.get("output") or {}).get("executionResult") or {}).get("externalExecutionSummary")
                when = d.get("lastUpdateTime")
                if who and when:
                    return {"approved_by": str(who), "approved_at": _ts(when)}
        token = resp.get("nextToken")
        if not token:
            break
    raise ManifestError(f"no succeeded manual approval '{action_name}' in pipeline execution {execution_id}; prod manifests require approved_by and approved_at")


def publish_release(
    info: ReleaseInfo, env: str, *, ssm: Any, cfn: Any, targets: Mapping[str, str], s3: Any | None = None, store_bucket: str | None = None, now: datetime | None = None, approval: Mapping[str, str] | None = None, root: Path = ROOT
) -> dict[str, Any]:
    if env == "prod" and not approval:
        raise ManifestError("prod manifests require the approval record (approved_by, approved_at)")
    outputs = stack_outputs(cfn, env)
    missing = [k for k in REQUIRED_OUTPUTS if not outputs.get(k)]
    if missing:
        raise ManifestError(f"the {env} deploy did not produce the outputs {missing}")
    cfg = load_env_config(env, root)
    plan = planned_parameters(env, info, outputs, cfg, targets=targets, existing_guards=_get(ssm, _name(env, "config", "explanation-guards")))
    for _key, (name, value) in sorted(plan.items()):
        _put(ssm, env, name, value)
    pointer = _name(env, "release", "current-release-id")
    manifest_name = _name(env, "release", "manifest")
    current = _get(ssm, pointer)
    previous: str | None = current
    rolled_back_from: str | None = None
    if current == info.release_id:
        existing = json.loads(_get(ssm, manifest_name) or "{}")
        previous = existing.get("previous_release_id")
        rolled_back_from = existing.get("rolled_back_from")
    elif info.rollback:
        rolled_back_from = current
    manifest = build_manifest(info, env, deployed_at=_ts(now or datetime.now(UTC)), previous_release_id=previous, approval=approval, rolled_back_from=rolled_back_from, outputs={k: name for k, (name, _v) in plan.items()})
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    _put(ssm, env, manifest_name, body)
    _put(ssm, env, pointer, info.release_id)
    if s3 is not None and store_bucket:
        s3.put_object(Bucket=store_bucket, Key=f"{RELEASES_PREFIX}{info.release_id}/manifests/{env}.json", Body=body.encode("utf-8"), ContentType="application/json")
        s3.put_object(Bucket=store_bucket, Key=f"{RELEASES_PREFIX}{info.release_id}/targets/{env}.json", Body=json.dumps(dict(targets), sort_keys=True).encode("utf-8"), ContentType="application/json")
    return manifest


def recorded_targets(s3: Any, bucket: str, release_id: str, env: str) -> dict[str, str] | None:
    """The target set a release registered in ``env`` (rollback re-registers it), or None."""
    try:
        body = s3.get_object(Bucket=bucket, Key=f"{RELEASES_PREFIX}{release_id}/targets/{env}.json")["Body"].read()
    except Exception:  # noqa: BLE001 - not recorded (first deploy of that release in env)
        return None
    return json.loads(body)


def write_variables(path: Path, variables: Mapping[str, str]) -> None:
    """``KEY=value`` lines sourced by the buildspec into exported CodeBuild variables."""
    lines = []
    for k, v in sorted(variables.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not re.fullmatch(r"[A-Za-z0-9_:/@.,=+-]*", v):
            raise ManifestError(f"refusing to export an unsafe variable {k}")
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
