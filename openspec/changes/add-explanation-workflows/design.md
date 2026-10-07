# Design

## Context

See proposal.md (Why). The requirements are in `specs/`: explanation-evidence, plan-performance-explanation, recommendation-change-explanation and sensitivity-explanation. The change builds on `add-agent-runtime-and-gateway`:
- the LangGraph graph, the Gateway tool path and its policy;
- the confirmation interrupt;
- the pluggable provider interface (Amazon Bedrock by default with IAM auth and the model ID from `/finplan/<env>/financeagent/config/explanation-model-id`, or fixture; Qwen excluded; OpenAI only as an optional future adapter). The configured model is Claude Opus 5 via the US inference profile `us.anthropic.claude-opus-5` (contracts OQ-13 resolved 2026-10-07; beta/gamma may set a cheaper ID), with per-invocation max-token caps, the per-session budget check before every invocation against the `bedrock_explanations` allocation, prompt caching where supported, and no Bedrock calls in CI;
- the deterministic claim check;
- the identity provider: a FinanceAgent-owned Amazon Cognito user pool per environment, referenced through `/finplan/<env>/financeagent/agent/user-pool-ref` and `authorizer-metadata-ref` (FA-OQ-1 RESOLVED 2026-10-07). Explanation requests are authenticated by the Runtime against that pool, and every evidence job and tool read goes through the Gateway as the same caller, so explanations get no privilege the caller lacks;
- the skills mechanism.

Cross-repo rules come from FinancialPlanning `establish-cross-repo-contracts`:
- D2 identifiers and lineage. A `configuration_id` is a JCS SHA-256 hash, so identical configurations are detectable. Override is a child version, and publication is separate from execution.
- Contract rule CS-07: `completion_status` is reported separately from `solution_status`, which includes `no_effect`.
- DOM-04: explanation evidence is referenced as trusted artifacts with checksums, separate from the narrative.
- D7: phase 2 adds classical optimizers, backtests, real daily ingestion of the initial instrument (an S&P 500 tracking ETF daily series, SPY, through the `yfinance` provider adapter; contracts OQ-5 resolved 2026-10-07), Bedrock-backed explanations, and the TypeSafe Jev strategy behind explicit user approval. Phase 3 adds RL and Qwen.
- D11 (user decisions 2026-10-07): single AWS account for all environments, USD 50 total AWS budget with category allocation, Bedrock as the explanation provider.

### Observed facts

- From the sibling planning changes as of 2026-10-07: FinanceModel (`add-research-job-foundation`, `add-learning-and-llm-strategies`) and FinanceLambdasTool (`add-mcp-tool-adapters`) define **no** explanation-specific experiment types or tools.
- FinanceLambdasTool exposes `submit_experiment` (with `dry_run`), `get_job_status`, `get_experiment_result`, `get_plan`, `get_plan_version`, `list_plan_versions`, `validate_plan_version`, `publish_plan_version` and `create_override_version`.
- It deliberately excludes any execution-recording tool.
- FinanceModel states that the explanation agent is not Qwen. Its earlier wording named OpenAI as the explanation provider. That is superseded by the 2026-10-07 decision: Amazon Bedrock (contracts OQ-3 resolved).
- Phase 1 executions are `paper` or `simulated` only (contracts ID-09). "Actual" performance in this change therefore means paper or simulated execution against realized market data, never live fills.

### Assumptions (unverified)

- B-1. Plan versions produced by classical optimizers can record solver, tolerance, seed and code version in lineage so that re-solves reproduce them. This needs a FinanceModel or contracts field (GAP-E3).
- B-2. A plan version may carry a forecast distribution or interval. If it does not, workflow 1 reports `not_available` (spec), so the spec does not depend on this assumption.
- B-3. The platform's paper execution records carry the intended weights, executed weights or quantities, timestamps, and assumed versus realized fees and slippage. If they do not, the execution and cost gap is limited to what the records hold (EX-OQ-4).

## Goals / Non-Goals

**Goals:**
- Explanations whose numbers can be reproduced exactly from immutable IDs, with checks that run without an LLM.
- One evidence pipeline (request → dry-run estimate → jobs → validate → narrate → claim-check) shared by all three workflows.
- Ground-truth fixture scenarios that prove each decomposition recovers planted effects.

**Non-Goals:**
- Real-world causal inference (no counterfactual claims about markets).
- Retraining RL models to explain reward changes.
- Re-solves of the GPU-backed Qwen swarm or of the TypeSafe Jev strategy (paid external API, billed by TypeSafe outside AWS). These are limited to the difference inventory until a budgeted, human-approved experiment exists.
- Persisting explanations as authoritative records.

## Decisions

### E1. Evidence is computed by FinanceModel jobs and read through FinanceLambdasTool

- Re-solves, sweeps, Shapley attribution and performance decomposition need the same model code, solvers and evaluator that produced the plan. FinanceModel owns those (contracts D1), so the computations become **new FinanceModel CPU experiment types**:
  - `performance_decomposition`
  - `controlled_resolve`
  - `grouped_shapley`
  - `sensitivity_sweep`

  They are submitted via `submit_experiment`, and their outputs are evidence artifacts referenced by `run_id` and trusted references. **CONTRACT GAP-E1 / BLOCKER:** these types and their payloads are not yet defined in FinanceModel or the contracts.
- The cheap, synchronous lineage comparison (`compare_plan_versions`) and the reads of publications and executions should be **FinanceLambdasTool read tools** over platform APIs. **BLOCKER (dependency):** they are not in `add-mcp-tool-adapters`.
- **Rejected: computing evidence inside the agent.** It would mix numeric code into the LLM process and bypass the budget and job controls. The brief also requires tools to compute the evidence.
- **Rejected: platform-side computation of attribution.** The platform owns state and validation, not models.
- Accounting reconciliation reuses the platform's deterministic reconciliation logic where possible, called from the `performance_decomposition` job. Duplicated reconciliation code between the platform and FinanceModel is a risk (see Risks).

### E2. Evidence and result shapes

- Evidence payloads are `finance/v1` domain-adapter schemas, one per kind:
  - `performance_reconciliation`
  - `gap_decomposition`
  - `forecast_position`
  - `data_quality_report`
  - `difference_inventory`
  - `controlled_resolve_set`
  - `grouped_shapley`
  - `sensitivity_sweep`

  Each carries the identifiers it was computed from, the evaluator code version, seeds, declared tolerances and the result of every check. **CONTRACT GAP-E2:** contracts task 5.2 lists "explanation evidence" without per-kind schemas.
- The explanation result, assembled by the agent and conforming to the contracts' domain-neutral explanation envelope, holds:
  - `explanation_type`
  - the request IDs
  - `evidence[]` (trusted refs plus checksums)
  - `checks[]` (name, status, tolerance, observed)
  - `labels[]` (`modeled_effect`, `no_effect`, `requires_retraining`, `concession`, …)
  - `narrative` (`status` ∈ `generated`, `unavailable`, `budget_exceeded`; `text`; `citations[]` to evidence items)
- Explanation results are not persisted as authoritative records. They live in the session checkpoint and can be regenerated from evidence. Evidence artifacts are immutable FinanceModel run outputs. **CONTRACT GAP-E4:** the ownership matrix has no row for explanation-evidence artifacts. This design proposes FinanceModel run-output storage plus platform-issued trusted references.

### E3. Controlled re-solve protocol (workflow 2)

- Factor groups (default): `data` (snapshot), `configuration` (non-constraint fields), `constraints`, `model` (`model_version`). The groups are declared in the request and recorded in the evidence.
- Runs:
  - base reproduction: previous inputs. This must reproduce the previous version's allocations within tolerance, or the workflow stops with `VALIDATION_FAILED` because the model is not reproducible.
  - one single-switch run per group.
  - the all-switched run, which must reproduce the new version.

  That is k+2 re-solves. The interaction remainder is total − Σ single effects.
- Grouped Shapley is exact over all 2^k subsets, with k at most a configured maximum (proposed default 4, i.e. 16 re-solves). Efficiency (Σ φ = total) is checked deterministically. Sampling-based approximations are rejected in this change, because they add estimator variance that would have to be explained.
- Attributed quantities: per-asset weight change, objective value, and modeled expected return and risk.
- RL models: the `model` group is allowed only when the two versions share reward, state and action definitions (comparison by declared spec hash in the model registry). Otherwise the factor is `requires_retraining`. Stochastic models report mean ± spread over the recorded seeds (spec).

### E4. Workflow 1 decomposition

- Total gap = plan-path return − executed-path return. It is decomposed into:
  - execution gap: weight and timing deviation, valued at realized prices;
  - cost gap: realized minus assumed fees and slippage;
  - market versus forecast: plan-path realized return minus forecast mean, with its percentile position in the forecast distribution when one exists;
  - residual.
- Each path is reconciled (start + flows + P&L − fees = end) before decomposing.
- Data quality comes from the snapshot quality flags of both snapshots plus a deterministic revision diff. Revision effects are quantified only through a `controlled_resolve` or re-valuation run.

### E5. Sensitivity classification (workflow 3)

- The "original policy" is the constraint set and risk limits in effect for the base version: its configuration plus portfolio settings, read through tools. **OPEN QUESTION EX-OQ-6:** the platform's portfolio-policy schema must be defined.
- A sweep point is `permitted` if all original items hold within tolerance, and `concession` otherwise, with each item listed with its original and required value.
- No interpolation between points.

### E6. Narrative and claim check extensions

The phase 1 claim check is extended with:
- number-to-evidence matching with declared rounding;
- a causal-wording rule: words such as "caused" or "because the market" attached to modeled effects are rewritten or flagged;
- monotonicity and trend claims, which are checked against the computed points;
- `no_effect` claims, which must match `no_effect` labels.

Evidence is complete before narration. Narration uses the phase 1 provider interface (Bedrock by default). When narration fails, or the Bedrock pre-flight check refuses it, the evidence is returned alone with narrative status `unavailable` or `budget_exceeded`.

### E7. Budget and cost

RESOLVED 2026-10-07 (contracts OQ-7, EX-OQ-7): USD 50 is the total AWS budget, split by the category allocation in `/finplan/shared/financialplanning/config/budget-allocation`.

- **Evidence jobs (`cpu_research`, default USD 7).** Every workflow first calls `submit_experiment` with `dry_run`. The agent sums the estimates against the configured explanation limits from `/finplan/<env>/financeagent/config/explanation-limits` (max re-solves per request, max Shapley groups, max sweep points, max estimated cost per request) and against `remaining_allocation_usd` in the contract cost-estimate block (`budget_category` `cpu_research`). FinanceModel's own pre-flight check remains authoritative. Exceeding either gives `BUDGET_EXCEEDED`, offering cheaper evidence such as single-switch only.
- **Narration (`bedrock_explanations`, default USD 5).** The prod model is Claude Opus 5 (`us.anthropic.claude-opus-5`), a high-cost tier. Each narration is one capped invocation:
  - the explanation skills set a conservative per-invocation `max_tokens_invocation` (configuration, not code), because narratives are short and cite evidence;
  - the per-session budget check runs before the invocation; if the remaining allocation or session budget cannot cover the worst case, no call is made and the result returns the evidence with narrative status `budget_exceeded` and a clear message naming the remaining amount and the estimate;
  - the narration prompt puts the stable part (system prompt, skill instructions, output contract, disclaimer) first so prompt caching can apply where supported, and the request-specific evidence summary last;
  - only the compact evidence summary is sent, never full evidence artifacts, to bound input tokens;
  - CI and beta use the fixture narrative provider; gamma may use a cheaper model ID through the same SSM key.
  Cost estimates use the rates in the provider configuration (configured or AWS pricing looked up at run time); no prices are recorded here.
- **Backstop.** The platform AWS Budgets budget alerts at 50/80/100% and applies a deny action at 100%, which covers FinanceModel job submission and the FinanceAgent Runtime role's Bedrock invocations.
- All evidence jobs are CPU. GPU-backed strategies are excluded (Non-Goals). No prices are assumed. The default limit values are conservative configuration, not a blocker.

### E8. Testing with planted ground truth

Synthetic fixtures with known answers:
- an additive toy model, where the Shapley values equal the single effects and the interaction is 0;
- an interacting model, where the remainder is non-zero and known;
- a planted execution slippage, which must be recovered as the execution gap;
- a planted fee overrun, which must be recovered as the cost gap;
- an identical-config case (`no_effect`);
- an infeasible sweep point;
- a non-monotonic sweep.

Property tests check the identities over random fixtures. LLM output is never a correctness oracle.

## Risks / Trade-offs

- [Re-solves do not reproduce the original version (non-determinism, missing lineage)] → base-reproduction check stops the workflow. Lineage fields requested (GAP-E3).
- [2^k Shapley cost] → hard cap on k and a budget pre-check. Controlled single-switch evidence is the default.
- [Reconciliation logic duplicated between platform and FinanceModel] → one deterministic implementation in the contract package's validators or a platform API, called by the job (resolve with GAP-E1).
- [Users read modeled effects as causal] → mandatory labels, the wording check, and a fixed disclaimer in each skill's output contract.
- [Paper execution records lack fee or slippage detail] → the cost gap reports `not_available` for missing components rather than inventing them (EX-OQ-4).
- [Phase 2 dependencies (classical optimizers, real data OQ-5) slip] → all workflows are developed and tested on fixtures. Deployment is gated on producer releases (`DEPENDENCY_UNAVAILABLE` otherwise).

## Migration Plan

1. Land the contract minor with the evidence schemas, explanation envelope fields and lineage solver fields (GAP-E2, GAP-E3), then the FinanceModel experiment types (GAP-E1) and the FinanceLambdasTool read tools.
2. Release FinanceAgent skills behind the per-environment flag `explanations.enabled`. In beta they run on fixture-backed evidence jobs (FinanceModel CPU stub) with the fixture narrative provider.
3. Promote to gamma with gamma FinanceModel releases and the Bedrock provider (after Opus 5 model access is enabled at bootstrap and verified, tasks 3.8–3.9 in `add-agent-runtime-and-gateway`). Enable in prod after approval. Rollback means disabling the flag or redeploying the previous `release_id`. Evidence artifacts are immutable and unaffected.

## Contract gap status (cross-repo review, 2026-10-07)

- GAP-E1: still a BLOCKER for deployed evidence. The experiment kinds are not in any FinanceModel change. Contracts OQ-12 records it: a follow-up FinanceModel change after the evidence schemas exist.
- GAP-E2 and GAP-E3: scheduled as phase 2 contract minors (contracts D10: per-kind evidence schemas and envelope fields; optional lineage fields `solver`, `solver_version`, `tolerance`, `seed`, `code_version`).
- GAP-E4: resolved in contracts D1. Explanation-evidence artifacts are FinanceModel run outputs, referenced by FinanceModel-issued trusted references of kind `explanation_evidence`.
- EX-OQ-9: still open. `compare_plan_versions` and the publication and execution read tools need a follow-up FinanceLambdasTool change. The platform read routes already exist (`GET /v1/publications/{id}`, `GET /v1/executions/{id}`, `GET /v1/plans/{plan_id}/versions`).

## Open Questions

| ID | Question | Blocks | Resolved by | Interim |
|---|---|---|---|---|
| EX-OQ-1 | FinanceModel experiment types and payloads for `performance_decomposition`, `controlled_resolve`, `grouped_shapley` and `sensitivity_sweep` (CONTRACT GAP-E1) | BLOCKER for deployed evidence | FinanceModel change plus contract minor | Local fixture evidence only |
| EX-OQ-2 | Per-kind evidence schemas and explanation envelope fields in `finance/v1` (CONTRACT GAP-E2) | BLOCKER for the contract tests | FinancialPlanning contract minor | Draft schemas in the test fixtures of this repo only, never published |
| EX-OQ-3 | Owner of explanation-evidence artifact storage (CONTRACT GAP-E4) | Resolved: FinanceModel run outputs (contracts D1) | n/a | n/a |
| EX-OQ-4 | Do platform paper or simulated execution records hold executed quantities, timestamps, and assumed versus realized fees and slippage? Does a plan version carry a forecast distribution? | Partial BLOCKER for workflow 1 components | FinancialPlanning platform change review | Missing components reported `not_available` |
| EX-OQ-5 | "Effective horizon" definition and alignment rules per model family | BLOCKER for workflow 2 on non-matching horizons | FinanceModel and user | Exact-match horizons only |
| EX-OQ-6 | Portfolio-policy (risk preferences and constraints) schema at the platform | BLOCKER for workflow 3 classification | FinancialPlanning contract | Use the base version's configuration constraints only |
| EX-OQ-7 | Explanation limits and budget (max groups, sweep points, cost) | None (config) | **RESOLVED 2026-10-07:** evidence jobs within `cpu_research` (default USD 7) and narration within `bedrock_explanations` (default USD 5) of the USD 50 total; per-request limits stay conservative configuration defaults (E7) | n/a |
| EX-OQ-8 | Initial instrument and data provider for realized data (= contracts OQ-5) | None | **RESOLVED 2026-10-07:** SPY daily completed observations (distinct from the index level and constituent universe) through the platform's `yfinance` adapter, XNYS calendar via `exchange_calendars`; retrieved data never committed; CI uses the mock provider | Synthetic fixtures and the mock provider until the platform ingestion ships |
| EX-OQ-9 | FinanceLambdasTool read tools: `compare_plan_versions` and publication/execution reads | BLOCKER for workflows 1 and 2 through the Gateway | FinanceLambdasTool change | Fixture tool fakes in graph tests |
| EX-OQ-10 | Bedrock explanation model and model access (= FA-OQ-4 in `add-agent-runtime-and-gateway`, contracts OQ-13) | None (model access is a bootstrap task in `add-agent-runtime-and-gateway`) | **RESOLVED 2026-10-07:** Claude Opus 5 via `us.anthropic.claude-opus-5` from SSM; model access enabled at bootstrap; narration under per-invocation caps and the per-session budget check | Fixture narrative provider until access is verified |
| EX-OQ-11 | Which identity do explanation requests and evidence jobs run under? (depends on FA-OQ-1 in `add-agent-runtime-and-gateway`, CONTRACT GAP-1) | None | **RESOLVED 2026-10-07:** the caller's identity from the environment's FinanceAgent-owned Cognito user pool (FA-OQ-1, user decision 15a); evidence jobs need a group the Gateway policy allows to call `submit_experiment`, otherwise only reads of existing evidence run | n/a |
