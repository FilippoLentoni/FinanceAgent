## Purpose
Prepare eligible ETF brokerage portfolio integration through an authenticated Coinbase boundary while distinguishing research payments and future execution authorization.

## ADDED Requirements
### Requirement: Authenticated read-only discovery
The integration SHALL discover current account, portfolio, product and order-read capabilities using independently authenticated Coinbase brokerage access. Research-wallet credentials MUST NOT substitute for brokerage authorization.
#### Scenario: Credentials unavailable
- **WHEN** no independently authenticated brokerage client is configured
- **THEN** readiness reports setup required and does not fabricate account holdings or eligibility.
#### Scenario: Unsupported capability
- **WHEN** the connected server does not advertise an exact permitted read tool
- **THEN** the adapter rejects the call without guessing another tool or issuing a write.

### Requirement: ETF-only deterministic order preparation
Order drafts SHALL bind an explicit account, verified ETF product, reference timestamp, side, quantity, reference price and notional ceiling. Drafts MUST reject nonfinite inputs, stale references, insufficient cash or holdings, and non-ETF products. A draft SHALL NOT be described as an executable quote or placed order.
#### Scenario: Ineligible product
- **WHEN** product eligibility or ETF classification has not been independently verified
- **THEN** preparation fails without any order submission.
#### Scenario: Valid draft
- **WHEN** authenticated ETF eligibility and bounded current state are supplied
- **THEN** a deterministic draft and checksum are returned with live execution disabled.

### Requirement: Reconcile confirmed executions only
Reconciliation SHALL use immutable fill identifiers and actual fill quantities, prices and fees, reject conflicting duplicates and oversells, and distinguish open orders from completed holdings changes.
#### Scenario: Repeated fill
- **WHEN** the same execution fill appears twice
- **THEN** it changes holdings and cash only once.

### Requirement: Preserve live execution boundary
LangGraph SHALL describe brokerage setup status and prerequisites without weakening existing live-action refusals. Buy, sell, cancel, funding and portfolio-creation operations SHALL remain disabled pending a separately authorized execution activation.
#### Scenario: Live trade requested
- **WHEN** a user asks the current agent to execute a live ETF order
- **THEN** it refuses and sends no brokerage write.
