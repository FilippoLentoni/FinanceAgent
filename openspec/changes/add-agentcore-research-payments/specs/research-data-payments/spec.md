## Purpose

Provide a separately governed research-data payment subsystem for portfolio planning while preserving the current paper-planning deployment and truthful integration readiness.

## ADDED Requirements

### Requirement: Resumable provider setup

The operator workflow SHALL report payment manager, connector and consent readiness without leaking credentials. It SHALL support Coinbase Quick Create and retain stable owned resource identities across retries.

#### Scenario: A connector requires user consent or a subscription

- **WHEN** a connector requires user consent or a subscription
- **THEN** the workflow SHALL retain pending status and explain the concrete prerequisite without marking setup complete

### Requirement: Bounded testnet payment execution

Payment execution SHALL use a pre-created bounded session, approved request identity, stable idempotency key and allowlisted research endpoint. This release SHALL allow only exact-amount USDC payments on Base Sepolia and reject mainnet, unbounded or malformed requests.

#### Scenario: A mainnet or over-limit challenge is received

- **WHEN** a mainnet or over-limit challenge is received
- **THEN** no payment SHALL be processed

### Requirement: Immutable research payment evidence

The workflow SHALL distinguish discovery, authorization, signing and delivered-data outcomes, retain checksummed receipts and expose unavailable results honestly. Paid current data SHALL NOT become historical training evidence.

#### Scenario: An execution times out or data delivery fails

- **WHEN** an execution times out or data delivery fails
- **THEN** the original logical payment identity SHALL remain retained and no automatic new payment SHALL be launched

### Requirement: Measured integration readiness

Offline tests SHALL verify duty separation and payment guards. Live discovery SHALL be tested independently of funded payments, and an authenticated testnet result SHALL be required before claiming an end-to-end payment integration.

#### Scenario: Only public discovery has succeeded

- **WHEN** only public discovery has succeeded
- **THEN** the paper and operator status SHALL report consent or testnet verification as pending

