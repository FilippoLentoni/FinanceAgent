# Horizon investigations and recursive research

The beta LangGraph agent reaches numerical analysis and research through the independent remote MCP Gateways. New research requests are handled by the deployed graph's evidence-first improvement path; no separate always-on model endpoint is needed for orchestration.

Ask `Evaluate the PPO strategy horizon for <decision_id>` to retrieve the frozen decision and its persisted evaluation. Ask `Deep dive into the daily loss and news for PPO <decision_id>` to fetch numerical evidence before optional dated market context. The response separates daily paper accounting from `horizon_evaluation`: declared objective, contract provenance, available forward sessions, mature/partial windows, frozen sequential-policy replay, same-cost controls and evidence assessment. Older decisions expose a retrospective protocol. Replay and controls do not establish optimality on one realized market path. Missing policy artifacts or accounting remain explicit limitations.

Ask `Review recursive improvement of my PPO strategy` to create a bounded persisted cycle. Ask `Resume recursive improvement <cycle_id>` to read its current job/results and lineage. Ask `Run the next experiment in this cycle` after reviewing an eligible estimate to obtain the exact confirmation prompt. Declining starts no job. Approval is independently checked by the MCP using the authenticated researcher's transport token and producer budget controls.

Named requests such as `Review the Qwen agent swarm benchmark` or `Review the TypeSafe Jev benchmark` inspect the producer's actual configuration and readiness. A complete matching proposal is validated and passed to `submit_experiment` with dry_run=true for a concrete cost estimate, without recording a run. A subsequent explicit launch request presents the exact benchmark arguments for confirmation; GPU/vendor approvals remain independent prerequisites. Unconfigured compute, checkpoints or credentials are surfaced. They are not replaced by a different model or reported as successful experiments.

Direct MCP clients can use the same packaged skills published through tools/list:

```json
{"name":"run_recursive_improvement","arguments":{"query":"Review PPO horizon evidence","dry_run":true}}
```

```json
{"name":"run_recursive_improvement","arguments":{"cycle_id":"<issued ca_ identifier>","dry_run":true}}
```

CPU recursive research is capped at one job per week, USD 0.50 per week, USD 2 per month and three iterations across weeks. Interactive paid launches require confirmation; the configured weekly scheduler can resume eligible CPU cycles under its standing authorization and the same caps. GPU/Jev benchmark experiments retain the existing sandbox's separate estimate, authorization and approval controls, and the scheduler cannot create or approve them. Returning an existing running/stopped/completed cycle reads its immutable evidence without attempting another paid call. Changes to serving strategies remain proposals requiring review. These tools never update holdings or execute broker orders.

Each cycle/iteration is an immutable model analysis retrievable with `get_classical_analysis`. It links source evidence, configuration, job/result and proposed change records. Hosted turns also retain tool results and exact skill versions/checksums through the existing activity archive. The initial user paper book is preserved during verification.
