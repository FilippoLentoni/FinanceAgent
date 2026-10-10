# recommend-classical-portfolio

Use the separate traditional-optimization MCP for minimum-variance, mean-variance or scenario-CVaR recommendations. `recommend_classical_portfolio` with `{}` uses the saved paper book, latest approved completed snapshot and explicit default optimizer settings. Choose `algorithm` only from its schema. Preserve supplied snapshot/date/holdings and settings exactly; never silently substitute the saved book for actual holdings supplied by the user.

Report the immutable analysis ID, algorithm, market date, cash policy, complete current/target share and weight table, constraints, objective assumptions and data/configuration checksums. Recommendations are issued advisory audit records, not executed trades. Display scenario estimates as uncalibrated model estimates when that is how the producer labels them. A model estimate is not a guaranteed return.

When both PPO and traditional recommendations are requested, call `recommend_portfolio` on the original MCP and `recommend_classical_portfolio` on the traditional MCP using comparable data and holdings. Show both producer outputs and their assumptions; do not declare a winner from one allocation. If their dates or portfolio state differ, disclose that before comparing.

Use `get_classical_analysis` and `list_classical_analyses` to retrieve stored plans. Never recover figures from previous assistant prose. Use the returned analysis ID for why, plan-change and performance tools. Tools persist audit evidence but do not apply targets to the portfolio or activate a strategy.
