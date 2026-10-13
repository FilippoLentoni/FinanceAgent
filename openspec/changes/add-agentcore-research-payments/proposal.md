# Proposal

## Why

The user selected AgentCore Payments with Coinbase to obtain research inputs for portfolio planning and demonstrate deployment governance.

## What Changes

- Add resumable Coinbase Quick Create setup, explicit readiness, free Bazaar discovery and a bounded testnet-only payment execution adapter. Separate human session management from payment execution and retain evidence, approval identity and idempotent outcomes. No retail trading integration or automatic policy activation.

## Capabilities

### New Capabilities

- `research-data-payments`: Governed setup and isolation of research-data payments.

### Modified Capabilities

None. This separately authorized change implements the previously deferred payment subsystem; it does not relax current paper-runtime or broker-execution restrictions.

## Impact

AgentCore payment APIs, an isolated operator workflow and tested research adapter. Coinbase Marketplace subscription, user consent and wallet activation remain external prerequisites. Existing hosted planning runtime retains its financial-action denial until a separately reviewed hosted integration.
