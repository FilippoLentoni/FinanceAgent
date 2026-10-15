# Tasks
## 1. Brokerage boundary
- [x] 1.1 Implement exact authenticated read-only capability adapter and setup documentation; verify missing auth, unsupported names and writes are rejected by tests.
- [x] 1.2 Implement bounded ETF order drafts and fill reconciliation; verify finite/stale/eligibility/cash/holding validation, deterministic checksums and duplicate-fill behavior.
## 2. LangGraph integration
- [x] 2.1 Add connection-readiness conversation and versioned skill; verify setup-required evidence and unchanged live-order refusals through graph tests.
## 3. Authenticated release gates
- [ ] 3.1 Obtain approved custom MCP client or supported scoped alternative, user brokerage consent, and verify actual eligible ETF products and account holdings; retain sanitized evidence.
- [ ] 3.2 Deploy the authenticated read-only brokerage target through AgentCore Gateway and verify hosted discovery, draft and reconciliation with no live order.

## Verification and external blockers

- 2026-10-14 America/New_York: 86 focused brokerage, hosted graph, crypto routing, skill and infrastructure tests passed (`pytest tests/unit/test_etf_brokerage.py tests/graph/test_etf_brokerage.py tests/graph/test_graph.py tests/graph/test_crypto_research.py tests/unit/test_explanation_skills.py tests/unit/infra/test_tool_policy_and_schemas.py -q`). The unauthenticated Coinbase endpoint returns HTTP 401 and advertises separate brokerage OAuth scopes. Existing secrets metadata identifies research-payment credentials only.
- 3.1 remains blocked on approved custom-client credentials and user brokerage consent; no live authenticated holdings or ETF eligibility are fabricated.
- 3.2 remains pending that authentication and a dedicated Gateway target release. No ETF trade, account funding, or portfolio creation was performed.
