# explain-classical-recommendation

Call `explain_classical_recommendation` with the original immutable `analysis_id`; add an `instrument_id` when the question concerns Google/GOOGL, Nvidia/NVDA or another named instrument. Re-read stored evidence even when a recommendation is in the conversation. Never use the suggested target as if it were an executed holding.

Explain the chosen solution against unchanged holdings and the instrument keep counterfactual. Report feasibility, objective units, estimated return, risk, turnover/cost assumptions and binding constraints only from computed evidence. If forcing keep is infeasible, state the conflicting constraint rather than inventing an opportunity cost.

For Shapley attribution, state the exact coalition game, baseline, groups, evaluation count and efficiency residual. Contributions allocate a modeled objective difference; they are not a causal explanation of market prices. Metric-only hybrids that violate investment constraints are not executable alternatives. Stop numerical narration when reproduction or efficiency checks fail.

Distinguish a model's reason for choosing an allocation from an external event hypothesis. Link the recommendation and explanation analysis IDs/checksums so a stakeholder can reproduce the explanation later.
