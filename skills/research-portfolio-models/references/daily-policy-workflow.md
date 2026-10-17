# Authorized daily policy data and deployment workflow

For an explicitly authorized data/backfill/training deployment task, assess data before submitting a job. Read the current approved snapshot, its actual historical panel, saved portfolio/profile, supported model state and news corpus/readiness. Preserve the requested investable universe GOOGL, AMZN, NVDA, MSFT, AAPL, META, TSLA and VOO separately from the ingestion superset, which may also contain NFLX. Do not add NFLX to the requested policy or silently replace eight assets with a two/five-asset profile. Frozen preprocessing, actor dimensions and inference assets must agree.

PPO needs many ordered state/action/reward/next-state transitions, not necessarily many snapshot objects. A single immutable snapshot containing aligned historical observations can supply those transitions. Assess real chronological coverage after observation warmup, next-session prices and train/validation/held-out separation. State includes lagged market/risk features, current weights and cash, and drawdown/high watermark; transaction costs and action/constraint transforms must match serving. Determine actual missing dates/assets/features before backfilling. News requires publication and first-availability times at or before each decision, immutable source identity and appropriate research/training rights. Late retrieved news is forward evidence from its actual availability, not historical contemporaneous evidence. Preserve missingness rather than fabricating news.

The authorized operator workflow is: freeze a data-readiness assessment; backfill available price/corporate-action history and licensed point-in-time inputs; retain immutable daily raw/curated records; run supported chronological training and matched classical controls; retain all seeds/failures and net-of-cost holdout results; export the complete actor ensemble with preprocessing/state/news identities and its actual evaluation scope; deploy the compatible producer/tool/runtime artifacts; then invoke the real hosted LangGraph tools on the approved saved paper profile and inspect all three explanation families. FinanceModel's `scripts/ppo_forward_checkpoint.py` provides bounded checkpoint/readiness and local CPU candidate training where supported. A blocked news feature is a blocked news model; a separately labeled price-only candidate is not completion of news training.

Carry forward the user's explicit session authorization for concrete operator backfill, training, deployment and hosted verification already requested. Do not repeatedly ask for the same permission. Ordinary daily recommendations still do not authorize those operations, and the MCP paid-launch identity, budget, confirmation and idempotency controls remain enforced. Completed tests do not substitute for actual trained/exported/served evidence, or justify a stronger profitability claim.

The implemented eight-asset price recipe has 58 state dimensions: 48 trailing
mean/standard-deviation log-return statistics across 5/20/60 sessions (scaled by
50 and clipped to [-5,5]), eight asset weights plus cash, and observed drawdown.
The numeric-news candidate adds five features per asset for 98 dimensions:
organization tone, logarithmic article and cluster counts, source-window age and
effective source coverage. Correlation, regime and raw sequence groups are not
enabled in these recipes. Read the frozen artifact's actual feature identities,
preprocessing, constraints, action smoothing and ensemble members; do not infer
deployed behavior from another configuration's score. Preserve all five seeds,
including failed attempts. A selected seed's validation ranking is not an
independently established performance claim for an exported ensemble or a daily
optimizer with different settings, cash rules, costs or book size.

`gdelt-gkg-v2-premarket-organization-tone-v1` reads four actual quarter-hour
machine-metadata archives beginning 07:45, 08:00, 08:15 and 08:30
America/New_York, covering the window through 08:45. Formation is fixed at 09:00
New York, including DST conversion; serving time is separate. Every source must
be available by that cutoff. Late requests retrieve the same day's frozen
formation, rather than constructing a new intraday state. The current fit's
frozen manifest requires 680 actual exchange-session windows. Training attaches
a completed close to the next actual exchange-session formation; unused calendar
gaps do not create synthetic transitions or justify padded observations.

Archive coverage and row acceptance are separate. Complete coverage requires all
four batches and no rejected physical fragments; partial windows retain actual
missing batches and rejected-row evidence. Effective coverage is
`(captured_batches / 4) * ((row_count - rejected_rows) / row_count)` when rows exist,
and zero when none are usable. Unknown inputs retain missingness; healthy observed
zero mentions remain distinct from unavailable data. Partial counts are lower
bounds and observed-subset tone may be biased. Quarantine malformed fragments
with immutable raw checksums; never erase the rejection or label partial evidence
complete. The feature is whole-document organization tone, not calibrated stock
sentiment or an asset-return forecast; Vanguard mentions do not represent
constituent-weighted VOO news.

Historical archive reconstruction uses the explicit source-availability proxy
and retains its later retrieval time. Live serving instead requires actual central
first-seen availability no later than formation. Never backdate central capture
or pass a historical proxy as a live source. Preserve source/rights evidence,
archive and curated checksums, HTTP attempts, actual coverage and publication
timestamps daily. Limit concurrent producer publication/backfill workers to two:
the current regional Lambda concurrency quota is ten, and eight workers previously
starved nested agent/tool/model/platform calls. Local parsing parallelism does not
grant additional producer concurrency.

Use the separately registered matched price-control profile to isolate the news
feature group while preserving seeds, windows, reward, action transforms and
transaction-cost evaluation. Check that the producer actually supports that
profile; an explicit nine-asset snapshot optimizer is not a matched eight-asset
control. Retrospective contemporary-universe selection, revised provider price
history and reused development/test periods remain research limitations. Retain
actual selected-family settings and identify unsupported serving projections;
never silently relax investor constraints to imitate a benchmark.

Daily numeric capture precedes 09:00 formation; the read-only report runs at
09:05 New York. A separate producer price-retention capture runs Friday at 17:05;
it is neither a report trigger nor a new-policy retraining job. A late
or post-close report still identifies its real formation, completed price date
and expected next exchange open, with no asserted fills. Sunday/holiday news with
last-session prices is `closed_market_preview` and
`outside_regular_exchange_session_training_distribution`; re-run at the next
actual market-session formation. Retain frozen report and JSON per attempt plus
private dated and stable latest views. Exercise frozen rationale, actual temporal
decision comparison and observed-outcome/context tools. Distinct centrally
eligible news formations can share a price close; repeated receipts cannot become
history, and missing forward outcomes remain unavailable.

Daily retention is not automatic weekly retraining of the new eight-asset policy.
The legacy scheduled research loop keeps its own portfolio, supported profiles,
one-job rule and category/global budgets. New training, complete export, scoped
paper activation and deployment follow the user's explicit operator authorization;
ordinary advice does not trigger those actions. Use private operator parameters
for portfolios, endpoints and receipts, never public account identifiers, tokens,
holdings or raw observations in this skill.
