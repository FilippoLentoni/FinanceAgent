# Traditional portfolio MCP and research lifecycle

Beta has an independent traditional-optimization MCP Gateway in addition to the existing PPO Gateway. The LangGraph runtime discovers both with the caller's bearer token. Classical tools cannot silently fall back to PPO. Both endpoints use the existing beta Cognito identity configuration. Endpoint references are `/finplan/beta/financeagent/agent/gateway-endpoint-ref` and `/finplan/beta/financeagent/agent/classical-gateway-endpoint-ref`.

Ask the hosted agent:

- “Compare PPO and traditional portfolio planning recommendations today.”
- “Give me the mean-variance portfolio recommendation today.”
- After a traditional recommendation: “Why should I sell Google?”
- “Why did the traditional recommendation change since yesterday?”
- “Are we trending green or red, and why?”
- “Review recent portfolio optimization literature and our performance feedback.”

Direct MCP clients discover the same versioned skill recipes in tool descriptions, including instruction checksums. Skills are instructions, rather than separate executable MCP endpoints. Hosted responses record selected skills and actual tool calls; `answer.portfolio_analyses` contains the numerical producer records. The hosted portfolio workflow formats and checks these records without another LLM call; the direct MCP response is the original structured evidence.

| Question | Producer tools | Evidence and limits |
|---|---|---|
| Today's traditional plan and why | `recommend_classical_portfolio`, `explain_classical_recommendation` | Minimum variance, mean-variance or scenario-CVaR; unchanged-holdings objective, constrained keep-instrument re-solves, exact trade Shapley and binding constraints. |
| What changed between plans? | `compare_classical_plans`, `list_classical_analyses`, `get_classical_analysis` | Exact four-group Shapley over expected returns, risk inputs, portfolio state and configuration. Same algorithm, universe, portfolio and horizon are required; infeasible hybrids are reported. |
| How is the plan materializing? | `evaluate_classical_performance`, `research_market_events` | Approved observed prices, immutable issued allocation, saved paper book, exposure/accounting reconciliation, and dated external context. Missing executions, fees and calibrated forecasts are explicit. |
| Research and feedback | `research_portfolio_models`, `submit_portfolio_feedback`, `run_portfolio_research` | Persisted evidence links, literature metadata, bounded sandbox benchmarking, and proposals requiring review before activation. |

The infrastructure pipeline stores approved market snapshots and the saved paper book. Existing weekday ingestion schedules retrieve completed daily market data, store raw/curated/snapshot artifacts in S3 and publish metadata in DynamoDB. Recommendation requests read these approved snapshots; they do not download live intraday prices. The Model pipeline deploys optimizer/controller code. The Tools pipeline deploys Lambda adapters. The Agent pipeline resolves same-environment manifests and tool targets, then deploys both MCP Gateways and the runtime. Beta talks to beta throughout. The expanded beta target handoff uses a validated Resolve JSON configuration artifact to remain within the CloudFormation action's parameter override limit.

Recommendations persist exact solve inputs, implementation identity, settings, holdings context, outputs and checksums in the Model-owned S3 research bucket. Explanations reproduce the issued solve; retrieval preserves the original record. A recommendation does not update holdings or select a production strategy. Historical hypothetical comparisons are labeled; the system does not invent a recommendation issued yesterday.

The optimizer defaults are configuration, rather than a claim that experimentation selected the best strategy: 60 completed returns, Ledoit-Wolf covariance, fixed cash 0, maximum instrument weight 0.6, horizon 21 sessions, risk aversion 2, CVaR alpha 0.9, and a proportional turnover proxy. Fixed cash avoids an all-cash minimum-variance solution. Historical mean/covariance and linearly horizon-scaled daily scenarios are modeling assumptions, not calibrated forecasts. The trade Shapley game uses algebraic partial-trade portfolios; infeasible hybrids are disclosed and are not executable plans. Its objective attribution differs from risk allocation or real-world causal explanation.

Green/red refers to the stated observed paper PnL threshold (zero), with separate issued-allocation and implementation-gap trends. A newly issued plan has no forward observations yet. A supplied hypothetical allocation has no observed account ledger. Planned allocation-hold returns are distinct from daily reoptimization and from actual fills. News is dated context, not proof that an event caused a price change.

The weekly beta schedule runs Monday at 09:00 America/New_York. It gathers stored feedback, performance discrepancies, prior experiment findings and literature metadata, then requests one existing sandbox CPU benchmark. The initial supported search covers three traditional families and three lookbacks with cash, buy-and-hold and equal-weight controls. Maximum runtime is 900 seconds; hard estimated cost limits are $0.50 per week, $2 per month and the existing $50 project budget. Duplicate/overlap checks, conditional week claims, CPU-category admission and project budget checks precede paid work. Literature and feedback are untrusted evidence. New code, features and algorithms remain proposals; the controller runs only supported reviewed configurations. Results feed the next review. Strategy replacement requires explicit reviewed activation and fresh forward validation.

The implementation round has a separate $5 incremental cap. Billing estimates are conservative and AWS Budgets reports can lag. Verification receipts, deployment revisions, bounded job results, isolation checks and final cost accounting are recorded separately in the beta acceptance report. Human login/client onboarding is not established by machine-principal tests.
