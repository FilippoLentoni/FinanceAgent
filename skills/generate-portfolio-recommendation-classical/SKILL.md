# generate-portfolio-recommendation-classical

Use the traditional MCP for minimum variance (generate_portfolio_recommendation_min_variance) or scenario CVaR (generate_portfolio_recommendation_cvar); the tool fixes the algorithm. Mean-variance is no longer served: say so, never substitute. Required `instruments` is the exact universe optimised (weights only for these): the listed symbols, else the supplied holdings' IDs, else the saved book's instruments. Otherwise the tool reads the saved paper book and approved completed snapshot with explicit default settings. Preserve supplied snapshot/date/holdings/settings.

Report analysis_id and decision_id, algorithm/session, cash policy, all current/target shares/weights, constraints, objective assumptions and checksums. Advice never executes trades or changes holdings.

Compare PPO and classical tools with the same instruments; verify identical snapshot/date, revision and universe, and pin shared settings. Disclose differing PPO horizon, fixed cash, turnover and objective assumptions. One allocation cannot establish a winner.

For why, get_portfolio_decision then explain_portfolio_decision; without decision_id use explain_classical_recommendation with the original analysis ID. Never recover figures from assistant prose. Reports disclose completed prices, news formation time and consumption; headlines do not prove trade causality.
