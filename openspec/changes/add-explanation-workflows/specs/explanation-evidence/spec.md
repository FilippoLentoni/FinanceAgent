# Spec Delta

## Purpose

Shared rules for every explanation workflow: deterministic, reproducible numerical evidence computed by tools; narrative kept separate and checked against that evidence; modeled effects labeled as such; no-effect outcomes accepted; and no automatic changes to a user's risk preferences.

## ADDED Requirements

### Requirement: Evidence computed by deterministic tools
Every number in an explanation SHALL come from an evidence artifact produced by a deterministic tool or job and referenced by a contract trusted artifact reference with checksum. The agent and the explanation provider MUST NOT compute financial figures themselves.

#### Scenario: Evidence reference present
- **WHEN** any explanation result is returned
- **THEN** it lists one or more evidence references (`artifact_id`, kind, checksum), and each evidence number in the result can be found in a referenced artifact

#### Scenario: Provider attempts arithmetic
- **WHEN** the narrative draft contains a figure derived by the provider (for example, a sum not present in the evidence)
- **THEN** the deterministic claim check flags it and the figure is removed or replaced by the evidence value before return

### Requirement: Evidence is separate from narrative
An explanation result SHALL contain a structured `evidence` section and a separate `narrative` field. The narrative MUST be generated after the evidence is final, MUST NOT be the only carrier of a numeric result, and MUST cite the evidence items it uses.

#### Scenario: Narrative generation fails
- **WHEN** the explanation provider is unavailable
- **THEN** the result still returns the complete evidence section with narrative status `unavailable`

#### Scenario: Bedrock allocation exhausted
- **WHEN** the phase 1 Bedrock pre-flight check refuses narration because the `bedrock_explanations` allocation would be exceeded
- **THEN** the result still returns the complete evidence section with narrative status `budget_exceeded`, and no Bedrock call is made

### Requirement: Narration within Bedrock cost controls
Narration for explanation workflows SHALL be one provider invocation per explanation, sent with the explanation skills' configured per-invocation maximum output tokens and preceded by the phase 1 per-session budget check. The narration request MUST contain only the stable prompt prefix and a compact evidence summary, MUST NOT contain a hard-coded model identifier or price, and explanation tests in CI MUST use the fixture provider only.

#### Scenario: Session budget insufficient for narration
- **WHEN** the per-session budget check finds that the remaining `bedrock_explanations` allocation or session budget cannot cover the narration's worst-case cost
- **THEN** no Bedrock call is made and the result returns the complete evidence with narrative status `budget_exceeded` and a message naming the remaining amount and the estimate

#### Scenario: Capped narration call
- **WHEN** narration runs with provider kind `bedrock`
- **THEN** the request carries the explanation skills' `max_tokens_invocation` and the usage record attributes its estimated cost to `bedrock_explanations`

#### Scenario: Explanation tests in CI
- **WHEN** the explanation graph tests run in the build stage
- **THEN** narration uses the fixture provider and the stubbed Bedrock client records zero calls

### Requirement: Evidence is reproducible from identifiers
Each evidence artifact SHALL record the exact identifiers it was computed from: `plan_version_id`, `publication_id`, `execution_id`, `input_snapshot_id`, `configuration_id`, `model_version` and `run_id` as applicable, plus the evaluator code version and seeds. Recomputing from those MUST reproduce the same checksum.

#### Scenario: Recompute evidence
- **WHEN** the integration test resubmits an evidence job with the identifiers and seeds recorded in an existing evidence artifact
- **THEN** the new artifact's checksum equals the original

### Requirement: Deterministic correctness checks
Each workflow's evidence SHALL pass deterministic checks before narration: accounting identities, components summing to totals within declared tolerance, weights summing to one, and constraint satisfaction as reported. A failed check MUST block narrative generation and be reported. LLM judging MUST NOT be a correctness gate.

#### Scenario: Decomposition does not sum
- **WHEN** the components of a decomposition differ from the reported total by more than the declared tolerance
- **THEN** the explanation returns `VALIDATION_FAILED` naming the failed check, and no narrative is generated

### Requirement: Modeled intervention effects are labeled
Every attribution, re-solve or sweep result SHALL be labeled `modeled_effect`, together with the model, inputs and assumptions under which it holds. The narrative MUST NOT describe these as real-world causes.

#### Scenario: Causal wording in narrative
- **WHEN** the narrative draft says a factor "caused" a realized market outcome on the basis of a modeled re-solve
- **THEN** the wording check rewrites or flags it as a modeled effect under the stated model

### Requirement: No-effect outcomes are valid
A change whose modeled effect is zero within tolerance, or whose canonical inputs are identical, SHALL be reported as `no_effect` with `completion_status` `succeeded`. It MUST NOT be reported as an error or omitted.

#### Scenario: Identical configuration
- **WHEN** the previous and new recommendations share the same `configuration_id` and `input_snapshot_id`, and the evidence shows identical allocations
- **THEN** the explanation reports `no_effect` with the identical checksums as evidence

### Requirement: Evidence jobs are budget-gated
Evidence computations that submit jobs SHALL first obtain a dry-run cost estimate and MUST stop with `BUDGET_EXCEEDED` when it exceeds the configured explanation limits or the remaining `cpu_research` allocation reported in the estimate. Paid or GPU jobs MUST NOT be submitted without recorded human approval outside the agent.

#### Scenario: Shapley over budget
- **WHEN** a requested grouped Shapley attribution's estimate exceeds the configured explanation budget
- **THEN** the agent returns `BUDGET_EXCEEDED` with the estimate and offers the controlled re-solve evidence instead

#### Scenario: CPU research allocation insufficient
- **WHEN** the dry-run estimate's `budget_category` is `cpu_research` and its `estimated_usd_upper_bound` exceeds `remaining_allocation_usd`
- **THEN** the agent submits no job and returns `BUDGET_EXCEEDED` with the estimate and the remaining allocation

### Requirement: Explanations run as the authenticated caller
Every explanation request SHALL be authenticated against the same environment's FinanceAgent-owned Cognito user pool, and every evidence job submission and tool read it makes MUST go through that environment's Gateway on behalf of the same caller. An explanation MUST NOT use a service identity or any permission the caller's groups do not grant.

#### Scenario: Caller not allowed to submit evidence jobs
- **WHEN** a caller whose groups the Gateway policy does not allow to call `submit_experiment` requests a workflow that needs a new evidence job
- **THEN** the Gateway denies the submission with `FORBIDDEN`, no job is created, and the result explains that the evidence is unavailable for this caller

#### Scenario: Evidence job audit
- **WHEN** an authenticated researcher requests a sensitivity explanation in gamma
- **THEN** the evidence job's audit record shows that caller and channel `hosted_agent`, and the token was issued by the gamma pool

### Requirement: No automated rewriting of risk preferences
Explanation workflows SHALL NOT create, modify or publish configurations, constraints, risk preferences or plan versions on their own. Any alternative MUST be presented to the user as an option, and adoption MUST go through the normal confirmed plan tools.

#### Scenario: Concession identified
- **WHEN** a workflow finds that a recommendation is reachable only by relaxing the user's turnover limit
- **THEN** the result lists the concession and no tool call changes the stored limit
