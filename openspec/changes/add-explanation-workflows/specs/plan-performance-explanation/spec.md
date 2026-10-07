# Spec Delta

## Purpose

Explains realized (paper or simulated) performance against the exact published plan. The gap is separated into accounting reconciliation, forecast uncertainty, execution and cost gaps, and data-quality effects, each backed by deterministic evidence.

## ADDED Requirements

### Requirement: Anchored on the exact published plan
A performance explanation SHALL be requested by `publication_id` (or a `plan_id` resolved to one specific publication) and MUST use the publication's referenced `plan_version_id` and checksum. A version that was never published MUST NOT be used as the plan baseline.

#### Scenario: Explain a publication
- **WHEN** a user asks how `pub_X` performed over a stated window
- **THEN** the evidence records `pub_X`, its `plan_version_id`, the checksum read from the platform, and the associated `execution_id`s

#### Scenario: Unpublished version
- **WHEN** a user asks to compare actual results against `pv_C`, which has no publication
- **THEN** the workflow refuses with `PRECONDITION_FAILED` and lists the publications available for that plan

### Requirement: Explicit evaluation window and actual source
The workflow SHALL state the evaluation window, the realized-data `input_snapshot_id` used, and the actual source (`paper` or `simulated` execution records). Partial periods and observations that are not completed daily MUST be flagged.

#### Scenario: Window includes today's partial session
- **WHEN** the window ends on a day whose observation is `intraday_partial`
- **THEN** the evidence excludes or flags that day, and the narrative states it

### Requirement: Accounting reconciliation
The workflow SHALL reconcile the starting value, cash flows, positions, realized and unrealized P&L, and fees to the ending value for both the plan path and the executed path. Any unreconciled residual MUST be reported, and a residual above tolerance MUST fail the explanation.

#### Scenario: Reconciliation passes
- **WHEN** the deterministic reconciliation of the executed path closes within tolerance
- **THEN** the evidence contains the reconciliation table and a residual below tolerance

#### Scenario: Reconciliation fails
- **WHEN** the executed path's ending value differs from the sum of its components beyond tolerance
- **THEN** the workflow returns `VALIDATION_FAILED` naming the reconciliation and generates no narrative

### Requirement: Gap decomposition
The total gap between planned and actual outcome SHALL be decomposed into execution gap (target versus executed weights and timing), cost gap (realized versus assumed fees and slippage), market outcome versus forecast, and residual. The components MUST sum to the total within tolerance.

#### Scenario: Decomposition sums
- **WHEN** the evidence lists the four components
- **THEN** their sum equals the total gap within the declared tolerance, checked deterministically

### Requirement: Forecast uncertainty context
The workflow SHALL place the realized outcome within the plan's published forecast distribution or interval when one exists, and MUST report its percentile or interval position. When no forecast distribution was published, it MUST report `forecast_uncertainty` as `not_available` rather than invent one.

#### Scenario: Outcome inside forecast interval
- **WHEN** the realized return lies inside the published 90% interval
- **THEN** the evidence reports the position, and the narrative states the outcome was within forecast uncertainty without assigning a model error

#### Scenario: No published forecast
- **WHEN** the plan version carries no forecast distribution
- **THEN** the evidence marks forecast uncertainty `not_available`

### Requirement: Data quality effects
The workflow SHALL report data-quality flags from the plan's input snapshot and from the realized-data snapshot, plus any revisions to data the plan used. It MUST quantify revision effects only when a deterministic tool computed them.

#### Scenario: Revised input observation
- **WHEN** a price used by the plan was revised in a later snapshot
- **THEN** the evidence lists the revised observation with both snapshot IDs and its modeled effect, labeled `modeled_effect`
