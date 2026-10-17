# Spec Delta

## Purpose
Make ETF research readiness observable through deployed collection, model-input, paper-planning and authenticated brokerage evidence.

## ADDED Requirements

### Requirement: Verified beta release
The system SHALL report deployed readiness only after dependency-ordered beta deployments, tool discovery and hosted conversation checks succeed. Gamma and production SHALL remain unchanged.

#### Scenario: A build succeeds but deployment is held
- **WHEN** a component has passed its build but has not deployed to beta
- **THEN** readiness identifies the component as pending instead of complete

### Requirement: Bounded paid collection and publication
The collector SHALL publish completed receipts idempotently, preserve original availability, and collect only during regular US market sessions under the original trial expiry, cumulative $10 ceiling and per-call cap.

#### Scenario: Publication is retried
- **WHEN** an existing paid receipt is republished
- **THEN** no new purchase occurs and the immutable central record and availability remain unchanged

#### Scenario: Trial expires or market closes
- **WHEN** the trial expires, funding is insufficient, the cap is exhausted or the market session is closed
- **THEN** no paid request is executed

### Requirement: Evidence-backed model input readiness
The hosted agent SHALL expose actual news provenance, missingness and per-model eligibility. It SHALL disclose insufficient historical coverage or training rights and SHALL NOT describe input sensitivity as measured investment-performance improvement.

#### Scenario: Research-only news is supplied
- **WHEN** purchased metadata has research-only rights
- **THEN** optimization and LLM research context can be evaluated while PPO training remains ineligible

### Requirement: Isolated paper portfolio evidence
The agent SHALL distinguish a separate $100 simulated ETF/stock portfolio from existing paper holdings, research-payment funds and live Coinbase capital. Matched experiments SHALL preserve prices, holdings, costs, constraints and decision cutoffs.

#### Scenario: Matched historical news is unavailable
- **WHEN** the news corpus does not cover historical decision times
- **THEN** the system records the experiment as ineligible rather than backdating new headlines or fabricating performance

### Requirement: Authenticated read-only brokerage
The hosted agent SHALL retrieve bounded brokerage-readiness and eligible ETF metadata through an authenticated Gateway boundary without exposing credentials or enabling brokerage writes.

#### Scenario: A caller lacks owner authorization
- **WHEN** an unauthorized caller requests the private brokerage connection
- **THEN** access is denied without returning account data

### Requirement: Measured publication evidence
The conference paper SHALL identify deployed versions, verified experiments and remaining eligibility limitations. It SHALL distinguish catalog access, paper recommendations and live order execution.

#### Scenario: A paper recommendation succeeds
- **WHEN** a policy returns a recommendation without submitting an order
- **THEN** the paper records advisory operation and zero live executions

### Requirement: Pipeline to recommendation exit criterion
Completion SHALL require purchased news retrieval, central persistence, authenticated hosted LangGraph payload handling and recommendations from PPO and the supported portfolio optimizers that actually consume the new features. Build success, adapter availability and readiness responses alone SHALL NOT satisfy completion.

#### Scenario: Verify actual model consumption
- **WHEN** the deployed agent receives a paper recommendation payload
- **THEN** PPO and optimizer outputs include input evidence identifying the news, decision cutoff, feature protocol, model configuration and compatible policy artifact

#### Scenario: Detect a legacy policy bypass
- **WHEN** an old actor ignores the news or has an incompatible observation space
- **THEN** the exit criterion fails even if the agent returns an otherwise valid recommendation

### Requirement: Iterate until verified or explicitly blocked
The implementation workflow SHALL repair code, data and deployment failures and rerun the exit checks until every criterion passes. Missing rights, compatible trained artifacts, funding or historical coverage SHALL remain explicit unmet criteria; the workflow SHALL NOT weaken the criteria or exceed authorized limits.

#### Scenario: Training cannot yet proceed
- **WHEN** PPO has no eligible training corpus or compatible trained artifact
- **THEN** the task remains open and reports the exact prerequisite instead of declaring the specification implemented
