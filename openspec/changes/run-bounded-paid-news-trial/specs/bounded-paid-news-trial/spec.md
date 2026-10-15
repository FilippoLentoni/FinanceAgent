## Purpose
Provide operator-controlled crypto news procurement with a fixed cumulative spending limit and immutable payment and delivery evidence.

## ADDED Requirements

### Requirement: Cumulative trial budget
The runner SHALL reserve quoted USDC amounts before signing, stop before cumulative reservations exceed USD 10, expire seven days after activation and permit only one search per asset per hourly slot.

#### Scenario: A payment or delivery result is unknown
- **WHEN** signing or delivery is interrupted
- **THEN** the reservation remains charged against the cap and the same slot is not automatically retried

### Requirement: Narrow financial authority
The runner SHALL purchase only the reviewed news endpoint using exact Base USDC payments up to USD 0.03 per request, with a funded active wallet and a bounded session. Hosted chat SHALL retain its existing financial permission denials.

#### Scenario: The wallet has no usable balance
- **WHEN** balance preflight fails
- **THEN** no payment session or signing request is created

### Requirement: Honest evidence adoption
The runner SHALL retain challenge, request, payment and response checksums with actual retrieval timestamps. Delivery SHALL not imply settlement verification, research rights, historical availability or improved policy performance.

#### Scenario: Purchased content has not passed research validation
- **WHEN** research quality or rights are unverified
- **THEN** the content remains quarantined and is not automatically used to train or alter a policy
