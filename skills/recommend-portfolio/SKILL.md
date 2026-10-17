# recommend-portfolio

For daily investment, rebalance or supported-stock buy/sell advice, call read-only recommend_portfolio with {} unless the user supplied structured inputs or a saved portfolio_id. The service revalues saved beta paper quantities/cash at approved completed-session prices and runs the selected frozen strategy. Disclose saved paper holdings; do not assume actual brokerage positions or ask the user to repeat the saved book.

Preserve explicitly supplied holdings/snapshot/date. Collect missing weights, cash, portfolio value and high watermark rather than substituting the saved book. A previous target is not an executed holding. Report missing saved state/prices; do not initialize holdings, refresh data or create jobs automatically.

Use the reported strategy; never assume PPO. Return all assets plus cash, current/target fractional shares, signed buy/sell share/value changes, reference prices/dates, market session, portfolio identity/revision and policy/data provenance. Positive changes buy, negative sell. Shares are indicative before fees, rounding and price movement. Completed-close decisions apply to the next session. The full producer output remains in answer.recommendation.

Narrate actual constrained targets deterministically. The targets condition on model state, rather than user-fixed weights. Explain how proposed trades bridge current and target holdings; do not invent feature attribution, news causality or calibrated forecasts from a critic/benchmark. Do not omit cash or claim full investment when cash remains. Missing causes remain unknown.

Recommendations never execute trades, change saved positions/preferences, publish/promote plans or retrain models. Asking again revalues saved holdings; acceptance is a separate explicitly confirmed paper lifecycle action. Retain/show immutable decision_id. For why, get_portfolio_decision then explain_portfolio_decision using its stored IDs and frozen evidence, even after holdings/policy changes. Use paper-portfolio-lifecycle for acceptance/rejection and historical outcomes.

For legacy/hypothetical results without decision_id, replay the last successful original tool inputs and call query_market_data on the same snapshot/session. Verify policy/configuration, portfolio revision and allocations match. Saved-paper replay pins original snapshot/date/portfolio_id; supplied-state replay preserves all original holdings. Changed/unreadable evidence means reproduction unavailable; never silently explain a new recommendation. With no successful reference, request context.

Daily reports show the actual completed market session, decision/news cutoff and diagnostics.news_evidence consumption. Regular US equity open is 09:30 America/New_York; 09:00 collection is premarket. Closed-day reports label last-session prices. Collection/publication does not establish training eligibility or model consumption. Disclose missing/stale inputs, price-only policies and requested8 versus legacy5 incompatibility.

Use three explanation families: frozen decision rationale; actual distinct-date compare_portfolio_decisions; observed evaluate_portfolio_decision then dated research_market_events. Missing history/forward outcomes remain unavailable. Explicit backfill/training requests follow research-portfolio-models; ordinary advice authorizes no jobs. PPO requires chronological transitions and one long-history snapshot can contain them. First-seen news is never backdated.

Hosted skill identity/version/checksum and direct MCP tool descriptions expose this same recipe; native prompt/resource lists need not contain it.
