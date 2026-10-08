# Explanation provider configuration (design D4; tasks 3.1-3.6, 3.11)

No prices and no account-specific values are recorded here.

## Parameters (per environment, written by the FinanceAgent pipeline/bootstrap)

| Parameter | Value |
|---|---|
| `/finplan/<env>/financeagent/config/explanation-provider` | `bedrock` or `fixture` (the registered contract value shape is a plain provider key). Beta is `fixture` only. |
| `/finplan/<env>/financeagent/config/explanation-model-id` | Bedrock model or inference-profile ID (prod: the Claude Opus 5 US profile). Rejected: `global.` profiles, any Qwen identifier, FinanceModel/SageMaker references, ARNs. |
| `/finplan/<env>/financeagent/config/explanation-guards` | JSON: `max_tokens_invocation`, `max_tokens_turn`, `max_tokens_session`, `max_tool_calls_turn`, `max_cost_session_usd`, `temperature` (or null), `prompt_caching` (`enabled`/`disabled`), `rates` `{input_per_1k_usd, output_per_1k_usd, cache_write_per_1k_usd?, cache_read_per_1k_usd?, source: configured\|aws_price_list, retrieved_at}`. Absent cap fields default from `config/<env>.json`; rates never default. |

Startup validation fails (the Runtime health check then fails) for an unknown kind, a denied model ID,
`max_tokens_invocation > max_tokens_turn`, or `bedrock` without input/output rates, `source` and
`retrieved_at`. `prompt_caching: enabled` without both cache rates keeps caching disabled.

## Prompt caching (checked 2026-10-08)

Bedrock Converse supports explicit caching for Claude Opus 5 (minimum 512 tokens per checkpoint, up to
4 checkpoints, `system`/`messages`/`tools`). The adapter places one cache point after the tool
definitions and one after the system prompt plus skill instructions; request-specific content follows.
If Bedrock rejects cache points for a configured model, the adapter retries once uncached and stays
uncached. Cache read/write tokens are recorded and priced with the configured cache rates.

## Budget layers

1. `maxTokens` = `max_tokens_invocation` on every Converse/ConverseStream request.
2. Before every Bedrock call: worst case (estimated input + `max_tokens_invocation` at configured rates)
   against the turn/session token caps, the session remainder (`max_cost_session_usd`), the
   platform `budget-state` flag and the `bedrock_explanations` remainder (allocation minus the
   month-to-date `EstimatedCostUSD` metric summed over all environments, plus this process's not yet
   visible spend). Refusal: `BUDGET_EXCEEDED`, no Bedrock call, tool-only/evidence-only answer.
3. Prompt caching as above.
4. Fixture provider in build and beta; the offline test harness blocks every Bedrock Runtime call.
5. The platform AWS Budgets deny action on the Runtime role (`budget-enforced-role-names`).
