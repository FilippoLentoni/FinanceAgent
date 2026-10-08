# Tasks

Scope: phase 2. This change requires `add-agent-runtime-and-gateway` to be deployed, including its per-environment Cognito identity stack (FA-OQ-1 RESOLVED 2026-10-07). Deployed evidence additionally needs contract and producer changes (design.md EX-OQ-1, EX-OQ-2, EX-OQ-9). Bedrock narration uses Claude Opus 5 via `us.anthropic.claude-opus-5` from SSM (EX-OQ-10 resolved 2026-10-07) and is live only after model access is enabled and verified at bootstrap (`add-agent-runtime-and-gateway` tasks 3.8–3.9); until then, and always in CI, narration uses the fixture provider. Until the producer changes exist, work proceeds on local fixtures and tool fakes. No GPU jobs, live data or live trading. All environments share one AWS account (contracts OQ-1 resolved); costs stay inside the USD 50 total (`cpu_research` for evidence jobs, `bedrock_explanations` for narration). Numerical correctness is asserted only by deterministic tests.

## 1. Shared evidence pipeline

- [x] 1.1 Implement the explanation result assembler (`explanation_type`, request IDs, `evidence[]` trusted refs + checksums, `checks[]`, `labels[]`, `narrative{status,text,citations}`) validated against the contract explanation envelope; verify FA-EV-02 contract tests (BLOCKED for published schemas by EX-OQ-2; uses draft fixture schemas until then)
- [x] 1.2 Implement the evidence subflow (dry-run estimates → budget check against `/finplan/<env>/financeagent/config/explanation-limits` and the estimate's `remaining_allocation_usd` for `cpu_research` → submit → poll → fetch → validate checks → narrate → claim-check); verify FA-EV-07 graph tests (over-budget offers cheaper evidence; insufficient `cpu_research` allocation submits no job)
- [x] 1.3 Implement deterministic check runners (identity, sum-to-total, weights sum, constraint satisfaction, base reproduction) that block narration on failure; verify FA-EV-04 unit and property tests
- [x] 1.4 Extend the claim check with evidence number matching, causal-wording, monotonicity and no-effect rules; verify FA-EV-01 and FA-EV-05 unit tests with planted narrative errors
- [x] 1.5 Implement `no_effect` handling (identical `configuration_id`/snapshot/checksum, or effect within tolerance); verify FA-EV-06 with the identical-config fixture
- [ ] 1.6 Implement the reproducibility check (resubmit with recorded IDs and seeds, compare checksums); verify FA-EV-03 against the FinanceModel CPU stub in beta (BLOCKED by EX-OQ-1)
- [x] 1.7 Add a guard that no explanation subflow can call state-changing tools without the phase 1 confirmation interrupt; verify FA-EV-08 graph test (concession found, no tool call)
- [x] 1.8 Document the evidence/narrative contract and the modeled-effect disclaimer in `docs/explanations.md`; verify that the documentation examples validate with the assembler
- [x] 1.9 Route narration through the phase 1 provider interface (Bedrock by default, fixture in tests) and map a Bedrock pre-flight refusal to narrative status `budget_exceeded` and an unavailable provider to `unavailable`, with evidence returned in both cases; verify FA-EV-02 graph tests with the fixture provider and a stubbed refusal (no network)
- [x] 1.10 Configure narration cost controls for the explanation skills: a conservative per-invocation `max_tokens_invocation` in skill configuration (no model ID or price in code or skill bundles), the per-session budget check before the narration call with a clear `budget_exceeded` message naming the remaining amount and estimate, a cache-friendly prompt layout (stable prefix first, compact evidence summary last), and a CI guard that explanation tests run with the fixture provider only; verify FA-EV-09 graph tests with a stubbed Bedrock client (cap sent, session budget insufficient → evidence returned with `budget_exceeded`, zero Bedrock calls in CI)
- [ ] 1.11 Run every explanation subflow's tool calls and evidence job submissions through the Gateway as the authenticated caller (no service identity); map a policy denial of `submit_experiment` to an evidence-unavailable result; verify FA-EV-10 graph tests with fake tools (allowed and denied groups) and the beta audit check using a project-owner token from the beta pool
- [ ] 1.12 Set the default explanation model to Claude Haiku 4.5 (decision 21, 2026-10-08: Opus/Sonnet 5.x and Opus 4.8 are not available for this account). Write `us.anthropic.claude-haiku-4-5-20251001-v1:0` to `/finplan/<env>/financeagent/config/explanation-model-id` as the default in beta, gamma and prod config. Scope the Runtime role's Bedrock grant to that US inference profile plus its underlying regional foundation-model ARNs. Set the configured per-token prices for the Bedrock cost estimate to Haiku 4.5. Opus 5 remains a config-only upgrade through the same key once access is granted. Verify:
  - a config unit test asserts the default ID and rejects `global.` profiles;
  - an IAM policy-simulation test allows the Haiku profile and its foundation-model ARNs, and denies other models;
  - in deployed beta, one capped explanation request sent to the deployed agent returns narration with usage recorded against `bedrock_explanations` (no CI Bedrock calls).

## 2. Workflow 1: actual versus exact published plan

- [x] 2.1 Implement publication anchoring (`publication_id` → `plan_version_id` + checksum; refuse unpublished versions); verify FA-WF1-01 graph tests with fixture tools (BLOCKED for Gateway use by EX-OQ-9)
- [x] 2.2 Implement window and actual-source handling (paper/simulated, `intraday_partial` flagging); verify FA-WF1-02
- [x] 2.3 Wire `performance_decomposition` evidence (reconciliation of both paths, four-component gap, forecast position or `not_available`, data-quality report); verify FA-WF1-03..06 with the planted-slippage, planted-fee, no-forecast and revised-observation fixtures (BLOCKED by EX-OQ-1, EX-OQ-4 for deployed runs)
- [ ] 2.4 Add the `explain-performance` skill bundle with output contract and disclaimer; verify skill validation and a beta fixture run

## 3. Workflow 2: new versus previous recommendation

- [x] 3.1 Implement the effective-horizon check (exact match; alignment only when defined); verify FA-WF2-01 (BLOCKED for alignment rules by EX-OQ-5)
- [x] 3.2 Implement the difference inventory via `compare_plan_versions` (snapshot, configuration JSON pointers, constraints, `model_version`, origin); verify FA-WF2-02 with configuration-diff and override fixtures (BLOCKED for Gateway use by EX-OQ-9)
- [x] 3.3 Implement the controlled re-solve protocol (base reproduction, single switches, all-switched, interaction remainder, infeasible switches); verify FA-WF2-03 with additive and interacting toy-model fixtures
- [x] 3.4 Implement optional grouped Shapley (exact, k ≤ configured maximum, efficiency check); verify FA-WF2-04 (additive fixture: Shapley = single effects; too-many-groups refusal)
- [x] 3.5 Implement the RL `requires_retraining` detection by model-spec hash comparison; verify FA-WF2-05 (reward-changed fixture submits no job)
- [x] 3.6 Implement reproducible solver settings and the seed-spread reporting; verify FA-WF2-06 with the stochastic fixture (BLOCKED for lineage fields until the phase 2 contract minor in contracts D10 ships)
- [ ] 3.7 Add the `explain-recommendation-change` skill bundle; verify skill validation and a beta fixture run

## 4. Workflow 3: sensitivity and permitted recommendations

- [x] 4.1 Implement sweep request validation (base version, parameters, grid limit, budget pre-check); verify FA-WF3-01
- [x] 4.2 Wire `sensitivity_sweep` evidence with full per-holding allocations and portfolio metrics; verify FA-WF3-02 with the single-asset-cap fixture (BLOCKED by EX-OQ-1 for deployed runs)
- [x] 4.3 Implement permitted/concession classification against the original policy; verify FA-WF3-03 (BLOCKED for the portfolio-policy source by EX-OQ-6; uses base configuration constraints until then)
- [x] 4.4 Keep infeasible and no-effect points; verify FA-WF3-04 with the flat-region and infeasible fixtures
- [x] 4.5 Implement adopt-alternative through the confirmation interrupt showing concessions; verify FA-WF3-05 graph test (stored policy unchanged)
- [x] 4.6 Enforce no interpolation and check trend claims; verify FA-WF3-06 with the non-monotonic fixture
- [ ] 4.7 Add the `explain-sensitivity` skill bundle; verify skill validation and a beta fixture run

## 5. Integration checks

- [ ] 5.1 Run all three workflows end-to-end in beta against the FinanceModel CPU stub evidence jobs and platform reads (real phase 2 data in beta, decision 26); verify evidence checksums reproduce and claim checks pass (BLOCKED by EX-OQ-1, EX-OQ-2, EX-OQ-9)
- [ ] 5.2 Run them in gamma against gamma producers only (gamma platform data is real phase 2 data from this release, decision 26), with Bedrock narration under the configured per-turn cap; verify the gamma isolation tests from the phase 1 change still pass, narration usage is recorded against `bedrock_explanations`, and explanations are disabled in prod until approval (Bedrock narration requires Opus 5 model access verified by `add-agent-runtime-and-gateway` task 3.9; gamma may use a cheaper configured model ID; fixture narration otherwise)
- [ ] 5.3 Prod smoke: one read-only workflow 2 `no_effect` explanation on the synthetic portfolio with the identical-config fixture (the portfolio stays synthetic; prod market data may be real or still synthetic during the transition, decision 26, and both are accepted); verify no job or plan state is created beyond the read-only evidence lookup

## Verification status (2026-10-08)

Checked tasks are verified **offline only** (graph, unit and property tests with fixture tools and the fixture provider, zero Bedrock calls); nothing is deployed yet. Draft evidence schemas stand in for EX-OQ-2. Open items:

- 1.6, 5.1: blocked by EX-OQ-1 (FinanceModel evidence job types) and EX-OQ-9 (FinanceLambdasTool read tools).
- 1.11: graph tests pass; the beta audit check is open.
- 1.12: `config/<env>.json` already sets the Haiku 4.5 ID. Two things are open: the IAM simulation runs with a fixture profile routed to three regions rather than the Haiku profile itself, and the deployed-beta narration check contradicts the beta fixture-only rule (FA-PRV-14). That check belongs in gamma once the provider is switched to `bedrock`.
- 2.4, 3.7, 4.7: the skill bundles validate offline; their beta fixture runs are open.
- 5.2, 5.3: not started.

## Requirement-to-test mapping

Test types: unit, contract, integration-beta, gamma, smoke. "graph" = unit-level LangGraph tests with fake tools. "fixture" = planted ground-truth scenario (design E8).

| Capability | Requirement | Test ID | Type |
|---|---|---|---|
| explanation-evidence | Evidence computed by deterministic tools | FA-EV-01 | unit (claim check) + integration-beta |
| explanation-evidence | Evidence is separate from narrative | FA-EV-02 | contract + unit (provider unavailable; Bedrock allocation exhausted) + gamma (Bedrock narration, after model access is verified) |
| explanation-evidence | Evidence is reproducible from identifiers | FA-EV-03 | integration-beta + gamma |
| explanation-evidence | Deterministic correctness checks | FA-EV-04 | unit (property tests) |
| explanation-evidence | Modeled intervention effects are labeled | FA-EV-05 | unit (wording check) |
| explanation-evidence | No-effect outcomes are valid | FA-EV-06 | unit (fixture) + smoke |
| explanation-evidence | Evidence jobs are budget-gated | FA-EV-07 | unit (graph; `cpu_research` remaining allocation) + integration-beta (dry-run) |
| explanation-evidence | Explanations run as the authenticated caller | FA-EV-10 | unit (graph; allowed and denied groups) + integration-beta (audit shows the caller, token from the beta pool) |
| explanation-evidence | No automated rewriting of risk preferences | FA-EV-08 | unit (graph) + gamma (policy) |
| explanation-evidence | Narration within Bedrock cost controls | FA-EV-09 | unit (graph, stubbed Bedrock client) + gamma (usage metric against `bedrock_explanations`) |
| plan-performance-explanation | Anchored on the exact published plan | FA-WF1-01 | unit (graph) + integration-beta |
| plan-performance-explanation | Explicit evaluation window and actual source | FA-WF1-02 | unit |
| plan-performance-explanation | Accounting reconciliation | FA-WF1-03 | unit (fixture) + integration-beta |
| plan-performance-explanation | Gap decomposition | FA-WF1-04 | unit (planted slippage/fee fixtures) |
| plan-performance-explanation | Forecast uncertainty context | FA-WF1-05 | unit (fixture) |
| plan-performance-explanation | Data quality effects | FA-WF1-06 | unit (revised-observation fixture) + integration-beta |
| recommendation-change-explanation | Same effective horizon | FA-WF2-01 | unit |
| recommendation-change-explanation | Deterministic difference inventory | FA-WF2-02 | unit + contract |
| recommendation-change-explanation | Controlled re-solves | FA-WF2-03 | unit (toy models) + integration-beta |
| recommendation-change-explanation | Optional grouped Shapley attribution | FA-WF2-04 | unit (efficiency) + integration-beta |
| recommendation-change-explanation | RL reward changes require retraining | FA-WF2-05 | unit (graph) |
| recommendation-change-explanation | Reproducible solver settings | FA-WF2-06 | unit + gamma |
| sensitivity-explanation | Declared sweep specification | FA-WF3-01 | unit |
| sensitivity-explanation | Portfolio-wide effects | FA-WF3-02 | unit (fixture) + integration-beta |
| sensitivity-explanation | Permitted versus concession classification | FA-WF3-03 | unit |
| sensitivity-explanation | Infeasible and no-effect points are valid | FA-WF3-04 | unit (fixture) + contract (CS-07) |
| sensitivity-explanation | Alternatives are not applied automatically | FA-WF3-05 | unit (graph) + gamma |
| sensitivity-explanation | No monotonicity assumptions | FA-WF3-06 | unit (claim check) |

Shared contract tests that also run here: DOM-04 (domain-neutral explanation evidence), CS-07 (completion versus solution status), CS-10 (conformance), CS-11 and ENV-17 (budget category and allocation), ENV-08 (secret references; no explanation-provider secret). Provider-level tests FA-PRV-01..15 (including the per-invocation cap, per-session budget check, prompt caching, no Bedrock in CI and model-access verification) live in `add-agent-runtime-and-gateway`.

## Workflow follow-up

- Archive after all three workflows pass in gamma and the prod smoke succeeds.
