# FinanceAgent invocation API (task 2.6)

The hosted agent implements the AgentCore Runtime HTTP protocol (`POST /invocations`, `GET /ping`).
Clients call it through the Runtime's OAuth endpoint with a bearer token from the SAME environment's
Cognito pool (`/finplan/<env>/financeagent/agent/authorizer-metadata-ref`) and the session header
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` (33-100 characters of `[A-Za-z0-9_-]`).

## Request body

| Field | Used by | Meaning |
|---|---|---|
| `action` | all | `invoke` (default), `confirm`, `describe`, `delete_session` |
| `prompt` | invoke | the user message (at most 20,000 characters) |
| `tool_request` | invoke | optional structured request `{"name": "<tool>", "arguments": {...}}` |
| `recommendation` | invoke | selected-strategy request with an approved snapshot, completed session and current portfolio state; returns `answer.recommendation` |
| `explanation` | invoke | optional structured explanation request (workflows of `add-explanation-workflows`; see `docs/explanations.md`); the answer carries `answer.explanation` (a contract explanation result) and `prompt` becomes optional |
| `approve` | confirm | `true` runs the pending state-changing call(s) once; `false` declines |
| `stream` | all | `true` (default): SSE events; `false`: one JSON document (the `final` payload) |
| `correlation_id` | all | optional; minted when absent |

## `describe`

```json
{"type": "describe", "framework": "langgraph", "graph_version": 1, "environment": "beta",
 "release_id": "rel_...", "contract_version": "1.2.0",
 "provider": {"kind": "bedrock", "model_id": "<from SSM>", "max_tokens_invocation": 1024,
              "prompt_caching": "disabled", "rates_source": "aws_price_list", "rates_retrieved_at": "..."},
 "skills": [...], "streaming": {"http": true, "websocket": false}}
```

## Stream events (in order)

1. `{"type": "progress", "stage": "accepted", "session_id", "correlation_id"}`, then `progress` per
   graph node (`route`, `plan`, `tool_call`, `narrate`, `claim_check`);
2. `{"type": "tool_call", "tool", "arguments", "id"}` and `{"type": "tool_result_summary", "tool",
   "id", "ok", "summary", "error_code"}` per tool call;
3. `{"type": "token", "text"}` narrative deltas (drafts: the `final` narrative is authoritative
   after the deterministic claim check);
4. exactly one `final`, always last.

## `final`

```json
{"type": "final", "session_id": "...", "correlation_id": "...",
 "status": "completed | in_progress | awaiting_confirmation | refused | failed",
 "answer": {"narrative": "...",
            "narrative_status": "generated | not_needed | unavailable | budget_exceeded | refused | pending_confirmation",
            "evidence": [{"id", "tool", "ok", "summary", "error"}],
            "claim_check": {"passed": true, "checked_figures": 3, "removed_figures": []}},
 "confirmation": {"type": "confirmation_required", "calls": [{"tool", "arguments", "idempotency_key"}]} ,
 "in_progress": {"run_id": "run_...", "state": "running", "tool": "get_job_status"},
 "declined": [], "error": {<contract error envelope>} , "degraded": "tool_only | tools_unavailable | null",
 "usage": {"provider_kind", "model_id", "input_tokens", "output_tokens", "cache_read_tokens",
           "cache_write_tokens", "estimated_cost_usd", "tool_calls", "invocations"},
 "graph_version": 1, "release_id": "rel_..."}
```

A streamed and a non-streamed run of the same prompt in fresh sessions return identical `final`
payloads apart from `session_id` and `correlation_id`.

## Errors

Every error is a contract envelope (`core/v1/error.json`): `UNAUTHORIZED` (no or invalid bearer
token), `FORBIDDEN` (another caller's session), `VALIDATION_FAILED` (bad body, with
`details.pointer`), `PRECONDITION_FAILED` (confirm without a pending confirmation, or a new prompt
while one is pending), `OPERATION_NOT_PERMITTED` (live trading, paid-job approval, budget raise,
risk-preference change), `BUDGET_EXCEEDED` (token, tool-call, session or `bedrock_explanations`
limits; details name the limit, remaining amount and estimate), `DEPENDENCY_UNAVAILABLE` (Gateway,
tool release or Bedrock model access), `RATE_LIMITED`, `INTERNAL` (never with a stack trace).

## Selected strategy recommendation

Send `recommendation` with `input_snapshot_id`, completed-session `as_of` and `holdings`
(`weights` of `{instrument_id, weight}`, `cash_weight`, `portfolio_value`, `high_watermark`).
An optional `prompt` asks for an explanation in natural language. The graph uses the read-only
`recommend_portfolio` tool and returns its complete result in `answer.recommendation` even when
the narrative is unavailable. Missing or unauthorized inputs produce tool errors without invented
weights or a training job. Plain-language requests can ask the hosted provider to collect these
inputs; the model must not assume cash holdings or a high watermark.

Hosted beta uses guarded Bedrock; offline CI retains fixtures/stubs with no network model calls.
The beta Gateway client waits up to 330 seconds. User authorization is enforced by the Gateway;
the Lambda target currently audits a Gateway caller identity, as documented in `docs/gateway.md`.

See `docs/strategy-lifecycle.md` for the four pipelines, beta-to-beta references, artifact activation,
explanations and the future research feedback loop. No recurring research schedule is enabled.
