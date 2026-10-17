# Recommendation runs

Cloud-side recommendation runs for the saved paper portfolios, and the run log that records them.
This is beta-only and advisory: a run only requests recommendations and records the results. It never
confirms or resolves a decision, changes holdings, trades, trains or promotes models, or refreshes
market data.

## What starts a run

| Trigger | How | Run id |
|---|---|---|
| `scheduled` | EventBridge Scheduler, `recommendation_trigger.schedule_expression` in `config/beta.json` (beta default `rate(1 hour)`, America/New_York) | `sched-<window>`, the scheduled time truncated to the hour |
| `on_demand` | The operator invokes the Lambda with their own AWS credentials | a fresh id per invocation |

Both go to the Lambda `finplan-<env>-financeagent-recommendation-trigger`. For every configured
portfolio it calls the deployed agent runtime with the existing read-only CI machine client and
requests, sequentially, PPO (`recommend_portfolio`) and minimum variance, mean variance and scenario
CVaR (`recommend_classical_portfolio`). Credentials are fetched through the SSM secret reference at
run time and are never logged or stored.

Start a run now (prints the run id, run status and each method's status and decision id):

```sh
.venv/bin/python scripts/recommend_now.py --env beta
.venv/bin/python scripts/recommend_now.py --env beta --portfolio-id pf_<id> --method ppo --method cvar
```

The script waits synchronously (read timeout above the Lambda's 900 s limit) and never retries the
invocation. It exits 0 for `completed`, `partial` and `not_due`, 2 for `duplicate` and `cap_reached`,
and 1 for `failed` or an invocation error. The raw equivalent is
`aws lambda invoke --function-name finplan-beta-financeagent-recommendation-trigger --payload '{"trigger":"on_demand"}' --cli-binary-format raw-in-base64-out --cli-read-timeout 960 out.json`.
The payload may name `portfolio_ids` and `methods` (`ppo`, `min_variance`, `mean_variance`, `cvar`).

## Formation timing

Runs use the existing decision-cutoff rules and record what was actually used; nothing is inferred from
the clock.

- Before the 09:00 America/New_York formation, news-driven methods return their explicit not-due status.
  The method is recorded as `not_due`, and no substitute policy is reported. If every method is not due,
  the run status is `not_due`.
- After formation, every run of that day reuses the same morning formation. Hourly runs therefore
  re-value holdings against the same inputs, and their records show the same `formation_at`,
  completed session and news source identity.
- Closed-market runs keep the producer's closed-market labelling.

## Duplicate and volume protection

- At most one scheduled run per portfolio per window. The first write is conditional, so a redelivered
  schedule event ends as `duplicate` without calling the runtime.
- A daily cap per New York day (`recommendation_trigger.daily_cap`, beta 40) counts scheduled and
  on-demand runs. Beyond it the outcome is `cap_reached` and the runtime is not called.
- Schedule retries are at most 1, and calls inside one invocation are sequential.

## Run log

DynamoDB table `finplan-<env>-financeagent-recommendation-runs` (on demand). Its name is published at
`/finplan/<env>/financeagent/config/recommendation-runs-table`. There is one item per (run, portfolio):
`pk = run_id`, `sk = portfolio_id`. The GSI `recent` (partition `env`, sort
`started_key = <started_at>#<run_id>`) lists runs newest first. Daily-cap counter items (`cap#…`) are not
runs and are not in the index.

| Field | Meaning |
|---|---|
| `run_id`, `portfolio_id`, `env` | identity |
| `trigger`, `window` | `scheduled` (with its window) or `on_demand` |
| `started_at`, `finished_at` | UTC ISO timestamps |
| `status` | `completed`, `partial`, `not_due`, `failed`, `duplicate`, `cap_reached` |
| `requested_methods` | the methods requested for this portfolio |
| `methods.<m>` | per method: `status`, `decision_id` (`pd_…`), `error_code`, `tool`, `input_snapshot_id`, `completed_session`, `formation_at`, `news` (source day, window, checksum, quality, consumed), `export_run_id`, `policy_artifact_checksum`, `portfolio_revision`, `targets` (instrument to weight), a bounded `narrative_excerpt`, `claim_check_passed`, `usage` (tokens and estimated cost), `session_id` |
| `error_code` | run-level failure code, if any |

Records reference the full immutable decision by `decision_id` and do not copy it. Read the full
decision with the existing explanation, comparison and evaluation paths.

## Querying

Ask the hosted agent. These requests are answered deterministically from the run log, without a
Gateway tool call or model narration:

- "show me the latest 5 recommendations", "last 3 runs", "latest ten policies" (N defaults to 5; the
  maximum is 20; you can write the number as a word up to twenty)
- method filters: "latest PPO recommendations", "last 2 min variance runs", "latest mean variance
  and CVaR runs", "latest traditional recommendations"
- dates (New York calendar days): "recommendations since 2026-10-15", "show runs since today",
  "show me today's recommendations", "which recommendations were issued yesterday"
- a portfolio: "latest runs for pf_<id>"

The answer lists the runs newest first, with each method's status, decision id, completed session,
formation and a short target summary. Then ask to "explain pd_…", or to compare or evaluate a listed
decision; those requests use the existing lifecycle paths. Requests for history, holdings revisions,
snapshots, activity, the "latest three recommendations" history view, or explanations and actions keep
their existing routes. If the run log is not deployed, or reading it is denied, the agent says that it
is unavailable and shows no data.

Operator (own IAM identity, read-only `ssm:GetParameter` and `dynamodb:Query`):

```sh
.venv/bin/python scripts/recommendation_runs.py --env beta latest -n 5
.venv/bin/python scripts/recommendation_runs.py --env beta latest --method ppo --since 2026-10-15 --until 2026-10-17
.venv/bin/python scripts/recommendation_runs.py --env beta latest --portfolio-id pf_<id> --trigger on_demand --json
.venv/bin/python scripts/recommendation_runs.py --env beta get <run_id> [--json]
```

## Rollback

Set `recommendation_trigger.enabled` to `false` in `config/beta.json` and release. This removes the
schedule and function. To stop runs without a release, disable the schedule. The table is retained.
Without the table parameter, the agent reports the run log as unavailable. The desktop 09:05 report is
unaffected.
