# explain-performance

Explain how a **published** plan performed against paper or simulated execution over a stated window.

## When to use

The user asks how a publication (`pub_...`), or the current publication of a plan (`pl_...`), performed, or why realized results differ from the plan. A plan version that was never published is not a baseline: the agent refuses with `PRECONDITION_FAILED` and lists the available publications.

## Request

```json
{"explanation": {"type": "plan_performance", "publication_id": "pub_...",
                 "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
                 "realized_snapshot_id": "snap_...", "actual_source": "paper"}}
```

## What happens

1. The publication is read, its `plan_version_id` and checksum are checked against the plan version, and its paper or simulated executions are listed.
2. A `performance_decomposition` evidence job is dry-run, budget-checked against `cpu_research`, shown for confirmation, then submitted as the caller.
3. Deterministic checks: both paths reconcile (start + flows + P&L - fees = end), the four gap components (execution, cost, market versus forecast, residual) sum to the total, and the forecast position is consistent. A failure returns `VALIDATION_FAILED` and no narrative.
4. The narrative cites every figure with `[evN]`.

## Output rules

- Figures come only from the evidence statements; never compute or round.
- An outcome inside the published forecast interval is "within forecast uncertainty", not a model error. With no published forecast, say `not_available`.
- State every `intraday_partial` day that was excluded.
- Data revisions are modeled effects only when a tool quantified them.
