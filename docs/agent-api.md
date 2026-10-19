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
| `tool_request` | invoke | optional structured request `{"name": "<tool>", "arguments": {...}}`; authoritative: exactly that tool runs with those arguments (state-changing tools after confirmation; recommendation tools may first read the saved book's universe), never a substitute. A tool the caller's Gateway does not offer fails `FORBIDDEN`, a live-execution/payment tool `OPERATION_NOT_PERMITTED`, invalid arguments the tool's `VALIDATION_FAILED` |
| `capture_inputs` | invoke | beta machine (`ci_test`) clients only, non-streaming: returns `captured_inputs` (planner/provider/tool boundaries, each durably stored as `modeling_input_capture` events, encoded and split into parts like tool-call records). Capture never consumes the tool-call budget; a refused write marks the entry `durable_status: capture_failed` and `input_capture_status: incomplete_durable_capture` without stopping the call |
| `recommendation` | invoke | arguments of a model tool: `{}` for the saved paper portfolio and latest approved market session, or an explicit snapshot/session/holdings request; optional `model` (`min_variance`, `cvar`, `ppo`; default `ppo`) and `instruments` (see "Model tools" below); returns `answer.recommendation` |
| `explanation` | invoke | optional structured explanation request (workflows of `add-explanation-workflows`; see `docs/explanations.md`); the answer carries `answer.explanation` (a contract explanation result) and `prompt` becomes optional |
| `approve` | confirm | `true` runs the pending state-changing call(s) once; `false` declines |
| `stream` | all | `true` (default): SSE events; `false`: one JSON document (the `final` payload) |
| `correlation_id` | all | optional; minted when absent |

## `describe`

```json
{"type": "describe", "framework": "langgraph", "graph_version": 1, "environment": "beta",
 "release_id": "rel_...", "contract_version": "1.3.0",
 "provider": {"kind": "bedrock", "model_id": "<from SSM>", "max_tokens_invocation": 1024,
              "prompt_caching": "disabled", "rates_source": "aws_price_list", "rates_retrieved_at": "..."},
 "skills": [...], "streaming": {"http": true, "websocket": false}}
```

## Stream events (in order)

1. `{"type": "progress", "stage": "accepted", "session_id", "correlation_id"}`, then `progress` per
   graph node (`route`, `plan`, `tool_call`, `narrate`, `claim_check`);
   deterministic policy routing also emits `skill_selected` with the applied skill's name,
   version, declared tools and instruction checksum;
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
            "claim_check": {"passed": true, "checked_figures": 3, "removed_figures": []},
            "tool_call_receipts": [{"tool", "call_id", "sequence", "gateway", "ok", "activity_event_id",
                                    "recording_status", "checksum", "recorded_at", "exact",
                                    "arguments_sha256", "result_sha256"}],
            "tool_call_recording": {"status": "complete | incomplete", "recorded", "failed", "failed_call_ids"}},
 "confirmation": {"type": "confirmation_required", "calls": [{"tool", "arguments", "idempotency_key"}]} ,
 "in_progress": {"run_id": "run_...", "state": "running", "tool": "get_job_status"},
 "declined": [], "error": {<contract error envelope>} , "degraded": "tool_only | tools_unavailable | null",
 "usage": {"provider_kind", "model_id", "input_tokens", "output_tokens", "cache_read_tokens",
           "cache_write_tokens", "estimated_cost_usd", "tool_calls", "invocations"},
 "graph_version": 1, "release_id": "rel_..."}
```

A streamed and a non-streamed run of the same prompt in fresh sessions return identical `final`
payloads apart from `session_id` and `correlation_id`.

Reads never ask for confirmation (`production_strategy` with `action: get` included);
`submit_portfolio_feedback` always does, and free text submits feedback only on an explicit instruction
("record my feedback on ca_…: …"). When no model narrative can be produced (provider error or token
budget), the answer still lists every tool's outcome or contract error deterministically.

## Errors

Every error is a contract envelope (`core/v1/error.json`): `UNAUTHORIZED` (no or invalid bearer
token), `FORBIDDEN` (another caller's session), `VALIDATION_FAILED` (bad body, with
`details.pointer`), `PRECONDITION_FAILED` (confirm without a pending confirmation, or a new prompt
while one is pending), `OPERATION_NOT_PERMITTED` (live trading, paid-job approval, budget raise,
risk-preference change), `BUDGET_EXCEEDED` (token, tool-call, session or `bedrock_explanations`
limits; details name the limit, remaining amount and estimate), `DEPENDENCY_UNAVAILABLE` (Gateway,
tool release or Bedrock model access), `RATE_LIMITED`, `INTERNAL` (never with a stack trace).

## Model tools (contracts 1.14.0)

One read-only MCP tool per model; the tool name fixes the algorithm and every request names its
`instruments` (1-100 unique symbols; weights are returned only for these):

| Tool | Gateway | Model |
|---|---|---|
| `generate_portfolio_recommendation_min_variance` | traditional | FinanceModel classical minimum variance (settings without `cvar_alpha`) |
| `generate_portfolio_recommendation_cvar` | traditional | FinanceModel classical scenario CVaR |
| `generate_portfolio_recommendation_ppo` | primary | frozen PPO policy trained on exactly `instruments`, else a typed "no policy for this universe" error |

`recommend_portfolio` and `recommend_classical_portfolio` were removed outright (no aliases);
mean-variance and `risk_aversion` are no longer served and are refused with an explanation.
Every tool also accepts `input_snapshot_id` + `as_of`, an isolated `holdings` scenario (never the saved
user portfolio), `portfolio_id`, `idempotency_key`, `contract_version`, `synthetic` (classical: `settings`).

**Choosing the tool.** An explicit `tool_request` runs exactly that tool. Free text names the model
with `min_variance` / `min variance` / `minimum-variance`, `cvar` or `ppo` (no model word: PPO), e.g.
`{"prompt": "Give me a portfolio recommendation for AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA, VOO using model cvar"}`
runs `generate_portfolio_recommendation_cvar` with those eight `instruments` against the latest
approved snapshot and the saved paper book (the tool's defaults). A structured `recommendation` payload
runs `generate_portfolio_recommendation_ppo` unless it carries `model` (`min_variance`, `cvar`, `ppo`;
anything else is `VALIDATION_FAILED` at `/recommendation/model`); `model` is not forwarded to the tool.

**Choosing the universe** when `instruments` is not supplied (free text, `recommendation`, or a
`tool_request` without it), in order:

1. a list of two or more ticker symbols in the prompt (`AAPL, AMZN and VOO`); a single symbol or a
   company name in a question ("Should I buy Google?") is not a universe;
2. the instrument IDs of the request's `holdings.weights`;
3. the saved paper portfolio's instruments: the agent first calls `get_portfolio_history`
   (`{"limit": 1}`, plus `portfolio_id` when given) and uses the positions of the latest revision;
4. otherwise the turn fails with `VALIDATION_FAILED` (`details.pointer` `/arguments/instruments`) and
   no model tool runs.

The answer contains every instrument and cash, current/target quantities, signed proposed share and
value changes, reference close/date, portfolio revision, model identity and provenance; the complete
producer result is `answer.recommendation` (classical results are also in `answer.portfolio_analyses`).
Deterministic narration explains movement from current holdings to the model's targets; it does not
invent news or feature-level causes.

**Follow-ups in the same session** re-read stored evidence, never conversation text:

* "Why did you recommend this?", "Explain the risk", "Explain the main drivers": an issued decision
  (`decision_id`) is re-read with `get_portfolio_decision` + `explain_portfolio_decision`; a classical
  analysis without a decision with `explain_classical_recommendation` on its `analysis_id`; a PPO result
  without a decision re-calls `generate_portfolio_recommendation_ppo` pinned to the original
  snapshot/date and saved portfolio ID or exact original holdings, and the same `instruments`, then
  `query_market_data` for that session, and verifies that policy/configuration, state revision and
  allocation reproduce the earlier result (otherwise `PRECONDITION_FAILED`,
  `recommendation_replay_mismatch`). The claim check verifies every figure against those reads.
* The structured `explanation` payload (`docs/explanations.md`) is unchanged.

`answer.skills_used` names the applied recipe (`generate-portfolio-recommendation-ppo`,
`generate-portfolio-recommendation-classical`, `explain-classical-recommendation`,
`paper-portfolio-lifecycle`). Direct MCP `tools/list` exposes the same versioned recipe and checksum in
the tool descriptions; direct clients can call the same tools without the hosted agent.

## Tool-call records (`tool_call` activity events)

Every tool the hosted agent calls (graph planning, explanation workflows, paper lifecycle, the
brokerage reader; not `record_agent_activity` itself) is followed by one `record_agent_activity`
event with `event_kind: "tool_call"`, the agent `session_id` and `correlation_id`, `synthetic` (true
for the machine `ci_test` client and input-capture requests) and idempotency key
`tc-<sha256(session_id|correlation_id|sequence)[:60]>`. The payload (`payload_format`
`finplan.tool_call.v1`) holds `tool`, `call_id`, `sequence`, `gateway`, `ok`, the exact `arguments`,
the full `result` and `error` as received (uncompacted), `started_at`, `finished_at`, `duration_ms`,
`size_bytes` and `arguments_sha256` / `result_sha256` / `error_sha256` (SHA-256 of canonical JSON:
sorted keys, `,`/`:` separators, UTF-8, computed before any encoding). Strings the activity contract
refuses (storage URIs, ARNs, path-like or `http://` values) are stored reversibly as
`{"representation": "encoded_string", "encoding": "base64url_utf8", "value"}`; credentials are replaced
by `<redacted>` markers listed in `redactions` (then `exact: false`). Content above 3 MiB is stored in
`tool_call_part` events (base64url chunks of the canonical content, `parts.content_sha256`).
Recording never consumes the per-turn tool-call budget. A failed recording does not fail the turn:
the call's receipt in `answer.tool_call_receipts` has `recording_status: "failed"` and
`answer.tool_call_recording.status` is `incomplete` (on a failed turn without an `answer`, both fields
are at the top level of `final`). The turn archive (`agent_turn`) lists the same receipts.

Hosted beta uses guarded Bedrock; offline CI retains fixtures/stubs with no network model calls.
The beta Gateway client waits up to 330 seconds. User authorization is enforced by the Gateway;
the Lambda target currently audits a Gateway caller identity, as documented in `docs/gateway.md`.

See `docs/strategy-lifecycle.md` for the four pipelines, beta-to-beta references, artifact activation,
explanations and the future research feedback loop. No recurring research schedule is enabled.
