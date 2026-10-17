# Proposal

## Why
The research wallet can pay for data but cannot manage Coinbase brokerage ETF positions. The LangGraph agent needs a distinct authenticated brokerage boundary, verified product eligibility, and reviewable order planning before future execution.

## What Changes
- Add independently authenticated read-only Coinbase MCP and Advanced Trade REST adapters with capability and permission discovery.
- Add deterministic ETF-only order drafts, portfolio constraints, and fill reconciliation; reject execution by default.
- Add LangGraph connection-readiness conversation and documented setup requirements without inventing holdings.
- Record authenticated deployment and ETF eligibility as explicit external gates; no orders, account funding, or brokerage account creation is authorized by this change.

## Capabilities
### New Capabilities
- `etf-brokerage-connection`: Read-only brokerage discovery, ETF order preparation and truthful connection readiness.
### Modified Capabilities
None. Existing live financial action prohibitions remain effective.

## Impact
FinanceAgent brokerage modules, graph, versioned skill, operator setup documentation, and tests. Existing AgentCore Payments identity and wallet are unchanged. Coinbase custom OAuth client approval or independently scoped supported credentials are required for an authenticated deployment.
