# generate-portfolio-recommendation-ppo

For daily investment, rebalance or buy/sell advice, call read-only generate_portfolio_recommendation_ppo. Required `instruments`: the symbols the user listed, else the supplied holdings' IDs, else the saved paper book's instruments; never invent a universe. Add portfolio_id or snapshot/date/holdings only when supplied; otherwise the service revalues saved beta paper quantities/cash at approved completed-session prices. Only a frozen PPO policy trained on exactly that universe is served; report a no-policy error, never substitute a universe or model. Disclose saved paper holdings; do not assume brokerage positions.

Preserve supplied holdings/snapshot/date. Collect missing weights, cash, portfolio value and high watermark rather than substituting the saved book. A previous target is not an executed holding. Report missing saved state/prices; never initialize holdings, refresh data or create jobs.

Use the reported strategy. Return all assets plus cash, current/target fractional shares, signed share/value changes, reference prices/dates, session, portfolio identity/revision and policy/data provenance. Shares are indicative before fees, rounding and price movement; completed-close decisions apply to the next session. The full producer output is answer.recommendation.

Narrate constrained state-conditioned targets deterministically. Never invent feature attribution, news causality or calibrated forecasts; disclose cash and unknown causes.

Recommendations never execute trades, change saved positions, publish plans or retrain models. Acceptance is a separate confirmed paper lifecycle action. Show decision_id; for why, get_portfolio_decision then explain_portfolio_decision on its frozen evidence. Without decision_id, replay the original holdings/snapshot/date/portfolio_id/instruments and query_market_data on that session; changed or unreadable evidence means reproduction unavailable.

Reports show the completed session, decision/news cutoff and diagnostics.news_evidence consumption. US equities open 09:30 America/New_York. Collection does not establish training eligibility or model consumption. Explanation families: frozen decision rationale; compare_portfolio_decisions across distinct dates or news formations; evaluate_portfolio_decision then dated research_market_events. Backfill/training follows research-portfolio-models. First-seen news is never backdated. GDELT tone is not calibrated sentiment.
