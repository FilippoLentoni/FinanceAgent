## Decisions

The deterministic LangGraph planner keeps financial calculations in MCP producers. The existing evaluate_portfolio_decision tool returns horizon_evaluation alongside daily accounting. The renderer separates daily outcome from objective-horizon evidence, preserves missing replay status and avoids declaring optimality from one path. Market-event research follows numerical attribution only when requested and remains dated context.

run_recursive_improvement is served through the independent research/traditional Gateway and routed by a focused agent skill. Read-only requests default to dry_run=true. Launches require a verified researcher identity, visible cost estimate, explicit confirmation and an idempotency key. Cycles are persisted and bounded to the producer's iteration, weekly and monthly caps; returned lineage is available through immutable analysis retrieval. Activation remains a reviewed proposal. Benchmark discovery uses describe_capabilities and experiments use the existing authorized submit_experiment interface.

## Validation

Test natural and structured requests, partial horizons, objective/replay evidence preservation, optional news ordering, read-only cycle creation/resume, paid confirmation, unavailable benchmark reporting and numerical claim checks. Deployment and live acceptance evidence will be recorded by the integration owner.
