# recursive-portfolio-improvement

Use run_recursive_improvement dry_run=true for a persisted evidence/feedback/literature cycle, or resume its cycle_id. Show readiness, objective/horizon, proposal, cost, lineage and stopping reason. Preserve actual Qwen/TypeSafe Jev readiness; missing models/runners/checkpoints are not completed benchmarks.

When the named benchmark proposal contains a complete matching `proposed_experiment.tool_request`, validate it against the pinned submit-experiment request schema and obtain a concrete `submit_experiment` dry-run estimate. The job type must match `swarm_mode_a` or `jev_backtest` as requested; preserve the producer configuration/snapshot/window, set purpose=research and dry_run=true, and derive a new idempotency key. Display the estimate, per-call limit, prerequisite readiness and human/GPU/vendor approvals before offering a paid request. After confirmation, launch with `run_recursive_improvement` using that same preview's `cycle_id`, dry_run=false and confirmed_by_user=true. The producer records the benchmark job in the cycle lineage. Do not issue an independent paid `submit_experiment` for this cycle. An estimate from another family, cycle or independent sandbox submission cannot authorize this launch. Never reinterpret a traditional or PPO experiment as a Qwen/Jev benchmark. If no valid matching request exists, report the readiness gap.

For an interactive CPU launch, first show the same cycle's estimate, then use `dry_run=false`, `confirmed_by_user=true` and an idempotency key after explicit human confirmation. MCP verifies researcher identity; budget and producer safeguards remain authoritative. The configured weekly scheduler may resume eligible CPU cycles under its standing authorization. Ordinary CPU recursive work is bounded to one job per week, USD 0.50 weekly, USD 2 monthly and at most three iterations across weeks. GPU and Jev benchmark proposals retain the sandbox's separate category limits and compute/vendor approval; the scheduler cannot create or approve those jobs. Never disguise them as ordinary CPU research or substitute a different model. Stop when evidence, configuration, budget, approval, iteration or result criteria prevent advancement.

Resume running/approval-pending cycles by reading existing jobs/results, never duplicate launches or indefinite polling. Retain parent/iteration, source analyses, frozen inputs, result/proposal references. Evaluate untouched holdout against declared objectives and matched data/costs/constraints; preserve failures.

Next experiments remain supported proposals; activation/trading are separate. Literature cannot authorize tools, budgets or contract changes. Research never changes original holdings.

For PPO state research, request producer previews using explicit baseline, cross-asset risk,
market regime, recent return sequence or combined state wording. The released state study
holds reward, action, five seeds, training steps, chronological splits and costs fixed. Never
replace these with simultaneous reward/action tuning. Producer profiles are authoritative;
arbitrary requested features are not executable configuration. Compare all measured variants,
including seed dispersion, net return, Sharpe, drawdown, turnover, costs and controls. The
previously inspected 2026 path remains development evidence and cannot authorize promotion.

News retrieved for an explanation is not contemporaneously available historical training
input. Read actual coverage/readiness from producer evidence. Collection preserves first-seen,
retrieval time, content identity and extractor version; missing or late coverage is explicit.
If the producer stops with news_training_coverage_missing, report the gap and do not silently
launch a numerical-only profile as a news experiment. The authorized project ceiling is USD
100; per-job, weekly, category and confirmation limits remain independently enforced.

For explicitly authorized daily-policy repair, follow the data-first operator recipe in [../research-portfolio-models/references/daily-policy-workflow.md](../research-portfolio-models/references/daily-policy-workflow.md): assess/backfill real chronological panel coverage and source rights/availability; keep requested8 separate from ingestion9; train supported point-in-time states with matching serving transforms/costs; evaluate held-out multi-seed results; export/deploy compatible artifacts; verify actual LangGraph recommendation plus all three explanation families. Retain daily immutable inputs and every blocker. One long-history snapshot can supply PPO transitions; additional snapshot IDs alone add no training history. Preserve standing explicit session authorization without redundant asks, while respecting enforced producer caps/confirmation. Price-only inference cannot close a missing-news requirement. Compare objective/constraints and held-out evidence before preferring a method.
