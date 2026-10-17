# Design

## Context

See proposal.md for the user-visible gap. Existing recommendation handling uses the selected strategy and an authenticated read-only MCP target.

## Goals / Non-Goals

Serve the user-approved persisted paper portfolio automatically. Live execution, retraining, strategy promotion and broader discrepancy workers are outside this change.

## Decisions

Use the existing read-only MCP tool, with no new target or write permission. Default requests use {} so FinanceModel resolves the approved dataset and persisted paper book. Structured explicit requests retain their arguments; natural requests with explicit holdings remain provider-planned. Recognized ordinary policy questions use deterministic routing so missing-state prompts cannot block the saved-book path. Successful recommendation narration is deterministic and takes precedence over provider draft text, because a numeric claim checker cannot catch omitted cash or invented causal statements. Keep generic explanation workflows unchanged. Treat list markers as formatting rather than numerical claims. Deploy only beta through the existing pipelines after Platform and Model 1.3 producers; rollback the application releases without changing saved books.

## Risks / Trade-offs

Recommendation follow-ups use the prior successful MCP result and its original tool invocation as a non-authoritative replay reference. They re-read the exact snapshot/session and saved portfolio identity (or original explicit holdings) through MCP, verify policy/configuration and portfolio-state identity, and read the same snapshot through query_market_data. A changed saved book or selected policy is reported as a failed reproduction rather than explained as the original result. Plain why/explain prompts without a prior recommendation ask for the missing context. Daily recommendation questions still resolve the latest approved data.

The recommendation skill is published unchanged, with its version and instruction checksum, inside recommend_portfolio's remote MCP tool description. This uses the existing Gateway target and supports direct clients without adding targets, permissions or a contract release. Native MCP resource/prompt lists can remain empty. Hosted responses record which packaged skill governed deterministic routing; this is instruction provenance, not a model-generated claim of execution.

Normalize the observed AWS Gateway plain-text invalid-request schema error to nonretryable VALIDATION_FAILED in the MCP client. Preserve producer error envelopes and retain dependency errors for unrecognized service failures; do not infer validation failure from arbitrary tool prose.

- Paper positions may differ from actual holdings → label the state source and retain explicit supplied-state mode.
- Recommendations could be mistaken for executions → proposals do not write holdings and the narrative states that boundary.
- Current prices could be stale → surface the producer completed session and decision timestamp.

## Contract compatibility

The original 1.2 `tools/recommend-portfolio-request` remains explicit-only. The 1.3 release adds `tools/recommend-portfolio-invocation-request` for the same MCP tool's saved-book/default mode and explicit mode. Gateway and tool input validation use the new invocation schema; the old schema stays unchanged.
