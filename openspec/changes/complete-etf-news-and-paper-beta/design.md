# Design

## Context
See proposal.md. Platform beta tests passed, but Model/Tools/Agent beta transitions remain disabled under the prior owned release. One paid ETF receipt exists in the canonical private ledger but has not been centrally published. Brokerage credentials now support verified View-only REST reads; the hosted path is still missing.

## Goals / Non-Goals
**Goals:** Complete the existing release and supply reproducible operational, model-input, paper-portfolio and read-only brokerage evidence.

**Non-Goals:** Mainnet investment orders, credential privilege upgrades, automatic serving-policy promotion, unlicensed training or fabricated historical news. Bounded research-only PPO training is allowed when actual data rights and coverage support it. Keep the existing $100 AWS project and $10 research ceilings.

## Decisions
- Reuse the canonical receipt ledger and its original seven-day expiry; replay publication before enabling recurring purchases. This avoids duplicate spending and resetting limits.
- Preserve actual central availability and immutable raw/curated evidence. Shared ETF-market and GOOGL company profiles retain separate symbol scopes and ledger slots. Old news can prove delivery but cannot be relabeled fresh; collect a new fixed-profile sample only inside existing limits if needed for current-input verification.
- Release platform → model → tools → agent using recorded pipeline execution identities and explicit beta dependency barriers. Stop each verified execution before Gamma and restore its original transitions; builds alone do not imply release completion.
- Implement brokerage access as a narrowly scoped Agent-owned read-only Gateway boundary, with an exact secret permission and explicit caller binding. It remains independent from payment credentials and has no order routes.
- FinanceModel's coordinated change owns a reproducible matched experiment runner and separate $100 paper simulation. Historical news performance is ineligible without point-in-time coverage and rights; prospective comparisons record actual observations without claiming alpha.
- Deployed verification retains sanitized JSON responses, feature masks, comparison settings, release identifiers and hosted claim checks. The paper cites these measured records.

## Risks / Trade-offs
- Short research-only news history → disclose historical/PPO training ineligibility and use prospective evidence.
- Funds or trial expiry interrupt collection → fail before purchase, retain reservations and pending publication evidence.
- Brokerage API schemas differ from ticker assumptions → use verified catalog identifiers and bounded reads; never infer trading authorization from catalog flags.
- Partial rollout → hold downstream beta transitions until upstream APIs and contracts are ready, retain rollback/release identities.

## Migration Plan
Publish the existing receipt after platform readiness, test immutable replay and time cutoffs, release each downstream component, run hosted checks and matched experiments, activate the expiring operator timer, and update the paper. Roll back a failed component using its previously recorded beta reference; never promote Gamma or production.

## Exit criteria and iteration
The decisive integration check is retrieval/payment → centralized news → point-in-time model features → authenticated LangGraph payload → optimization and compatible PPO recommendations. Verify actual consumed feature/policy identities, not just tool discovery. Change only news while freezing the remaining inputs and verify that the model input evidence changes; a particular allocation need not change. A legacy actor or readiness-only response fails this check. Repair and rerun failures. Keep the change open for unavailable rights, historical data or compatible policy artifacts, recording the prerequisite and preserving spending/expiry controls.

## Selected daily universe (October 17, 2026)
The user selected GOOGL, AMZN, NVDA, MSFT, AAPL, META, TSLA and the S&P 500.
Represent the index using VOO, not an orderable index ticker. Collect eight
separate symbol-scoped news searches per trading day and persist raw/curated
records centrally. Preserve the $10 cumulative data limit and current authorized
expiry. Observed expected cost is $0.088/day, $0.88 for ten sessions; quotes
remain authoritative. Keep old receipts and their original symbol scope.
The existing API permits at most five symbols per response; capture the eight
assets in two four-symbol batches. This does not constitute an eight-asset PPO
training integration. Aligned approved price ingestion for all eight assets,
explicit daily collection/no-news/failure status, compatible multi-batch PPO
state integration and deployed recommendation evidence remain required. Do not
claim an old two-asset profile or policy covers the new investment universe.
