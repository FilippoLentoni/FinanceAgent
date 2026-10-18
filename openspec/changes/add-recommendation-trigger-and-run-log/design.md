# Design

## Context

- The only automatic recommendation path is `scripts/daily_financial_report.py` run by a desktop systemd timer at 09:05 New York. Its `HostedReader` already obtains a client-credentials token for the read-only CI machine client (`report_token`, `auth_mode=ci_client_credentials`) and calls the runtime with deterministic `tool_request` payloads, one session per call. The `daily-financial-report` spec already permits the CI machine client for unattended read-only operation.
- The runtime authorizes with a Cognito JWT (not SigV4), so any AWS-side invoker must fetch a machine token first.
- Decisions are already persisted centrally by the platform (`pd_…`, decision table + S3 document) with full input provenance; a run log only needs to reference them.
- FinanceAgent infra builds Lambdas as `CfnFunction` with inline code (`infra/stacks/brokerage.py`); there is no asset-bundling path in this repo today. CloudFormation inline Python code is limited to 4096 bytes.
- Account Lambda concurrency is 10; AWS requires at least 10 unreserved, so reserved concurrency cannot be used.
- The runtime role currently has no tool or platform data permissions; all tools go through the Gateway.

## Goals / Non-Goals

**Goals:**
- Cloud-side scheduled and on-demand runs with a durable, queryable run log in beta, verifiable within a few hours (hourly cadence).
- Reuse the existing tool path and identity; no contract, platform or FinanceModel changes.

**Non-Goals:**
- Event-driven triggering from S3/EventBridge data-landing events (all data lands before the 09:00 formation, so the schedule after formation is equivalent; can be added later on the same handler).
- Retiring the desktop 09:05 report, a dedicated machine identity, dashboards/tracing, cross-portfolio decision indexes, and drift-since-decision analysis — follow-ups.
- Gamma/prod enablement.

## Decisions

1. **One Python Lambda (`finplan-<env>-financeagent-recommendation-trigger`), stdlib + boto3 only.** It mirrors `HostedReader`: fetch the CI client secret via the SSM reference, request a token, call the runtime for `recommend_portfolio` (PPO) and `recommend_classical_portfolio` (×3) sequentially, extract evidence, write the run log. Packaging: inline code if it fits the 4096-byte limit; otherwise follow whatever artifact path the repo's pipeline/release synth supports (check `infra/stacks/pipeline.py` first) and add a release-synth assertion that the function code is present. *Alternative rejected:* reusing the agent container image as a Lambda image — needs a Lambda runtime interface in the image and couples release cadence.
2. **EventBridge Scheduler** with `rate(1 hour)` from `config/beta.json` → `recommendation_trigger.schedule_expression`, `flexible_time_window OFF`, retries ≤ 1, timezone America/New_York. The schedule passes `{"trigger":"scheduled","window":<scheduled time truncated to the hour>}`. Lambda timeout 900 s (measured ~11 s per hosted call; 4 calls per portfolio).
3. **On demand** = `lambda:InvokeFunction` by the caller's IAM identity with `{"trigger":"on_demand","portfolio_ids":[…],"methods":[…]}`; `scripts/recommend_now.py` wraps it and prints the run summary. No API Gateway/Function URL (adds auth surface for no gain).
4. **DynamoDB on-demand table `finplan-<env>-financeagent-recommendation-runs`**, PITR off in beta, SSE default.
   - Item per (run, portfolio): `pk=run_id`, `sk=portfolio_id`; attributes per the spec (methods map keyed by method with decision_id, status/error_code, input identities, target summary, narrative excerpt ≤ 2 KB, claim_check, usage).
   - GSI `recent`: partition `env`, sort `started_at#run_id` → newest-first listing; method/trigger/date filters applied as key conditions (date) and filter expressions (low volume).
   - Dedupe: scheduled `run_id = "sched-" + window` and the first write is `attribute_not_exists(pk)` per portfolio item; a duplicate delivery exits as `duplicate`.
   - Daily cap: counter item `pk="cap#<NY date>", sk="cap"` updated with `ADD n :1` under condition `n < :cap`; failure → `cap_reached`, no invocation. Default cap 40/day in beta (24 hourly + on-demand headroom).
   - Item size kept well below 400 KB by storing only references and summaries.
   *Alternatives rejected:* S3 + Athena/Glue (per-query scan cost, more moving parts for point lookups); writing into the platform decision table (owned by FinancialPlanning, contract change).
5. **Agent read path:** grant the runtime role `dynamodb:Query`/`GetItem` on this table and its `recent` index only, add `recommendation_runs_table` to runtime settings via SSM, and add a lifecycle route for "latest/last N recommendations|runs|policies" (N ≤ 20, optional method/date phrases) that answers deterministically from the table, listing run and decision ids. It consumes no Gateway tool calls. This is a narrow exception to "runtime role has no data permissions", limited to an agent-owned table.
6. **Operator read path:** `scripts/recommendation_runs.py latest [-n 5] [--method ppo] [--since DATE]` and `get RUN_ID` using the operator's IAM identity; the table policy allows the operator role read-only Query/GetItem.
7. **Run status:** `completed` (all requested methods returned a decision), `partial`, `not_due` (all news-driven methods not due and no decision), `failed`, `duplicate`, `cap_reached`. Formation/session/news identities are taken from the returned decision provenance, never inferred from clock time.

## Risks / Trade-offs

- [Hourly runs create ~96 decisions/day per portfolio and grow decision history pages] → beta only, daily cap, reduce to one post-formation run after verification.
- [Bedrock narration cost per call] → existing per-session caps and `bedrock_explanations` budget guard; Lambda stops on budget-guard refusal and records `failed`.
- [Lambda concurrency 10 shared with tools] → calls are strictly sequential inside one invocation; schedule retries ≤ 1; never overlaps backfills by design (backfills are manual).
- [CI client is a verification identity used for automation] → accepted for beta per existing spec; dedicated identity is a follow-up.
- [Inline code limit] → keep handler compact or use the pipeline's artifact path; release synth must refuse a missing handler.
- [Runtime role gains a data permission] → scoped to one table + index, read-only, asserted in synth tests.

## Migration Plan

1. Deploy table, Lambda, schedule (enabled) and grants through the beta pipeline with the Gamma barrier helper; Gamma transition restored afterwards.
2. Invoke once on demand; verify run item, decision ids and evidence.
3. Observe ≥ 3 hourly runs; verify dedupe and statuses, then ask the hosted agent for "latest five recommendations".
4. Rollback: set `recommendation_trigger.enabled=false` (removes schedule/function) or disable the schedule; the table is retained.

## Open Questions

- When to drop from hourly to a single post-09:00 run and retire the desktop timer (after a few days of beta evidence).
