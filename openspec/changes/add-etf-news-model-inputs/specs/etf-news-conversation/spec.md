## Purpose
Enable bounded paid ETF news collection and evidence-based conversations about eligible financial model inputs and research results.

## ADDED Requirements

### Requirement: Budget continuity
ETF collection SHALL use the existing cumulative USD 10 procurement ledger and seven-day trial expiry, reserve a quote before signing, and stop on insufficient funds, expiry or exhausted cap.

#### Scenario: A funded crypto trial changes to ETF research
- **WHEN** the operator selects the ETF news profile
- **THEN** existing paid reservations and expiry remain authoritative

### Requirement: Central evidence publication
The operator collector SHALL publish immutable sanitized ETF search metadata and payment/delivery checksums to centralized storage using authenticated ingestion. Secrets and payment proofs SHALL remain private.

#### Scenario: Publication fails after successful purchase
- **WHEN** central storage is unavailable
- **THEN** the original receipt can be republished without a replacement purchase

### Requirement: Honest model-input conversation
LangGraph SHALL retrieve ETF input eligibility, provenance and model evidence through a read-only tool, distinguishing research use, training rights and actual measured comparison outcomes.

#### Scenario: Current news has no historical corpus
- **WHEN** a user asks whether news improved historical PPO results
- **THEN** the agent reports missing historical coverage instead of backdating current information
