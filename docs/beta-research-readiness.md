# ETF beta research readiness

The coordinated change is `complete-etf-news-and-paper-beta` in FinanceAgent, FinanceModel and FinancialPlanning. Completion requires actual paid-news ingestion and model consumption through the deployed LangGraph recommendation path. Build success, tool discovery, an input adapter or an ineligible model response cannot close that exit criterion.

## Operator collection

`finplan-etf-news.timer` is installed as an operator-host user service. It runs hourly at 09:35–15:35 America/New_York on weekdays, with the pinned XNYS calendar additionally checking holidays and early closes. Persistent catch-up is disabled. The canonical private ledger is `.worktmp/paid-news-trial-v1` at workspace level; never reset or duplicate it. Its original expiry is 2026-10-22 03:09 UTC, and its cumulative ceiling remains $10. This is separate from the $100 AWS project ceiling and the $100 synthetic investment profile.

The cycle repairs pending publication before buying, preserves slot reservations, and disables recurring attempts on expiry, cap exhaustion, failed preflight or uncertain collection. Operators must reconcile private receipts before manually re-enabling a stopped timer. Central publication can be replayed with `scripts/publish_paid_news.py --receipt ID` without purchasing again. Titles/URLs/publication dates are stored centrally with rights, checksums and actual availability; private signing credentials and payment proofs stay outside repositories.

## Measured prerequisites

The original and new ETF samples were each delivered for $0.011 and centrally published. Including the earlier BTC sample, total reserved spending at setup is $0.033. Immutable replay, prior-availability exclusion and publisher access denial on unrelated routes passed. A closed-market cycle made zero purchases. These facts prove delivery and central storage, not predictive quality.

The isolated brokerage Gateway returns only authenticated View-only readiness and verified SPY catalog metadata. Its credential is read only by the dedicated beta Lambda, not the LangGraph runtime. Bootstrap deployment permissions are the three exact beta Lambda/log/PassRole statements in `infra/stacks/policies.py`; their operator application is recorded outside the repository. Live order and transfer routes are absent.

## Exit check

Verify a real LangGraph payload leads to news-aware optimizer and compatible PPO policy recommendations, retaining decision cutoff, news identities, feature protocol and policy artifact checksums. Freeze prices/holdings/constraints while varying news to prove input consumption; allocation changes are not mandatory. Record model eligibility, corpus rights and missingness. Missing historical outcomes must produce an explicit incomplete study, not backdated news or claimed return improvement. Keep the change open until the strict exit check succeeds.
