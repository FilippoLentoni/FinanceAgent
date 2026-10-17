# Beta saved-paper recommendation verification — 2026-10-10

Ordinary portfolio questions now invoke the hosted AgentCore/LangGraph agent's
`recommend_portfolio` MCP tool without supplying holdings on each request. Real authenticated
MCP and hosted-agent checks passed. This verifies serving behavior, not investment superiority.

## Released components

All four existing pipelines deployed beta with contracts `1.3.0` and frozen wheel SHA-256
`ee347e88ecdd80f135a4cd79c0eec8bc98ca8744b4ec5acf9961e1ada7728638`.

| Repository | Beta release | Deployed source commit |
|---|---|---|
| FinancialPlanning | `rel_01M4HKY3A4075DQ14ZVR5TS69R` | `db026270e78ab2d11dc8399b8881c96c516688d1` |
| FinanceModel | `rel_01M4HHQF1EXE59WJR4TT3E3F65` | `0e245ec0a2338ac015d6eacdb4d6f6ce0b61cd96` |
| FinanceLambdasTool | `rel_01M4HHJWCFC8KBKQ75E61KSFHH` | `65db3136ad2d1fbc6e618aa0afda1b32e2d17d87` |
| FinanceAgent | `rel_01M4HHHFMGX5CCVXXP8MJ6ZXB9` | `55a56b1e90f1ad805c3a389c5298f6de19d01f9b` |

Documentation and completion checkboxes are later commits; they do not change the deployed image.
Release order was Platform → Model → MCP Tools → Agent, using beta manifests and references.
Gamma/prod transitions were blocked during the rollout. All eight Gamma/prod manifests remain
exactly unchanged, owned executions are terminal, and original transition states are restored.

## Persisted paper book and policy

The user approved a one-time **USD 10,000 hypothetical paper portfolio**, initialized from the
existing published equal-weight plan: AAPL, GOOGL, NFLX, NVDA and VOO at USD 2,000 each, cash zero.
The operator preview checked the five 20% weights and raw completed close prices before writing.
State revision is `1`, book date `2026-10-08`, and quantities are fractional.
An existing saved state is preserved by the initializer. Recommendation requests never write it.

Inference reads saved quantities/cash and the latest approved universe snapshot, values positions
using raw closes, and reconstructs the high watermark over every covered session since the book
date. Actor inputs retain the frozen adjusted-price feature basis used in offline training.
Market session for this check: `2026-10-08`; snapshot `snap_01M4G3BWYX7WRADXCS33PEZH35`.

The selected advisory policy was unchanged: PPO seeds `[0, 1, 2, 3, 4]`, aggregation
`mean_target_weights`, experiment `run_01M4ERRE6F29S2N9DGHJBTYJE8`, export `run_01M4G3X3HJKG2WD3DKEZ3C51SD`,
configuration `cfg_c7f9f9249b453712d61229ec6d468067e2a629dfc88c407f6a42b853a3425acb`. Artifact checksum:
`sha256:2e922b07194c6ab51da2c74d34b70667bb1b3351867cc3e692ee1b6e48c61ccb`. No retraining, new export or strategy activation occurred.
The production strategy pointer and research-plan reference also remained unchanged.

## Live acceptance

| Request | Observed latency | Result |
|---|---|---|
| Authenticated MCP, call 1 | 42.996 s | Identical saved-book recommendation |
| Authenticated MCP, call 2 | 13.792 s | Identical saved-book recommendation |
| Hosted agent: how should I invest today? | 16.313 s | Completed; one MCP tool call, no model call |
| Hosted agent: Should I buy more Google stocks or sell? | 15.608 s | Completed; one MCP tool call, no model call |

Both natural-language questions followed AgentCore → MCP Gateway → tool Lambda → frozen-policy
inference Lambda. Basic recommendation routing and rendering are deterministic; the hosted agent
reported one tool call and zero Bedrock invocations for each. Other research workflows can still
use the configured model provider.

The hosted recommendations exactly matched direct MCP output. All five instruments, current and
target quantities, signed changes, reference prices, target weights, cash and provenance survived
rendering. Figure checks passed without removed figures or thinking markup. Unrounded quantity,
notional and cash arithmetic balanced. Repeated calls retained the same state revision and
positions, and created no processing or training jobs.

Observed output for the approved paper book (display quantities rounded):

| Instrument | Action | Current shares | Target shares | Share change | Value change USD | Target weight |
|---|---|---:|---:|---:|---:|---:|
| AAPL | sell | 5.8751 | 5.8097 | -0.0654 | -22.27 | 19.78% |
| GOOGL | buy | 5.7423 | 6.5508 | +0.8085 | +281.58 | 22.82% |
| NFLX | sell | 27.9447 | 23.1056 | -4.8391 | -346.33 | 16.54% |
| NVDA | sell | 8.6775 | 8.2294 | -0.4481 | -103.29 | 18.97% |
| VOO | sell | 2.8118 | 2.4987 | -0.3132 | -222.75 | 17.77% |

Cash: current USD 0.00; target USD 413.05
(4.13%). Portfolio value: USD 10000.00.
These are recorded test outputs for a paper book; no trades executed. A calibrated return forecast
is explicitly unavailable. Repeat questions revalue saved holdings, rather than applying earlier
recommendations. Fills, corporate actions, dividends and cash flows require explicit book updates.

## Deployment defects and cost

Adding state/latest-snapshot routes pushed the API Gateway resource policy over its 8,192-byte
limit. AWS rolled the update back. Removing resource entries already covered by existing terminal
wildcards preserved the Allow/NotResource unions and explicit denials. Regression tests check
permission equivalence and reserve 512 bytes below the service limit. The successful beta policy
is 7,461 bytes under normal JSON serialization. A stale contracts example was also corrected before
the successful build. A deployed daily-loop test used wall-clock time after close while replaying
the morning fire-date idempotency key, then waited for the wrong session. Its scheduler event and
expected session now use the same configured morning fire time; market freshness checks remain intact.

The optional October 9 refresh did not produce approvable data: the provider returned five
rows rejected by validation. A direct GOOGL provider recheck confirmed missing close and adjusted
close, with rejection reason `missing_close`. The paper book and live checks therefore use the
latest approved October 8 session explicitly; no prices were fabricated or validation relaxed.
The first historical refresh exceeded the existing 29-second API router timeout; subsequent
operator attempts used the existing 300-second ingestion Lambda, without changing permissions.
All operator attempts are included in the cost estimate.

This round's final conservative estimate is **USD 0.8825**,
within the USD 2 incremental cap and the USD 50 project budget:

| Service | Estimate USD |
|---|---:|
| CodeBuild: 23 builds, 76 rounded minutes | 0.3800 |
| CodePipeline V2: 108 rounded action minutes | 0.2160 |
| CPU processing upper bound (2 integration jobs) | 0.0365 |
| Other service allowance | 0.25 |

The prior serving/export round remains separately recorded at USD 1.2562.
These estimates are not settled billing. AWS Budgets reported USD 6.0160
as of `2026-10-09 20:38:03.211000+00:00` and may lag this work. Final checks found no active processing or
training jobs and no new training job in this round.

See [beta-access.md](beta-access.md) for hosted HTTP and Codex/Claude MCP connection instructions,
and [strategy-lifecycle.md](strategy-lifecycle.md) for the four-pipeline lifecycle. Human sign-in and
personal client setup remain separate onboarding checks. The broader CPU discrepancy workers and
recurring literature/experiment controller retain their own acceptance criteria; this change does
not complete them or enable a recurring research schedule.
