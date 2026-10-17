# Daily financial report operation

`scripts/daily_financial_report.py` asks the deployed beta LangGraph runtime for
PPO, minimum-variance, mean-variance and scenario-CVaR advice, then retains each
method's immutable decision and the three existing explanation families: issued
recommendation rationale, distinct-date plan changes, and observed outcomes with
dated market context. It also invokes real cross-method comparisons. No decision
is accepted and no holdings are changed.

Run with the FinanceAgent environment:

```sh
.venv/bin/python scripts/daily_financial_report.py --config /home/ec2-user/.finplan/daily-financial-report.json
```

The private configuration supplies `environment: beta`, `region`, `portfolio_id`,
`expected_instruments`, `methods`, `output_dir`, `news_status_dir`, `gdelt_status_dir`,
`require_news_features` and the complete `classical_settings`. Use
`auth_mode: ci_client_credentials` for the existing read-only machine client. The
runner resolves its secret through the existing SSM references without persisting
or printing the token. Interactive saved-user use may instead supply
`auth_mode: user_refresh_token` and an existing private `token_file`; credentials
must not be copied into the repository.

The runner checks returned snapshot identity, completed-session date, holdings and actual news feature/provenance inputs after each default call. It retains the producer current decision cutoff: passing yesterday's `as_of` would request a historical-close decision and wrongly exclude the morning ingestion. Sequential calls are not atomic, and changed inputs invalidate the controlled comparison.

The daily output is `output_dir/YYYY-MM-DD/report.md` plus `report.json`, with
private immutable hosted receipts under `attempts/`. A `partial_readiness` report
preserves available outputs and explicitly identifies unavailable methods,
universe mismatch, unconsumed news, stale prices and missing news collector
completion. Closed-day reports still run and label the latest completed session.
A new decision has no future observed outcome; absent historical plans are never
manufactured. Report status does not establish profitability or approve a model.

The operator templates in `operator/systemd/` schedule 09:45 America/New_York every
day, including Sunday 2026-10-18. Before installation, populate the private config
with the approved paper profile's real identifier. Preserve legacy books rather
than rewriting them to match a newly trained universe. The report reads a byte-identical checksum-pinned copy of FinancialPlanning's
versioned XNYS calendar packaged with FinanceAgent and the
collector's `.worktmp/news-backfill/daily-status/YYYY-MM-DD/<attempt>.json` markers.
The timer's ordering follows collection on this host; the marker and expected
completed-session checks remain authoritative when a job fails or runs late.

PPO needs chronological transitions, not multiple snapshot identifiers. One
approved snapshot with a long enough point-in-time price/feature panel can contain
many training transitions. Daily raw/curated retention supports future training,
but news retrieved today cannot be backdated as previously known evidence. See the
workspace cross-repository data/training specification for promotion requirements.

Numeric GDELT models report their actual feature protocol, UTC archive source day,
release/central availability and source-age lag. Their current cutoff combines
these eligible archives with completed-session prices. The separate paid-headline
collector remains research context and is not proof that this numeric actor was
trained on or consumed same-morning headlines. Local GDELT capture markers are
retained as operational evidence; central availability comes from the model's
frozen source provenance.
