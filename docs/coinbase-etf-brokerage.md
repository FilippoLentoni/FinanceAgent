# Coinbase ETF brokerage integration

## Implemented boundary

The Coinbase brokerage adapter is separate from AgentCore Payments. It uses the fixed `https://agents.coinbase.com/mcp` endpoint, per-request OAuth token provider, exact capability names returned by `tools/list`, and each discovered input schema. Read operations cover portfolios, balances, products, orders and fills. Local ETF drafts validate independently verified account-specific eligibility, freshness, cash, holdings and notional limits. Reconciliation deduplicates immutable fill identifiers and accounts for actual fees. No authenticated brokerage data or trade has been tested against a live account.

Hosted LangGraph answers “What is my Coinbase brokerage connection status?” with setup-required evidence by default. The runtime's optional `AgentContext.brokerage` accepts an independently authenticated adapter for approved deployments. The readiness check is local runtime evidence, not a deployed Gateway target. Existing live-action refusal policy remains active.

## Concrete onboarding gates

1. Sign in to Coinbase and confirm stocks/ETFs are available for your account. Choose the research ETFs, total investment ceiling, per-order ceiling and which account may be accessed. Do not fund the research-payment wallet for ETF investments.
2. For remote MCP, obtain approval for the deployed custom LangGraph/AgentCore OAuth client. Coinbase supports approved clients; dynamic registration alone does not grant access. A connection established in the Codex or ChatGPT client cannot simply be copied into AgentCore.
3. A supported alternative is Coinbase's official CLI (`@coinbase/coinbase-cli`, Node 22+). Configure a separately scoped Coinbase App/Advanced Trade credential with the official CLI flow. Do not reuse the AgentCore Payments CDP key or send key files in chat. Scope and eligibility must be verified specifically for ETFs; spot-crypto portfolio isolation cannot be assumed.
4. An operator stores an approved OAuth access token in a dedicated Secrets Manager secret and sets `FINPLAN_COINBASE_BROKERAGE_TOKEN_SECRET_ARN` only for the read-only probe. The script reads it in memory, does not print it, and only lists capability names. Secret value must be JSON `{"access_token":"..."}`. This is a short-lived diagnostic path, not refresh-token lifecycle implementation.
5. Run `.venv/bin/python scripts/coinbase_etf_brokerage.py status`. Without configuration it prints setup-required; with valid consent it lists read capabilities. Then an operator uses `CoinbaseBrokerage.read` with exact schema-validated arguments to verify actual account holdings and chosen ETF product IDs, retaining sanitized eligibility evidence.
6. Deploy a dedicated read-only Gateway target and token refresh/IAM configuration after authentication is verified. Keep creation/submission/cancellation off the general research gateway. Hosted discovery and draft verification are explicit remaining spec tasks.

## Future execution boundary

The adapter includes disabled-by-default submit/cancel methods for later activation. Submit requires explicit operator-created authorization bound to the draft/account, expiry, ceiling and durable idempotency journal. It validates the connected schema and accepts only its narrowly supported share-sized limit payload shape; incompatible equity schemas fail closed. Ambiguous submissions are journaled as unknown and never resubmitted automatically. Cancellation requires explicit allowed order identifiers. Neither method is exposed to current LangGraph tools. This is not sufficient for production activation: fresh cash/holdings checks, broker-side account scoping, market-session validation, fee/slippage limits and operational kill controls must be verified before a separately authorized activation.

## Primary sources

- https://docs.cdp.coinbase.com/ai-agents/coinbase-for-agents/coinbase-mcp
- https://github.com/coinbase/agents
- https://help.coinbase.com/en/trading-and-funding/buy-sell-stocks-funds

## Observed connection evidence

The unauthenticated initialize probe on October 14, 2026 (America/New_York) returned HTTP 401, with OAuth resource metadata at `https://agents.coinbase.com/.well-known/oauth-protected-resource`. Metadata identifies `https://login.coinbase.com/` as issuer and advertises S256 PKCE. For the first read-only deployment request only `mcp:portfolios:read mcp:accounts:read mcp:orders:read mcp:trades:read mcp:products:read`. Omit every update/create/delete/transfer scope. Public DCR metadata is not approval for this custom client.

A metadata-only Secrets Manager scan in Ohio found the two existing AgentCore payment/CDP secrets and no independently named brokerage token. No secret values were retrieved. The default operator status probe returned `setup_required`. These findings establish the external-auth blocker, not successful brokerage connection.
