# Beta selected-strategy serving verification — 2026-10-09

The recommendation path passed real authenticated MCP and hosted LangGraph requests in beta.
This verifies serving behavior; it does not establish PPO investment superiority or complete the
older CPU discrepancy-analysis workflows. Human login/client onboarding remains a separate check.

## Released components

All four beta releases use contracts `1.2.0`. Workload releases ran through their existing pipelines.

| Repository | Beta release | Deployed source commit |
|---|---|---|
| FinancialPlanning | `rel_01M4G30AE48T4MC0R8MSBCFW8V` | `6f323e2a7c746da53259d3fb688ee279e5a1a6b5` |
| FinanceModel | `rel_01M4G4VTWFX6T09PF7YJH1JP35` | `580b727f47dc23bb6a4091dfe3acccc45d6c7f5d` |
| FinanceLambdasTool | `rel_01M4G72R9JFEXAPH8VYWPVJCXC` | `e0fdeda7d599eb17b41f636b8d2029325aab205b` |
| FinanceAgent | `rel_01M4G61BPYRVXG15XG9KJ5DY9F` | `8deb3102a72f8deccd569991b369c3abfb6b84a4` |

Documentation, task tracking and the local login helper are follow-up commits; they do not change
the hosted runtime image. Gamma/prod workload releases are outside this milestone.

The shared FinanceAgent Tooling stack was updated through a reviewed CloudFormation change set.
Its pipeline previously passed only 12 tool targets although the released catalog contained 18.
The update refreshes target parameter overrides and CodeBuild exports; it does not deploy
Gamma/prod workloads. PublishRelease now rejects a dropped target handoff instead of reporting success.

## Frozen policy and activation

- Source experiment: `run_01M4ERRE6F29S2N9DGHJBTYJE8`.
- Export job: `run_01M4G3X3HJKG2WD3DKEZ3C51SD`; `prepare_policy`, no retraining.
- Configuration: `cfg_c7f9f9249b453712d61229ec6d468067e2a629dfc88c407f6a42b853a3425acb`.
- Artifact: `policy_inference_2e922b07194c6ab51da2c74d34b70667bb1b3351`, 717,393 bytes.
- Artifact checksum: `sha256:2e922b07194c6ab51da2c74d34b70667bb1b3351867cc3e692ee1b6e48c61ccb`.
- PPO seeds `[0,1,2,3,4]`; deterministic mean target weights; frozen observation/action transforms.
- Beta advisory activation recorded at `2026-10-09T11:05:17.635212Z` with a DynamoDB audit identifier.

The activation updates `/finplan/beta/financemodel/config/advisory-policy`, separate from
`production-strategy`. The export dry-run estimate was USD 0.048334. No SageMaker training job
was created in this round. The selected PPO remains research/advisory; prior benchmarks do not
establish that it outperforms min-variance reliably.

## Real serving checks

The fixture was a **hypothetical** USD 10,000 all-cash paper portfolio, with high watermark USD 10,000,
using approved snapshot `snap_01M4G3BWYX7WRADXCS33PEZH35` through completed close `2026-10-08`.
The market input was reported `synthetic: false`; those holdings are not the user's actual holdings.

| Request | Observed latency | Result |
|---|---|---|
| Direct authenticated MCP, first allocation | 40.716 s | Full five-seed PPO result, feasible constraints |
| Direct MCP, identical repeat | 12.521 s | Identical recommendation |
| Hosted agent, structured recommendation | 22.017 s | Completed; full producer result preserved |
| Hosted agent, natural-language request | 20.101 s | Completed; same producer result preserved |

The hosted requests used the configured Bedrock provider and one recommendation tool call each.
Their reported Bedrock costs were USD 0.00106584 and USD 0.00104220. The natural-language narrative
passed the figure check (17 checked figures, none removed). The recommendation includes all
allocations, buy/sell deltas, constraints, snapshot/configuration/artifact provenance and bias disclosures.
Its calibrated return forecast is explicitly `not_available`.

Observed target weights for this demonstration were AAPL 4.0988%, GOOGL 6.7340%, NFLX 1.8420%,
NVDA 4.5372%, VOO 3.2880%, and cash 79.5000%. These figures record a serving test, not an
investment recommendation for the user.

Before/after checks confirmed no new processing job or experiment run record from the recommendation
requests, and no change to production strategy selection. Four successful calls correlated between
the tools and model logs by the same correlation identifier. The tool audit identifies `gateway:beta`;
the model receives its corresponding caller block. This does not establish a verified end-user subject
inside Lambda; user authorization is enforced at the Gateway.

## Defects found by deployment

The shared pipeline target list was stale. Earlier component suites could pass while the Gateway
omitted the recommendation tool; the new target-handoff release gate catches that failure.

The reader's Lambda deny rule excluded only the unqualified inference ARN. Actual Lambda
authorization evaluated `:$LATEST`, so the explicit deny blocked the intended call even though an
unqualified IAM simulation passed. The policy now permits/excludes both exact ARN forms for the
same function. Other versions, aliases, functions and environments remain denied; regression tests
cover them. The direct invoke client still makes a single attempt.

SSM publication twice exhausted its normal retry bound on throttled writes. Publication now uses
at most 10 standard attempts, while runtime SSM defaults remain unchanged. Higher-throughput
Parameter Store was not enabled. Other fixes cover the activation audit key, serving log level,
first-request provider refresh and prompt budgets large enough for the actual catalog/skills.

The account's one-instance CPU processing quota also caused export queuing while a cancelled
integration fixture finished cleanup. No quota increase or additional RL training was requested.

## Costs, access and remaining scope

The round is capped at USD 2 within the existing USD 50 project budget. The final conservative
estimate at 11:47 UTC was **USD 1.2562 (approximately USD 1.26)**:

| Service | Estimate |
|---|---:|
| CodeBuild: 40 builds, 121 rounded minutes | USD 0.605 |
| CodePipeline V2: 158 rounded action minutes | USD 0.316 |
| CPU processing, including provisioning/cleanup as an upper bound | USD 0.0852 |
| Model/API/storage allowance | USD 0.25 |

These are estimates, not settled billing. The AWS project budget still reported USD 5.935 as of
09:48 UTC, before this round. Detailed accounting is retained in the operator's local evidence files.
All owned pipeline executions are terminal, original transition states are restored, and all eight
Gamma/prod release manifests are exactly unchanged. The final regional check found no active
SageMaker processing or training jobs; no training job was created in this round.

See [beta-access.md](beta-access.md) for the local PKCE helper, hosted HTTP request and Codex/Claude
MCP configuration. The helper passed 18 offline tests and accepts the deployed public metadata;
no human account, invitation, browser sign-in or personal client connection was performed.

The full CPU accounting/attribution/sensitivity workers and missing comparison-tool integration
retain their earlier OpenSpec acceptance criteria. The synchronous evidence summary is not a
replacement for those workers. A recurring literature/experiment controller is a future phase;
no recurring research schedule was enabled. See [strategy-lifecycle.md](strategy-lifecycle.md).
