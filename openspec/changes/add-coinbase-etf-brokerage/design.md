## Context
Coinbase primary documentation exposes equities products and orders but warns that custom MCP clients require allowlisting, ETF availability is account-specific, previews may be unavailable, and isolated agent portfolios apply to spot crypto only. AgentCore Payments only authenticates the existing USDC research wallet.

## Goals / Non-Goals
**Goals:** Build the offline-testable brokerage boundary, exact read capability discovery, ETF drafts and reconciliation, and honest hosted connection readiness.
**Non-Goals:** Execute trades, fund or create an account, assume ETF portfolio isolation, reuse research-wallet secrets, or infer profitable recommendations.

## Decisions
Support the documented remote MCP endpoint with a caller-supplied token provider, plus a distinct own-account View-only Advanced Trade REST reader with downloaded ECDSA key JSON from Secrets Manager. The latter avoids the custom MCP client approval requirement legitimately; it does not share OAuth tokens. REST JWTs bind GET and the exact host/path, expire after two minutes, and use fresh nonces. Every account read verifies View-only permissions and the configured portfolio UUID. Responses are bounded and redirects forbidden. Calls are restricted to exact read tool names and validated against the connected tool's input schema. Preserve bounded HTTP responses, no redirects, and sanitized failures. Account/product eligibility is explicit rather than guessed from ticker text. Order drafts are local computations, not Coinbase previews. Decimal arithmetic and stable canonical checksums make drafts reviewable; actual fills drive reconciliation. Hosted readiness has no credentials and defaults to setup required. An optional injected authenticated client supports future activation without global token storage. Live writes are absent from the exposed adapter.

## Risks / Trade-offs
OAuth client approval and user brokerage consent are external blockers. Account-specific ETF eligibility and authenticated tool schemas cannot be validated without that consent. These remain incomplete tracked gates, not simulated successes. Direct REST credentials require separate scoped secret/IAM deployment and owner identity verification; a preparation helper exists but is disabled until explicitly configured. Gateway brokerage target deployment remains pending authenticated approval.
