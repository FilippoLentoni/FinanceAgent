# Design

## Context

See proposal.md for the user-visible gap. Existing recommendation handling uses the selected strategy and an authenticated read-only MCP target.

## Goals / Non-Goals

Serve the user-approved persisted paper portfolio automatically. Live execution, retraining, strategy promotion and broader discrepancy workers are outside this change.

## Decisions

Use the existing read-only MCP tool, with no new target or write permission. Default requests use {} so FinanceModel resolves the approved dataset and persisted paper book. Structured explicit requests retain their arguments; natural requests with explicit holdings remain provider-planned. Recognized ordinary policy questions use deterministic routing so missing-state prompts cannot block the saved-book path. Successful recommendation narration is deterministic and takes precedence over provider draft text, because a numeric claim checker cannot catch omitted cash or invented causal statements. Keep generic explanation workflows unchanged. Treat list markers as formatting rather than numerical claims. Deploy only beta through the existing pipelines after Platform and Model 1.3 producers; rollback the application releases without changing saved books.

## Risks / Trade-offs

- Paper positions may differ from actual holdings → label the state source and retain explicit supplied-state mode.
- Recommendations could be mistaken for executions → proposals do not write holdings and the narrative states that boundary.
- Current prices could be stale → surface the producer completed session and decision timestamp.

## Contract compatibility

The original 1.2 `tools/recommend-portfolio-request` remains explicit-only. The 1.3 release adds `tools/recommend-portfolio-invocation-request` for the same MCP tool's saved-book/default mode and explicit mode. Gateway and tool input validation use the new invocation schema; the old schema stays unchanged.
