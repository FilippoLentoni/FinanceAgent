# Daily research collection and bounded history

The operator can explicitly apply the authorized two-week collection plan:

```sh
.venv/bin/python scripts/paid_news_trial.py configure-daily-plan
```

This local configuration action requires an existing started trial. It preserves
its original start, USD 10 cumulative cap, reservations, receipts, payment
recipient, and request history. It records the original expiry and authorization
reason, and sets expiry to `2026-10-31T03:59:59Z` (October 30, 23:59:59 New York).
The checkpoint is October 30 after the market closes; collection does not itself
start retraining or promote a model.

The plan collects `ETF_MARKET` and `GOOGL` once per New York trading day. At the
observed USD 0.011 per request, expected collection cost is USD 0.022 per trading
day; each actual quote must still satisfy the USD 0.03 request ceiling. The
repository timer runs weekdays at 08:55 New York; the cycle additionally checks
the XNYS session calendar. Installation/reload is a separate operator step.
Without the daily plan, the existing hourly slot grouping remains unchanged.

For an explicit historical collection, supply both UTC publication bounds:

```sh
.venv/bin/python scripts/paid_news_trial.py collect --asset ETF_MARKET \
  --start-published 2026-10-01T00:00:00Z \
  --end-published 2026-10-02T00:00:00Z
.venv/bin/python scripts/publish_paid_news.py --receipt RECEIPT_ID
```

Bounds require ISO UTC seconds ending `Z`, a positive interval, and no more than
31 days. Validation happens before AWS clients, budget checks, or payment calls.
The provider receives `startPublishedDate` and `endPublishedDate`; a successful
request does not establish that historical coverage is complete. Five results
per query remain the bound. Inspect returned publication timestamps and coverage
before treating data as training evidence.

Historical reservation IDs derive from the profile and complete bounded query,
so the same window cannot buy a replacement on another day, and historical IDs
cannot collide with live hourly/daily slots. Explicit signing resume retains the
original reservation/token and its original short session deadline. Unknown
outcomes retain their reservations and require reconciliation. Daily collection
continues to repair pending central publication before considering new purchases;
all successful scheduled pulls are published centrally by the existing publisher.

Historical publication preserves a verified actual publication timestamp only
inside the explicit request window and no later than retrieval. Outside-window,
future, missing, or timezone-free dates remain null and quality-unverified. The
retrieval timestamp is never backdated, and central ingestion assigns availability
at ingestion time. Existing live seven-day normalization and the central reader's
seven-day retrieval window remain unchanged. Historical metadata does not confer
training rights or make the data available to a historical decision retrospectively.

## Installed first checkpoint

The operator installed `finplan-etf-news.timer` and the one-shot
`finplan-forward-news-checkpoint.timer` on the research host. The checkpoint is
October 30, 2026 at 16:15 America/New_York; user lingering is enabled. These
host timers require the EC2 machine to remain running.

`forward_news_checkpoint.py` assumes the existing narrow central publisher/read
role, resolves the endpoint from SSM, and runs the Model CLI in capture-only
mode. It writes the immutable corpus, checksum manifest and readiness report in
`.worktmp/news-backfill/checkpoint-20261030`. Credentials stay in subprocess
environment and are not printed. No purchase, learner or serving promotion occurs.
An early invocation exits `checkpoint_not_due`; `--verify-now` explicitly captures
the preceding fourteen days for a read-only installation check.

The installation check read six actual central deliveries and reported
`missing_price_bundle` and `rights_review_pending`, with zero learners. Training
is not scheduled as an unconditional action: the Model candidate runner requires
approved actual price arrays, eligible training rights/coverage and fresh whole-run
budget evidence. See `FinanceModel/docs/ppo-forward-checkpoint.md`. The current
research-only classification is a conservative unresolved review, not evidence
that the provider categorically prohibits model training. Additional collection
alone does not resolve that classification or demonstrate improved returns.

Observed historical probe: the September 1--8 ETF request cost USD 0.011 and
returned five results, three dated September 17. Only the September 4 and 6 rows
were centrally curated; all raw results were retained. There have been five
purchases totaling USD 0.055 across the unchanged cumulative USD 10 ledger.
Expected ten-session daily collection cost is USD 0.22 at the observed quote.
Raw and curated current versions have no configured age expiry in beta storage;
the seven-calendar-day API read bound is separate from storage retention.

## October 17 selected universe (supersedes the two-profile plan above)

Daily profiles now are GOOGL, AMZN, NVDA, MSFT, AAPL, META, TSLA and VOO.
VOO represents the requested S&P500 exposure. Eight symbol-specific searches
cost an observed $0.088/day, approximately $0.88 for ten sessions. The original
ledger/cap/expiry remain preserved. Legacy ETF_MARKET receipts are still replayable
but are no longer a scheduled daily profile. Each new publication is scoped to
one symbol; a search association is not proof that every returned article is
company-specific or financially useful. The checkpoint captures two four-symbol
batches; no expanded hosted PPO model or eight-asset training result is claimed.
Expanded price ingestion covers the eight selected symbols plus legacy NFLX; model readiness remains separately verified.

## Premarket collection and daily readiness

The updated timer runs at 08:55 America/New_York. Collection is permitted from
08:45 through the actual session close on XNYS session dates, including early
closes, using FinancialPlanning's shipped versioned calendar. Weekends and
exchange holidays make no purchases. This lets the same-day news arrive before
the 09:00 model formation and the 09:30 regular session open. Trial expiry,
cumulative reservations, funding checks and uncertain-payment handling remain
the collection limits.

Every scheduled attempt writes a private, immutable JSON readiness record under
`.worktmp/news-backfill/daily-status/YYYY-MM-DD/`. It includes the actual central
manifest identifier, retrieval and availability timestamps, publication status,
research eligibility and training eligibility per profile. A reserved or unknown
delivery cannot count as published. Research eligibility never implies training
permission. The report runner should read the newest attempt for its decision
session and verify the timestamps against its own cutoff before claiming news
was available; the model's artifact evidence separately establishes whether it
actually consumed news. Beta's expanded price universe retains NFLX alongside
the eight selected symbols, preserving the original portfolio's price coverage.
