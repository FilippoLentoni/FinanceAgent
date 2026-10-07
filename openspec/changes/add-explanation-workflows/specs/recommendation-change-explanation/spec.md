# Spec Delta

## Purpose

Explains why a new recommendation differs from the previous one for the same effective horizon. It uses deterministic input and configuration diffs, controlled re-solves that switch factor groups one at a time, and optional grouped Shapley attribution, while recognizing RL reward changes that would need retraining.

## ADDED Requirements

### Requirement: Same effective horizon
The workflow SHALL compare two plan versions only when their effective horizons (decision date, holding period and rebalance schedule) match, or after aligning both to an explicitly reported common horizon. If no common horizon exists, it MUST refuse with `PRECONDITION_FAILED`.

#### Scenario: Horizons differ
- **WHEN** the previous version targets a 1-month horizon and the new one a 3-month horizon, with no alignment defined for that model
- **THEN** the workflow refuses with `PRECONDITION_FAILED` and states both horizons

### Requirement: Deterministic difference inventory
The workflow SHALL list every difference between the two versions' lineage: `input_snapshot_id` (with observation-level changes), canonical configuration fields (by JSON pointer), constraints, `model_version`, and origin such as override or Excel import. Identical lineage MUST yield an empty inventory.

#### Scenario: Configuration diff
- **WHEN** the new version's configuration differs only in risk aversion 2.0 → 2.5
- **THEN** the inventory lists exactly that JSON pointer with both values and both `configuration_id`s

#### Scenario: Override child version
- **WHEN** the new version is a manual override child of the previous version
- **THEN** the inventory reports origin `manual_override`, and no re-solve is attempted for the manual change

### Requirement: Controlled re-solves
For model-run versions, the workflow SHALL submit deterministic re-solves that start from the previous version's inputs and switch one declared factor group (data, configuration, constraints, model) to its new value at a time. It MUST report each group's modeled allocation and objective change, plus the interaction remainder.

#### Scenario: Two factor groups changed
- **WHEN** both the snapshot and the risk aversion changed
- **THEN** the evidence contains a re-solve for each single switch and for both, and the single effects plus the interaction remainder equal the total change within tolerance

#### Scenario: Re-solve infeasible
- **WHEN** a single-switch re-solve is infeasible
- **THEN** the evidence records `solution_status` `infeasible` for that switch with `completion_status` `succeeded`, and the workflow continues

### Requirement: Optional grouped Shapley attribution
On request, the workflow SHALL compute grouped Shapley attribution over at most the configured number of factor groups, after a budget pre-check. Group attributions MUST sum to the total change within tolerance, and the evidence MUST state the group definitions, ordering-invariance and number of re-solves.

#### Scenario: Shapley efficiency check
- **WHEN** a grouped Shapley result over three groups is returned
- **THEN** the deterministic check confirms the three values sum to the total change within tolerance

#### Scenario: Too many groups
- **WHEN** a user requests Shapley over more groups than the configured maximum
- **THEN** the workflow refuses with `VALIDATION_FAILED` stating the maximum and the 2^k re-solve count

### Requirement: RL reward changes require retraining
When the two versions come from RL models whose reward definition, state or action design differs, the workflow SHALL NOT attribute that difference by re-solving. It MUST mark the factor `requires_retraining` and may only offer an experiment proposal that needs human approval and budget.

#### Scenario: Reward function changed
- **WHEN** the new `model_version` was trained with a different reward than the previous one
- **THEN** the evidence lists the reward factor as `requires_retraining` with no modeled effect, and no training job is submitted

### Requirement: Reproducible solver settings
Re-solves SHALL use the original solver, seeds, tolerances and code version recorded in each version's lineage. If those are unavailable or non-deterministic, the evidence MUST state it and report each effect with its observed run-to-run spread.

#### Scenario: Stochastic model
- **WHEN** a re-solve uses a stochastic model with multiple seeds
- **THEN** the evidence reports the mean effect and spread across the recorded seeds, and the narrative states that the attribution is uncertain
