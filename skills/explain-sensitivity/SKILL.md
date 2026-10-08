# explain-sensitivity

Show how the recommendation of a plan version responds to risk aversion or target, turnover limit or constraint bounds, across **all holdings**.

## Request

```json
{"explanation": {"type": "sensitivity", "plan_version_id": "pv_...",
                 "parameter": {"name": "turnover_limit", "values": [0.05, 0.1, 0.15, 0.2, 0.3]}}}
```

Adopting a computed point is a separate, confirmed step:

```json
{"explanation": {"type": "adopt_alternative", "plan_version_id": "pv_...", "sweep_run_id": "run_...", "point_index": 4}}
```

## What happens

1. The grid is validated against the configured maximum number of points, then a `sensitivity_sweep` job is dry-run, budget-checked, confirmed and submitted as the caller.
2. Each point reports the full allocation, its change versus the base for every holding, expected return and risk under the model, turnover, cost estimate and constraint slack.
3. Each point is `permitted` under the original policy (the base version's constraints) or a `concession` listing every relaxed item with its original and required value. Infeasible and no-effect points are kept.
4. Adopting a point shows its concessions and the exact plan content in the confirmation request; only then is `create_override_version` called. The stored constraints and risk preferences are never changed.

## Output rules

- Report points as computed; never interpolate and never claim a trend the points do not show.
- Present concessions as the user's decision; never recommend relaxing stored preferences.
