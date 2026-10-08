# FinanceAgent

The FinanceAgent explanation agent: a code-based **LangGraph** graph hosted in **Amazon Bedrock
AgentCore Runtime** (one Runtime per environment), reaching the FinanceLambdasTool tools only through
the environment's **AgentCore Gateway** over MCP, with Amazon Bedrock (configured inference profile) or
a deterministic fixture as the explanation provider. OpenSpec changes: `add-agent-runtime-and-gateway`
(phase 1) and `add-explanation-workflows` (phase 2).

## Layout

| Path | Content |
|---|---|
| `agent/finplan_agent/` | the agent package: `graph/` (state, nodes, refusals, claim check), `providers/` (interface, fixture, Bedrock Converse), `budget/` (guards, usage metrics), `session/` (caller identity, AgentCore Memory checkpointer, owner registry), `tools/` (Gateway MCP client, tool catalog), `runtime/` (AgentCore Runtime entry point and service), `config/` |
| `config/` | per-environment settings and SSM parameter names (no IDs, ARNs, prices or secrets) |
| `container/` | the ARM64 Runtime image (`Dockerfile`, build-context allow-list) |
| `scripts/` | `check_contracts_pin.py` (pin check; `--repin` re-pins in one command), `agent_gates.py` (architecture, model-ID scan, provider kind, WebSocket gate), `runtime_image.py` (image build + L3 import check; `--local-check`) |
| `tests/` | `unit/`, `graph/`, `contract/` (offline, hermetic harness: no network, no AWS, no Bedrock); `integration_beta/`, `gamma/`, `smoke/` (deployed, real stage-role credentials, real payloads) |
| `infra/` | CDK app: per environment `finplan-<env>-financeagent-identity` (Cognito pool, clients, ci_test secret) and `-agent` (Memory, policy engine, Gateway + targets + Cedar policies, Runtime + role + log group); account-level store and tooling (image repository, Gateway service roles, CodePipeline V2) |
| `policy/` | `tool-policy.yaml`: the single Gateway tool policy (roles x tools, denied arguments) rendered to Cedar |
| `skills/`, `cli/` | skill bundles, optional CLI |
| `docs/` | `agentcore-verification.md`, `agent-api.md`, `explanation-provider.md`, `bootstrap.md`, `gateway.md` |

## Commands

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync                                           # locked dependencies + vendored contract wheel
uv run pytest                                     # offline suites (deployed suites skip without FINPLAN_TARGET_ENV)
uv run python scripts/check_contracts_pin.py      # contract pin (version, digest, pyproject, uv.lock)
uv run python scripts/check_contracts_pin.py --repin   # re-pin to the newest FinancialPlanning wheel, then `uv sync`
uv run python scripts/agent_gates.py              # architecture / model-ID / provider-kind / WebSocket gates
uv run python scripts/runtime_image.py --local-check   # runtime dependency closure (no dev deps)
uvx ruff check .
uv run python scripts/synth.py --out cdk.out          # offline synth (all environments, tooling, pipeline)
uv run python scripts/infra_gates.py --assembly cdk.out   # post-synth gates (ownership, boundaries, lessons L1-L6, ...)
```

Deployment happens only through the pipeline (`docs/bootstrap.md`): Build (ARM64 image by digest) ->
Beta -> Gamma -> approval -> Prod, each environment stage `Resolve` -> `DeployIdentity` -> `DeployAgent`
-> `PublishRelease` -> deployed suite with REAL payloads to the Runtime and Gateway.

The agent never runs against Bedrock from a workstation or CI: the offline harness blocks every
Bedrock Runtime call, and beta allows only the fixture provider.
