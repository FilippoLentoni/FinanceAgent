# Tasks

## 1. Run log storage and configuration

- [x] 1.1 Add `recommendation_trigger` config to `config/beta.json` (enabled, schedule_expression `rate(1 hour)`, timezone, portfolio_ids `[pf_01M547WFC8S9W35652YX87Z204]`, methods, daily_cap 40, narrative_excerpt_bytes) and validation in the config loader; verify with config unit tests (missing/invalid fields rejected, disabled outside beta by default).
- [x] 1.2 Add the DynamoDB on-demand table `finplan-<env>-financeagent-recommendation-runs` (pk/sk, GSI `recent`) to infra only when enabled; verify with synth assertion tests (billing mode, keys, GSI, tags, absent when disabled).
- [x] 1.3 Implement the run-log module (item builder from hosted evidence, dedupe conditional write, daily cap counter, status derivation, bounded narrative excerpt, credential scrubbing); verify with unit tests using recorded hosted receipts from `.worktmp/daily-financial-report/manual-news-ppo-verification/` (completed, partial, not_due, failed, duplicate, cap_reached).

## 2. Trigger Lambda and schedule

- [x] 2.1 Implement the trigger handler (stdlib + boto3): CI client-credentials token via SSM secret reference, sequential `tool_request` calls for PPO and the three classical methods per portfolio, error capture without credential echo, run-log writes; verify with unit tests faking SSM/Secrets Manager/HTTP (scheduled, on_demand, duplicate window, cap reached, auth failure).
- [x] 2.2 Add the Lambda, its role (read CI secret + SSM refs, write/read its table, own log group), log group (30 days), and EventBridge Scheduler schedule (configured expression, retries ≤ 1) to infra; choose packaging per design decision 1 and add a release-synth/gate assertion that the handler code is present; verify synth tests (least-privilege statements, no wildcard actions, schedule target and input).
- [x] 2.3 Add `scripts/recommend_now.py` (invoke the Lambda on demand with portfolio/method options and print the run summary); verify with a unit test of payload construction.

## 3. Query paths

- [x] 3.1 Grant the runtime role read-only Query/GetItem on the run table and `recent` index, publish the table name via SSM to runtime settings; verify synth tests that the grant is limited to this table.
- [x] 3.2 Add the agent lifecycle route for "latest/last N recommendations|runs|policies" (N ≤ 20, optional method and date phrases) answered deterministically from the run log with run and decision ids; verify graph tests (routing, ordering, N bounds, empty log, filter by method).
- [x] 3.3 Add `scripts/recommendation_runs.py` (`latest`, `get`) for the operator; verify unit tests with a fake table.
- [x] 3.4 Document the trigger, on-demand usage, run-log fields and queries in `docs/recommendation-runs.md`; verify the leak scan passes.

## 4. Release and beta verification

- [ ] 4.1 Run the full FinanceAgent suite, `scripts/agent_gates.py`, contracts pin check and leak scan once; commit and push to `codex/advisory-policy-agent`; release through beta with the Gamma barrier helper and restore the Gamma transition; verify Build and Beta IntegrationBetaTests succeed.
- [ ] 4.2 Invoke once on demand in beta; verify the run item exists with four method entries, decision ids resolvable via `get_portfolio_decision`, and input identities matching the decision provenance.
- [ ] 4.3 After at least three hourly windows, verify one item per window per portfolio (no duplicates), statuses consistent with formation rules, and ask the hosted agent "show me the latest five recommendations"; record evidence paths in the change folder.
