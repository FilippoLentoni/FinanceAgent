# Proposal

## Why

Users need to understand three things: why realized results differ from the exact plan that was published; why a new recommendation differs from the previous one; and what they could change and at what cost relative to their original policy. A free-form LLM answer to these questions cannot be trusted with money. The explanations must rest on deterministic numerical evidence that tools compute and that can be reproduced from immutable identifiers. The narrative must be kept separate from that evidence and checked against it. This is phase 2. It builds on the phase 1 agent, Gateway, provider and claim check from `add-agent-runtime-and-gateway`, and it needs the phase 2 classical optimizers and real data described in contracts D7.

## What Changes

- Add three **explanation workflows** to the LangGraph agent, each delivered as an agent skill:
  1. **Actual performance versus the exact published plan.** Anchored on a `publication_id`, so on the exact `plan_version_id` and checksum. Separates accounting reconciliation, forecast uncertainty (realized versus forecast distribution), execution and cost gaps (paper or simulated fills, fees, timing) and data quality.
  2. **New versus previous recommendation for the same effective horizon.** Diffs the inputs (snapshot), configuration (`configuration_id` fields), `model_version` and constraints. Runs **controlled re-solves** that switch one factor group at a time. Optionally runs **grouped Shapley** attribution under a budget pre-check.
  3. **Sensitivity and permitted recommendations.** Sweeps risk, turnover and constraints. Reports portfolio-wide effects across all holdings. Labels every point as permitted under the original policy or as requiring explicit **original-policy concessions**.
- **Deterministic evidence first.** All numbers come from tool-computed evidence artifacts: contract trusted references with checksums, reproducible from the referenced IDs. The narrative is a separate field, generated afterwards. Every figure in it is checked deterministically against the evidence.
- **Modeled effects are not real-world causality.** Every attribution and sweep result is labeled as a modeled intervention effect under the stated model and inputs.
- **No-effect changes are valid results.** They are reported as such, not as errors.
- **RL reward changes may require retraining.** These are never "re-solved" in an explanation. They are flagged `requires_retraining` and offered only as a budgeted experiment proposal that needs human approval.
- **Financial correctness is verified by deterministic code** (identities, sums, tolerances), never by LLM judging.
- **No automated rewriting of risk preferences.** Concessions are presented to the user and never applied.
- **Out of scope:** live trading, Coinbase, AgentCore payments, wallet spending, real-world causal claims, and automatic policy or preference changes.

## Capabilities

### New Capabilities

- `explanation-evidence`: rules shared by all workflows. Covers the evidence/narrative separation, evidence provenance and reproducibility, deterministic correctness checks, the modeled-versus-causal labeling, no-effect handling, budget gating of evidence jobs, running as the authenticated caller, and the ban on preference rewriting.
- `plan-performance-explanation`: workflow 1, actual versus the exact published plan.
- `recommendation-change-explanation`: workflow 2, new versus previous recommendation for the same effective horizon, with controlled re-solves and optional grouped Shapley.
- `sensitivity-explanation`: workflow 3, risk, turnover and constraint sweeps, portfolio-wide effects, and permitted recommendations versus original-policy concessions.

### Modified Capabilities

None. The phase 1 capabilities from `add-agent-runtime-and-gateway` are used unchanged. The skills are added under the existing `agent-skills` rules.

## Impact

- **This repo:** three new skills and graph subflows, explanation request and result assembly, evidence-to-narrative claim checks, and workflow-specific deterministic validators.
- **FinanceModel (dependency, not owned here):** deterministic evidence computations need new experiment types run as CPU jobs: performance reconciliation and decomposition, controlled re-solve, grouped Shapley and sensitivity sweep. None exist in FinanceModel's current changes. Recorded as CONTRACT GAP and BLOCKER.
- **FinanceLambdasTool (dependency):** evidence jobs go through `submit_experiment`, `get_job_status` and `get_experiment_result`. Reads of executions, publications and evidence artifacts may need new read tools. Recorded as a BLOCKER.
- **Identity (RESOLVED 2026-10-07, FA-OQ-1):** explanation requests and their evidence jobs run as the caller authenticated by the environment's FinanceAgent-owned Cognito user pool (delivered by `add-agent-runtime-and-gateway`, referenced through SSM). Explanations gain no permission beyond the caller's groups.
- **FinancialPlanning contracts:** per-workflow evidence payload schemas in `finance/v1` and the explanation request/result envelope (contracts DOM-04), plus an owner for the explanation-evidence artifact storage. Both are recorded as CONTRACT GAPs.
- **Cost (within the USD 50 total AWS budget, contracts OQ-7 resolved 2026-10-07):** evidence jobs are CPU-only, budget-pre-checked through dry-run estimates, and count against the `cpu_research` allocation (default USD 7). Shapley cost grows as 2^k re-solves and is capped by configuration. Narrative generation uses the phase 1 **Amazon Bedrock** provider with **Claude Opus 5** through the US inference profile `us.anthropic.claude-opus-5` (IAM auth, no API key; model ID from `/finplan/<env>/financeagent/config/explanation-model-id`, never hard-coded; beta/gamma may configure a cheaper model). Opus is a high-cost tier, so narration counts against the `bedrock_explanations` allocation (default USD 5) under the phase 1 controls: per-invocation max-token caps (set low for explanation skills), the per-session budget check before every invocation, prompt caching of the stable prompt prefix where supported, and the fixture provider in CI. When narration is refused or unavailable, the evidence is still returned.
