# Proposal

## Why
The user authorized a total USD 10 paid crypto news/search trial, separate from the USD 100 AWS ceiling. Existing signing supports testnet only; public merchant quotes require Base mainnet USDC.

## What Changes
- Add a separate operator-run Base USDC news procurement runner with a locked USD 10 cumulative reservation ledger, two asset searches per hour and seven-day expiry.
- Bind the provider endpoint, recipient, payment challenge and request; preserve unknown outcomes and immutable delivery evidence.
- Check wallet balance before creating sessions or signing. Keep the hosted planning runtime without financial permissions.
- Keep collection and algorithm adoption separate: no data enters policy evaluation without provenance, quality and research-rights validation.

## Capabilities
### New Capabilities
- `bounded-paid-news-trial`: Operator-controlled purchase and auditable delivery of crypto research within a fixed trial budget.
### Modified Capabilities
None.

## Impact
FinanceAgent operator scripts, payment tests and research documentation. No hosted IAM changes, real trading or training jobs.
