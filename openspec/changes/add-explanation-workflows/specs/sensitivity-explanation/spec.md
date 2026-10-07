# Spec Delta

## Purpose

Shows how a recommendation responds to changes in risk, turnover and constraint settings, including effects across the whole portfolio. Each alternative is classified as permitted under the user's original policy or as requiring explicit concessions from it.

## ADDED Requirements

### Requirement: Declared sweep specification
A sensitivity request SHALL name the base `plan_version_id`, the parameters to sweep (risk aversion or risk target, turnover limit, individual constraint bounds), their grid or range and the fixed inputs. The workflow MUST validate the grid size against the configured maximum and run a budget pre-check before submission.

#### Scenario: Grid too large
- **WHEN** the requested grid exceeds the configured maximum number of re-solves
- **THEN** the workflow refuses with `VALIDATION_FAILED` and states the maximum

#### Scenario: Valid sweep
- **WHEN** a turnover sweep of five values is requested for `pv_A`
- **THEN** five deterministic re-solves are submitted from `pv_A`'s inputs, changing only turnover

### Requirement: Portfolio-wide effects
For every sweep point, the evidence SHALL report the full allocation and its change versus the base for every holding, plus portfolio-level metrics (expected return and risk under the model, turnover, cost estimate, constraint slack). Effects MUST NOT be reported only for the parameter's directly constrained asset.

#### Scenario: Single-asset cap tightened
- **WHEN** one asset's maximum weight is lowered in a sweep
- **THEN** the evidence shows the reallocation across all other holdings and the change in portfolio risk and return

### Requirement: Permitted versus concession classification
Each sweep point SHALL be classified as `permitted` (satisfies every constraint and risk limit of the original policy) or `concession` (violates at least one). Each concession MUST list every original-policy item it relaxes and by how much.

#### Scenario: Concession reported
- **WHEN** a sweep point needs turnover 30% against an original limit of 20%
- **THEN** it is classified `concession` with item `turnover_limit`, original 20%, required 30%

#### Scenario: Permitted alternative
- **WHEN** a sweep point satisfies every original limit
- **THEN** it is classified `permitted` with no concessions

### Requirement: Infeasible and no-effect points are valid
Sweep points that are infeasible, or that produce the same allocation as the base within tolerance, SHALL be reported with `solution_status` `infeasible` or `no_effect` respectively and `completion_status` `succeeded`. They MUST NOT be dropped from the evidence.

#### Scenario: Flat region
- **WHEN** risk aversion values 2.0 to 3.0 all produce the base allocation
- **THEN** each point is reported `no_effect`, and the narrative states that the recommendation is insensitive in that range under the model

### Requirement: Alternatives are not applied automatically
Sweep results SHALL be presented as options only. Creating a plan version from a sweep point MUST require the user to choose it explicitly through the confirmed plan tools. A concession point MUST display its concessions in the confirmation request.

#### Scenario: User adopts a concession
- **WHEN** a user asks to adopt a concession point
- **THEN** the agent shows the concessions and the exact configuration, waits for confirmation, and only then calls the plan tool, without altering the stored original policy

### Requirement: No monotonicity assumptions
The workflow SHALL report sweep results as computed. It MUST NOT interpolate between points or assume monotonic behavior, and any trend statement in the narrative MUST be supported by the computed points.

#### Scenario: Non-monotonic response
- **WHEN** computed risk rises, then falls, across a turnover sweep
- **THEN** the evidence shows the non-monotonic values, and the claim check rejects a narrative that calls the response monotonic
