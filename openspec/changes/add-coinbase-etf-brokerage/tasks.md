# Tasks
## 1. Brokerage boundary
- [x] 1.1 Implement exact authenticated read-only capability adapter and setup documentation; verify missing auth, unsupported names and writes are rejected by tests.
- [x] 1.2 Implement bounded ETF order drafts and fill reconciliation; verify finite/stale/eligibility/cash/holding validation, deterministic checksums and duplicate-fill behavior.
## 2. LangGraph integration
- [x] 2.1 Add connection-readiness conversation and versioned skill; verify setup-required evidence and unchanged live-order refusals through graph tests.
## 3. Authenticated release gates
- [x] 3.1 Obtain approved custom MCP client or supported scoped alternative, user brokerage consent, and verify actual eligible ETF products and account holdings; retain sanitized evidence.
- [ ] 3.2 Deploy the authenticated read-only brokerage target through AgentCore Gateway and verify hosted discovery, draft and reconciliation with no live order.

## Verification and external blockers

- 2026-10-14 America/New_York: 86 focused brokerage, hosted graph, crypto routing, skill and infrastructure tests passed (`pytest tests/unit/test_etf_brokerage.py tests/graph/test_etf_brokerage.py tests/graph/test_graph.py tests/graph/test_crypto_research.py tests/unit/test_explanation_skills.py tests/unit/infra/test_tool_policy_and_schemas.py -q`). The unauthenticated Coinbase endpoint returns HTTP 401 and advertises separate brokerage OAuth scopes. Existing secrets metadata identifies research-payment credentials only.
- At proposal time, 3.1 was blocked on brokerage credentials; the later authenticated verification below supersedes that initial blocker. No holdings or ETF eligibility are fabricated.
- 3.2 remains pending its full hosted discovery, draft and reconciliation scope. No ETF trade, account funding, or portfolio creation was performed.

## Authenticated read-only verification

- The separately supplied ECDSA credential was stored in the dedicated Ohio secret and used only in adapter memory. Actual key permissions verified View=true, Trade=false, Transfer=false and the configured portfolio UUID. Accounts and portfolio reads succeeded.
- Actual SPY product GET by the catalog-provided 64-character lowercase hex ID succeeded: EQUITY_PRODUCT_SUBTYPE_ETF, quote currency USDC, with catalog buy/sell/tradable flags. This establishes catalog access and ETF classification, not order execution permission.
- A bounded 64-page scan at three products per page found 95 ETF products including SPY and IWM; VOO was not found within that scan, which is not evidence that it is unavailable. Sanitized private receipts are retained under `.worktmp/etf-news/live/brokerage-{readonly-verification,etf-catalog,spy-product}.json` outside the repository.
- The coordinated readiness release subsequently verified authenticated Gateway discovery and a hosted View-only SPY status response. Task 3.2 remains open for its additional hosted order-draft and fill-reconciliation scope. No write, fund transfer, portfolio creation or live order occurred.

## Coordinated readiness Gateway

The later `complete-etf-news-and-paper-beta` change implements a separate authenticated status Gateway with owner and exact beta operator authorization. Its source tests verify narrow secret access and fixed read routes. This does not complete this older task 3.2's additional hosted order-draft and reconciliation scope; those remain local operator primitives and must not be described as deployed hosted tools.

## Deployed status verification

- 2026-10-16 America/New_York: the separately authenticated status Gateway and hosted LangGraph response verified SPY ETF metadata and View-only brokerage access as the authorized beta operator. Human-owner login was not exercised. Raw responses are retained in `.worktmp/etf-news/live/hosted-exit-brokerage.json`.
- All four beta release suites passed; `.worktmp/etf-news/release-audit.json` records restored transitions and 20 unchanged Gamma/production stacks. Draft/reconciliation primitives remain local; no live order capability was enabled.
