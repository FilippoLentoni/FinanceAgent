# explain-recommendation-change

Explain why a new recommendation differs from the previous one for the **same effective horizon**.

## Request

```json
{"explanation": {"type": "recommendation_change", "plan_version_id": "pv_new...",
                 "compare_to_plan_version_id": "pv_previous...",
                 "groups": ["data", "configuration", "constraints", "model"], "shapley": false}}
```

## What happens

1. `compare_plan_versions` returns the deterministic difference inventory (snapshot, configuration JSON pointers, constraints, `model_version`, origin). Different horizons without a defined alignment are refused with `PRECONDITION_FAILED`.
2. An empty inventory is a valid `no_effect` result; no job runs.
3. Manual-override or Excel children are explained from the inventory only. RL reward, state or action changes are `requires_retraining`: no re-solve, no training job, only an experiment proposal that needs human approval and budget.
4. Otherwise a `controlled_resolve` job (k + 2 re-solves: base reproduction, one switch per group, all switched) and, on request, an exact grouped Shapley job (2^k re-solves, at most the configured number of groups) are dry-run, budget-checked, confirmed and submitted as the caller.
5. Deterministic checks: base and all-switched reproduction, single effects, effects plus interaction remainder equal the total, Shapley efficiency.

## Output rules

- Every effect is a **modeled effect under the stated model and inputs**, never a real-world cause.
- Report infeasible switches as infeasible; never drop them.
- For stochastic models, give the spread across seeds and say the attribution is uncertain.
