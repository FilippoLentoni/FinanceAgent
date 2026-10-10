# Beta paper portfolio lifecycle verification

Verified at **2026-10-10 16:57:23.307761+00:00**. Scheduled stored market data, both MCP Gateways and the hosted LangGraph agent support the paper recommendation → review → confirmed accept/reject → versioned holdings → subsequent recommendation lifecycle. Contract release: **1.5.0**.

| Pipeline | Beta release | Deployed functional commit |
|---|---|---|
| financialplanning | `rel_01M4K5CHF32XT2EXSY32ZT0Y7F` | `018b6a476f7260537c34b3cc15fa9e947665b67d` |
| financemodel | `rel_01M4K7JCY6XH7H1DNB96X8RY7W` | `670bc60a37164bdae5c7c1c4f227144c7069c62b` |
| financelambdastool | `rel_01M4KA7Q5XMYACDW9HYY62MSWW` | `96e909c46ac9988c01486d45fb679b644553596c` |
| financeagent | `rel_01M4KB8BHHBR51MM5Q3W04DYCK` | `ef386082b9d24203d9d78c007421e92cc7e4e106` |

All four exact-commit beta executions passed their required build/deployment/integration actions. They were stopped before Gamma, and the original transition states were restored. Gamma/prod manifests and serving-policy selection stayed unchanged.

## Live verification

The isolated test book `pf_01M4K9VBK6ZYZBEQPZFVQQX2KC` started with USD 10,000 cash at revision 1. Its original market reference date was 2026-10-07; the issued latest PPO recommendation used stored completed prices dated 2026-10-08. The user's existing saved portfolio was compared byte-for-byte with its pre-test state and remained unchanged.

- Both Gateways expose the same ten lifecycle tools; PPO and traditional recommendation tools remain separate.
- The primary remote MCP publishes the complete `recommend-portfolio@0.3.0` recipe, with matching runtime instructions and SHA-256 checksum. The CI catalog exposes read-only recommendations while omitting the acceptance tool.
- A cash-only book receives a traditional recommendation across the complete approved stock universe. Unit regressions additionally verify that an accepted full exit does not prevent later repurchase.
- Hosted and direct MCP PPO explanations and dated comparisons resolve to identical numerical analysis IDs. Frozen PPO replay is verified; traditional explanations include optimizer reproduction and Shapley evidence.
- Explanation parity is compared at the same decision status. Accepting a proposal adds resolution evidence and therefore produces a new explanation identity; its frozen issued recommendation and checksum remain unchanged.
- Hosted acceptance pauses for explicit confirmation using a real, isolated Cognito PKCE test user. Confirmation creates revision 2 with self-financing fractional fills and recorded costs. Repeating acceptance with a new retry key through the other Gateway returns the same resolution without another revision. CI cannot accept.
- A previous isolated acceptance committed successfully but its returned receipt failed the public privacy check. After the fix, retrying that exact decision recovers the stored resolution, retrieves and explains the accepted decision, and confirms the holdings head is still revision 2. The complete lifecycle was then verified on a fresh isolated book.
- A new direct request and a new hosted conversation both read revision 2. Two concurrent competing acceptances produce one commit and one conflict, resulting in revision 3. Confirmed rejection leaves revision 3 unchanged. Stale decisions fail without applying.
- The latest three holdings revisions, decisions and stored market snapshots are retrievable. Archived agent turns retain versioned skill checksums and successful tool evidence; invocation receipts retain sanitized inputs/results and caller joins.
- Large Unicode audit payloads are reconstructed through MCP pagination and verified against their checksum. A cursor from another history partition is rejected.
- Both human Gateway catalogs advertise the confirmation flag as boolean. The full contract and adapter still require exactly `true`; constant-only contract fields no longer fall back to object during Gateway schema projection. Every completed hosted acceptance test passes the numeric claim check, with version metadata preserved only when it matches the current tool evidence exactly.
- No later observed market session exists yet after the test acceptance. Both channels report `no_forward_observations`, rather than manufacturing a green/red result. Calibrated expected-return forecasts and broker execution evidence remain explicitly unavailable.

Raw hosted/direct receipts and the machine-readable summary are in `/home/ec2-user/projects/.worktmp/finplan-lifecycle/live-acceptance/`. The acceptance summary records 59 expected successful/denied cases. Operator verification, schedule verification and the cost/isolation audit are retained alongside them. Test credentials were held outside repositories; the suppressed temporary Cognito user and token file were deleted after verification.

## Data and cost

Both weekday morning market-ingestion schedules remain enabled at 09:00 America/New_York. They store completed daily stock/ETF observations in S3 and metadata in DynamoDB. Requests consume those approved snapshots and the latest saved holdings. There is no automatic intraday/news/macro feature feed. Existing weekly research remains bounded, proposal-only and subject to reviewed activation.

The conservative incremental deployment/verification estimate is **USD 1.55**, below the **USD 5.00** round cap. AWS Budgets last reported **USD 7.71 / USD 50.0** at 2026-10-10 11:28:26.075000+00:00; billing can lag. No training jobs were created. CI smoke processing jobs were stopped and all round builds/jobs are terminal. The cost audit distinguishes billed-budget data, estimated build/pipeline/CPU usage, and its other-services allowance.

The first agent rollout hit a Gateway action-catalog registration race and rolled back cleanly. The deployed fix serializes target registration within each Gateway and makes policies wait for the complete catalog, retaining optional targets and the same permissions. Synthesized templates are compacted and checked against CloudFormation's 1 MiB limit. Dedicated graph tests cover complete, partial, single-tool and empty catalogs.

Live tests also uncovered a malformed PPO explanation response, a failed audit archive that replaced the original validation error, and a constant-only confirmation flag incorrectly advertised as an object. These were corrected and retested through the public dispatcher and remote Gateways. PPO explanations use additive `policy_recommendation` evidence with a versioned immutable identity; classical recommendations keep their existing contract. Archived JSON-pointer diagnostics use documented lossless typed segments while caller-facing errors retain their original pointer. Human decision reviews show proposed trades and preserve full frozen inputs in immutable evidence.

The last receipt failure came from private AWS caller identity metadata embedded in an otherwise valid committed resolution. A common public-response projection now exposes stable caller hashes consistently in resolution, decision, explanation and history responses. The original audit identity remains in immutable storage, financial fields and producer checksums remain unchanged, and the public privacy check remains enabled. Focused dispatcher/lifecycle tests cover deterministic projection and retries in addition to the live recovery checks above.

The final next-state test found that a provider-planned PPO recommendation returned the correct saved holdings and passed its numerical claim check but omitted skill-version evidence. Skill selection now follows actual planned tools across both planning paths, and common investment-recommendation wording uses the deterministic route. Focused runtime/archive and deployed regressions cover this path. The live run resumed from its verified revision 2, preserving every earlier receipt; hosted explanations, comparison and confirmation retry were repeated against the final agent release before completing the remaining lifecycle checks.
