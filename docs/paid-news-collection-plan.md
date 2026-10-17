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
repository timer runs weekdays at 09:35 New York; the cycle additionally checks
the XNYS session calendar. Installation/reload is a separate operator step.
Without the daily plan, the existing hourly slot grouping remains unchanged.

For an explicit historical collection, supply both UTC publication bounds:

```sh
.venv/bin/python scripts/paid_news_trial.py collect --asset ETF_MARKET \
  --start-published 2026-10-01T00:00:00Z \
  --end-published 2026-10-02T00:00:00Z
.venv/bin/python scripts/publish_paid_news.py RECEIPT_ID
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
