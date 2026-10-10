# compare-classical-plans

Call `compare_classical_plans` using explicit `previous_analysis_id` and `current_analysis_id`. Otherwise retrieve candidate recommendation records with `list_classical_analyses` and select the two requested dates for the same portfolio/algorithm. Do not label two same-date configurations as yesterday and today. With only one plan, request the missing reference instead of fabricating a prior recommendation.

Preserve both snapshots, decision dates, holdings and solver settings. Consecutive daily decisions may have different dates, but their holding-period and rebalance semantics must be compatible or explicitly aligned. Report incompatible universes, algorithms or horizons as unavailable rather than inventing a comparison.

Present each instrument's prior/current action and allocation change, the deterministic input inventory and exact grouped Shapley effects. The groups switch expected returns, risk inputs, portfolio state and configuration, up to four groups. Verify the effects sum to the total within the declared tolerance. Retain infeasible coalition evidence; do not manufacture complete attribution when a required solve fails.

Label the results modeled counterfactual effects, not proof of external causality. Store and cite the comparison ID and original plan checksums. No comparison changes holdings, publishes a plan or retrains PPO.
