# recommend-portfolio

For daily investment, rebalance or supported-stock buy/sell advice, call read-only recommend_portfolio with {} unless the user supplied structured inputs or a saved portfolio_id. The service revalues saved beta paper quantities/cash at approved completed-session prices and runs the selected frozen strategy. Disclose saved paper holdings; do not assume actual brokerage positions or ask the user to repeat the saved book.

Preserve explicitly supplied holdings/snapshot/date. Collect missing weights, cash, portfolio value and high watermark rather than substituting the saved book. A previous target is not an executed holding. Report missing saved state/prices; do not initialize holdings, refresh data or create jobs automatically.

Use the reported strategy; never assume PPO. Return all assets plus cash, current/target fractional shares, signed buy/sell share/value changes, reference prices/dates, market session, portfolio identity/revision and policy/data provenance. Positive changes buy, negative sell. Shares are indicative before fees, rounding and price movement. Completed-close decisions apply to the next session. The full producer output remains in answer.recommendation.

Narrate constrained state-conditioned targets and trades deterministically. Targets are not user-fixed weights. Never invent feature attribution, news causality or calibrated critic/benchmark forecasts; disclose cash and unknown causes.

Recommendations never execute trades, change saved positions/preferences, publish/promote plans or retrain models. Asking again revalues saved holdings; acceptance is a separate explicitly confirmed paper lifecycle action. Retain/show immutable decision_id. For why, get_portfolio_decision then explain_portfolio_decision using its stored IDs and frozen evidence, even after holdings/policy changes. Use paper-portfolio-lifecycle for acceptance/rejection and historical outcomes.

Without decision_id, replay the last successful original holdings/snapshot/date/portfolio_id and query_market_data on that session. Verify policy/configuration/revision/allocations match; changed or unreadable evidence means reproduction unavailable. Never substitute another recommendation; request missing reference context.

Daily reports show the actual completed market session, decision/news cutoff and diagnostics.news_evidence consumption. Regular US equity open is 09:30 America/New_York; 09:00 collection is premarket. Closed-day reports label last-session prices. Collection/publication does not establish training eligibility or model consumption. Disclose missing/stale inputs, price-only policies and requested8 versus legacy5 incompatibility.

Use three explanation families: frozen decision rationale; actual distinct-date compare_portfolio_decisions; observed evaluate_portfolio_decision then dated research_market_events. Missing history/forward outcomes remain unavailable. Explicit backfill/training requests follow research-portfolio-models; ordinary advice authorizes no jobs. PPO requires chronological transitions and one long-history snapshot can contain them. First-seen news is never backdated.

Hosted identity/version/checksum and direct MCP descriptions expose this recipe.

GDELT numeric inputs retain protocol, UTC source_day, release/central availability and cutoff/lag. Archive organization tone is neither calibrated stock sentiment nor proof of same-morning paid-headline consumption; VOO uses Vanguard mentions.
