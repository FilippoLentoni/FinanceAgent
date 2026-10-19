# Beta modeling EC2 operations

This workflow is restricted to beta in us-east-2. DynamoDB and verified immutable
S3 evidence are authoritative; local scheduler receipts record operational health.
The current deployment and live acceptance evidence is kept under
`/home/ec2-user/projects/.worktmp/beta-modeling/`. Read `CHECKPOINT.md` and
`deployments/*-result.json` before resuming or deploying. A source commit, successful
synth, manual smoke run or reminder is not proof of a completed scheduled cycle.

## Data and evidence

FinancialPlanning owns the additive research-input, research-run and
portfolio-observation tables. Their exact physical mappings and bounded day,
approach, portfolio, observation-time and pending-forecast indexes are documented
in FinancialPlanning `docs/modeling-evidence-storage.md`. Existing portfolio,
portfolio-decision, portfolio-history, activity and audit stores retain their
authoritative purposes. Existing keys and data are preserved. The four consumers
pin the same frozen finplan-contracts 1.13.1 wheel, SHA-256
`d6416d4e63058ca3c35fdf17585480e56b12f08e296ff31a95af7a6da349fda2`.

Each distinct evaluated policy/optimizer has a separate synthetic book with the
same initial capital within its comparison cohort. Fixed five-asset, eight-asset
and native BTC/ETH hourly cohorts are compared separately. Full S&P 500 news
collection does not imply a frozen policy can invest in every constituent. The
published current constituent universe is dated and forward-only; historical
membership must not be reconstructed with today's list. Missing trained policies,
vendor dependencies or approved compute remain explicit coverage blockers.

The actual deployed graph uses LangGraph StateGraph and is distinct from direct
Codex review. It captures orchestration, actual transformed provider wire input and
the served numerical policy input separately. Codex receives the actual selected
permitted public headlines and approved market descriptors, not fictional demo
inputs. News-aware RL retains actual GDELT metadata/features, feature ordering and
normalization; price-only actors say news not consumed. Model weights are immutable
versioned model artifacts, separate from the exact decision observation payload.
Never capture credentials or hidden model reasoning.

The explicit recommendation route can execute deterministic planning, the
`generate_portfolio_recommendation_ppo` tool (or the model named in the request) and deterministic narration without a Bedrock model
call. The first live capture followed this route and returned the served PPO
allocation. Label the allocation origin and actual captured boundaries; a
configured Nova model is not evidence that Nova ran or consumed the news.

Automatic execution applies only to newly isolated synthetic experiment books.
Existing user portfolio acceptance still requires explicit confirmation. The
common evaluator enforces eligible prices, costs, cash and positions; policy
intents, simulated fills and no-ops remain separately inspectable. Equity off-session
decisions await eligible prices. The native crypto cohort explicitly uses approved
completed one-hour Coinbase candles and its declared 24/7 policy, not fabricated
minute bars. Existing fee/spread policies and cohort identities are persisted with
each execution. Existing compute approvals and spending caps are unchanged.

Forecasts exist only when actually supplied by a model. Freeze USD baseline,
expected value and future target with the decision. A deterministic allocation is
not a forecast. Reconciliation selects the first valid authoritative valuation
whose actual price/value as-of time is at or after the target, records signed and
absolute errors, and never rewrites the forecast or a matched outcome. Pending
heads have no seven-day expiry. MAE/RMSE use matched observations only. Closed
markets, missing quotes and immature windows remain visibly pending.

## Durable units

The installer `scripts/install_modeling_jobs.py` requires a real, matching beta
data-path verification receipt before enabling these user units. User lingering
must be enabled. Source roots, Python environment and source commit environment
variables point to an immutable EC2 release. Keep EC2 running; laptop/SSH closure
does not stop these jobs, but stopping EC2 does.

| Unit prefix `finplan-beta-modeling-` | Cadence |
| --- | --- |
| snapshot | Every minute: real headless Codex creates one zero-byte snapshot |
| research | Every thirty minutes: real headless Codex invokes retrieval swarm and review |
| hourly | At :05 UTC: permanent hourly slot accounting, all cohorts, forecasts and report |
| eod | 17:00 America/New_York daily, including closed-market days |
| weekly | 17:25 America/New_York daily, rolling seven-calendar-day window |

Each prefix has a `.service` and `.timer`. Jobs use independent non-overlap locks,
bounded subprocess/process-group deadlines, retries with the original permanent
slot ID, atomic local status and bounded health logs. Hourly recovery accounts for
missed equity AND crypto slots without evaluating today's data as historical
inference. DynamoDB conditional writes, invocation leases, permanent execution
receipts and optimistic holdings revisions prevent duplicate fills after retries.
Snapshot work cannot invoke the financial workflow. Preserve unrelated schedules;
the legacy Claude snapshot timer must remain disabled.
The snapshot service admits three starts per minute so its ordinary minute
activations cannot exhaust an hourly start limit. The other services retain a
three-start hourly bound. Start-limit failures remain visible in the journal;
never fabricate snapshot files for missed activations.

The weekly service admits 3050 seconds for its two sequential read-only report
windows (closed, then provisional). Only the exact configured interpreter and
`platform_root/scripts/modeling_report.py` with `--weekly` or
`--repair-weekly-from-queue` receive a 1500-second child deadline. Ordinary daily
reports, model/native/provider commands and their retry budgets retain 900 seconds;
research retains 1500 seconds and the hourly service retains 3400 seconds. Paid/GPU
approval, vendor caps, artifact/transport limits and environment isolation are
unchanged.

A controlled 169-hour DST-fallback corpus on 2026-10-18 exceeded the former
900-second report deadline: aggregation/consolidation took 384.49 seconds and
original-byte packing 497.97 seconds, leaving no time for encoding/publication.
The original failed evidence is retained. The report-only 1500-second bound is a
runtime allowance, not proof of weekly capacity; acceptance still requires an
actual complete build under that bound plus exact original-input browser checks.

The weekly timer runs at 17:25 America/New_York to usually follow the 17:05
hourly report. Reports share a per-output process lock before heavy aggregation;
contention remains inside the same bounded retry deadline. Model, research and
snapshot jobs remain independent. EOD remains at 17:00; the hourly cadence is
unchanged. A delayed or failed publication stays visible in job health.

An explicit `report_cache_dir` in the private EC2 configuration enables the
reporter's disposable checksum-verified original-byte cache for hourly, EOD and
weekly jobs. Snapshot/research jobs do not receive that setting. DynamoDB heads
are always read fresh; deleting only this cache forces verified S3 reads and
does not delete authoritative evidence or reset any run, forecast or portfolio.

`report_refresh_dir` retains small operational tasks for late managed-model cost
receipts. The importer writes a task before acknowledging its Model outbox entry;
the report clears it only after archiving the exact authoritative observation.
Historical daily revisions run before the current daily view. A separate read-only
hourly command repairs at most one queued historical weekly window, preserving a
newer weekly latest alias. These tasks contain record references, not another
portfolio ledger. Leave failed or overflowing tasks visible for recovery.

Initial deployment verification is deliberately separate from recurring proof.
The manual primary slot before the declared experiment horizon remains visible
as original evidence, and is excluded from automatic comparison execution. The
first declared hourly slot may be accounted as missed if installation completes
after its timer; retain that declared horizon and never relabel a manual run as a
scheduled invocation. Check actual `systemd_scheduled` receipts and permanent
run IDs for acceptance.

Use these commands as ec2-user:

```bash
systemctl --user list-timers 'finplan-beta-modeling-*'
systemctl --user status finplan-beta-modeling-hourly.service
journalctl --user -u finplan-beta-modeling-hourly.service --since today
systemctl --user stop finplan-beta-modeling-research.timer finplan-beta-modeling-hourly.timer
systemctl --user start finplan-beta-modeling-research.timer finplan-beta-modeling-hourly.timer
```

Stopping timers leaves an already running service to finish safely. If intervention
requires stopping a service, inspect its DynamoDB lease/input/run and ledger receipt
before retrying; reuse the original slot. Disable all five explicitly named timers
for a rollback, retain every immutable artifact and ledger entry, then install the
previous verified immutable EC2 release/config. Do not reset existing portfolios,
delete tables, overwrite input bytes or replay fills to roll back code. A beta-only
AWS rollback uses the existing supported release manifest/pipeline with Gamma held;
never approve a production pipeline action. Restore a temporary Gamma transition
only after its beta-only execution is terminal.

## Reports and acceptance

The private self-contained artifacts are
`/home/ec2-user/projects/reports/beta-modeling/latest.html`, `latest.csv`,
`latest.json`, dated files, immutable `archive/` revisions and `weekly/` reports.
Open HTML through the IDE file explorer or copy it using the existing SSH/SCP
connection. No AWS browser credentials or CDN are required. Exact original input
downloads are embedded with checksum verification, independently pinned run
selectors, raw/derived news layers and complete numerical values. Reports use
bounded DynamoDB queries and original S3 bytes; they never regenerate features.
See FinancialPlanning `docs/beta-modeling-reports.md` for CLI and browser checks.

Controlled replay lives in a separately labeled `replay/` subtree and must never
enter the forward leaderboard. Real live proof requires three consecutive scheduled
Codex snapshots, two actual half-hour cycles and two distinct scheduled hourly
slots with every registered approach accounted for. Inspect original input bundles
for both decisions of every approach. Missing or uninspectable required approaches
remain coverage blockers even if the rest of the deployment works. Daily and weekly
reports stay provisional until their actual cutoffs/window mature; the observed
comparable winner is not proof of universal optimality or permission to promote it.

The hourly configuration includes `commands.rollover`: at New York midnight it
renders the previous calendar day using `{previous_day}` before the current-day
commands. This incorporates late evening and 23:30 interim recommendations into
a new immutable dated revision while leaving the original 17:00 archive intact.
The current-day render runs last and restores the current `latest.html` alias.

## Host supervision

The existing IDE exhausted the host's inotify watch allowance during live
verification. The additive host setting
`/etc/sysctl.d/90-finplan-beta-modeling-inotify.conf` sets
`fs.inotify.max_user_watches=262144`; it preserves existing IDE processes and
does not change cloud resources or financial spending limits. Original value and
rollback evidence are in `.worktmp/beta-modeling/inotify-adjustment.json`. Inspect
`/proc/sys/fs/inotify/max_user_watches` and the user journal if systemd reports
inotify `ENOSPC`. To roll this host change back, first check that the file still
contains the task-owned setting, remove only that file, and restore the recorded
previous value with `sudo sysctl -w`; preserve any subsequent administrator edits.

An hourly runtime exit75 means a permanent slot remains incomplete, commonly
because the original invocation's600-second lease has not expired. The wrapper
keeps the same command and slot under its existing job lock, waits at most30
seconds between attempts, and retries only this explicit condition within one
900-second command deadline. It never advances the successful-hour checkpoint
while work is incomplete. Generic command failures are not retried by this path.
After lease expiry the runtime recovers the captured attempt without repeating
model invocation. Expired deadlines remain failed health events for later repair.
