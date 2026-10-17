# Proposal

## Why
The ETF news code passed builds but remains behind beta deployment barriers; paid delivery has not reached central storage and the hourly collector is inactive. Authenticated Coinbase reads work locally, while the hosted agent still uses an older paper-policy release.

## What Changes
- Finish and verify the dependency-ordered beta release across platform, model, tools and agent, preserving Gamma/production.
- Publish existing paid receipts without repurchasing, enable the expiring market-session collector under the existing cumulative $10 cap, and verify actual availability and immutable replay.
- Add repeatable deployed checks and evidence for news features, model readiness, matched comparisons and a separate $100 simulated portfolio.
- Complete authenticated read-only brokerage Gateway access and distinguish it from trading permission and research payments.
- Record performance-study eligibility honestly: a short research-only news sample cannot establish historical alpha or train a new PPO policy.
- Require the complete pipeline-to-hosted-recommendation exit criterion: actual news inputs consumed by optimization and a compatible PPO policy, with iteration after failures rather than accepting readiness metadata as completion.
- Update the conference paper with measured deployment, experiment and limitation evidence.

## Capabilities
### New Capabilities
- `beta-research-readiness`: Evidence-backed release, collection and hosted readiness for ETF research and paper planning.
### Modified Capabilities
None. Existing ETF-news and Coinbase-brokerage changes retain their detailed interfaces and are completed alongside this change.

## Impact
FinanceAgent operator scripts, timers, hosted verification, brokerage Gateway infrastructure, documentation and paper. FinanceModel owns the coordinated `complete-etf-news-and-paper-beta` experiment/paper-simulation change. FinancialPlanning and FinanceLambdasTool finish their existing `add-etf-news-model-inputs` beta releases. The $100 AWS ceiling and separate $10 research ceiling remain in force. Bounded research-only PPO training is included when licensed point-in-time data permits it; no live order, funding, trading credential or automatic serving-policy promotion is included.
