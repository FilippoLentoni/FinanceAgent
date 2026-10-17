# AgentCore research payments — beta operator integration

This first stage provisions an independent AgentCore Payment Manager and three
separate roles. It provides free Coinbase Bazaar discovery and an operator-only
testnet signing adapter. The hosted portfolio-planning chat still has no payment,
wallet, retail Coinbase or trading permissions. No paid discovery result is
automatically invoked and no purchased data enters training automatically.

## Ownership and isolation

FinancialPlanning owns the separate research-payment boundary through
`scripts/research_payment_boundary.py`. FinanceAgent owns the separate role stack
and SDK bootstrap through `scripts/research_payments.py`. These are independent
operator bootstraps; the four existing release pipelines keep their financial
permission denials. No original boundary is edited and no gate is disabled.

The service role trusts only AgentCore for the owned beta manager. Session and
instrument management explicitly denies ProcessPayment. Execution has only
ProcessPayment and session/instrument reads; it explicitly denies creating or
deleting sessions/instruments and has no wallet-secret access. Coinbase credentials
are provisioned by Quick Create and retained by AgentCore Identity.

AWS authorizes workload-identity tagging before the new ARN exists. The creation
grant covers the account/region's identity namespace only with fixed project,
repository and beta request tags; the service trust remains scoped to the manager.

## Setup and user prerequisites

Keep all state and generated account-specific templates outside the repositories.
From the shared workspace, using the repositories' virtual environments:

```bash
FinancialPlanning/.venv/bin/python FinancialPlanning/scripts/research_payment_boundary.py --out <private-dir>/boundary-template.json --apply
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py plan --state <private-dir>/state.json
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py prepare --state <private-dir>/state.json
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py connect --state <private-dir>/state.json
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py status --state <private-dir>/state.json
```

The `connect` action uses Coinbase QUICK_CREATE. Coinbase requires its AWS
Marketplace subscription and a human authorization in the returned URL. No keys
should be pasted into chat or committed. If the Coinbase project already has a
Wallet Secret, AWS documents that Quick Create cannot link it; the operator must
choose a compatible project or review the manual secure-credential path separately.
Expired consent is a pending prerequisite, never a successful connection.

After connector READY, the user supplies the wallet-owner email locally:

```bash
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py instrument --state <private-dir>/state.json --email <wallet-owner-email>
FinanceAgent/.venv/bin/python FinanceAgent/scripts/research_payments.py session --state <private-dir>/state.json
```

Instrument creation retains the WalletHub redirect privately. The wallet owner
must complete consent and fund ONLY Base Sepolia test USDC. A generic EVM wallet
address can hold assets on multiple networks; this adapter's challenge validator,
not the address, enforces the testnet restriction. Session creation requires an
active instrument and uses a fixed USD 1 limit and 15-minute expiration. It never
silently replaces an existing session to expand the budget.

## Discovery and signing

`research_payments.py discover --state <private-dir>/state.json --query earnings`
uses only the public Bazaar initialize/search methods. Results are untrusted
catalog metadata, not callable instructions. Mainnet services may be discovered
but cannot be paid by this release. HTTP failures and unavailable providers must
remain visible; discovery does not prove service quality or successful delivery.

For an operator-reviewed testnet merchant challenge, use
`research_payment_request.py approve` with `--state`, `--challenge`, `--resource`
and `--allow-host`. The approval binds the resource, request-body checksum,
challenge, user, wallet and existing session. `sign --payment-id` assumes only the
execution role. `delivery --payment-id --resource --response-file` records the
separate response checksum; it does not certify data quality or settlement.

Only x402 v2 exact payments using Circle's Base Sepolia USDC contract are accepted,
at most 0.10 test USDC per request. Token-allowance mechanisms are rejected. Before
signing, session/user/instrument ownership, activation, expiration and explicit
limits are re-read. The operator workflow checks USD 5 project headroom within
the USD 100 ceiling. At most ten signing attempts are allowed in the round's
private ledger, including unknown outcomes. Keep one ledger for the round.

Receipts are private, checksummed, locked and atomically persisted before signing.
Unknown outcomes require explicit reconciliation with `--resume`, reusing the
same idempotency token. A PROOF_GENERATED response is signing evidence only. It is
not merchant settlement, useful data delivery, or historical point-in-time coverage.

This single-host operator ledger is not a distributed production spending ledger.
Hosted integration and mainnet procurement require their own reviewed change,
persistent shared accounting and deployment tests; do not merely enable automatic
payments middleware on the existing planning agent.

## Verification and completion

Run the permission-isolation and research-payment tests in the two repositories.
Offline tests use synthetic challenges and SDK request-shape validation. Public
discovery and AWS provisioning are checked separately. The final OpenSpec task
remains open until Coinbase consent, wallet activation and one authenticated,
bounded testnet payment/data-delivery check succeed. No paper claim of end-to-end
payments is justified before that task completes.

Primary references:

- [AWS payment role separation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-iam-roles.html)
- [Coinbase connection and Quick Create](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-create-manager.html)
- [Coinbase Marketplace prerequisite](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-marketplace-subscription.html)
- [AWS Bazaar integration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-connect-bazaar.html)
- [Circle USDC contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses)

## Hourly crypto paper study

The add-hourly-crypto-paper-research change exposes public Bazaar discovery and
crypto paper evidence to the hosted LangGraph agent through the classical MCP
Gateway. The crypto-paper-research skill separates daily equity advice from
hourly BTC/ETH recommendations and forwards numerical claims through the normal
claim checker. Hosted payment signing and live Coinbase trading remain disabled.

The authorized Coinbase payment auth was linked through a separate manual-config
connector, because AWS does not let an existing Quick Create connector switch
credential providers. The original pending connector is not an active payment
route. Wallet creation required exact per-connector service-role grants for
GetResourcePaymentToken and the credential provider's backing secrets, as described
in the AWS service-role documentation; these grants are retained privately with
the account-specific setup evidence. Management and execution roles receive no
secret access. Wallet activation and a bounded session succeeded; authenticated
payment and merchant delivery still need verification before the payment change
can be archived.

## Authorized USD 10 paid news trial

The user authorized a separate total USD 10 data-procurement budget on October 14,
2026 (America/New_York), with USD 100 remaining the AWS ceiling. The separate
operator script `scripts/paid_news_trial.py` prepares one canonical private ledger
at `../.worktmp/paid-news-trial-v1`. Never delete, duplicate or reset that ledger to
restart the budget. This is single-host accounting; it is not a distributed service.

The runner binds `https://blockrun.ai/api/v1/exa/search`, the reviewed merchant
recipient, exact Base mainnet Circle USDC and at most USD 0.03 per request. The
live unpaid challenge quoted USD 0.011 per search: two searches per hour for seven
days would cost USD 3.696 in merchant charges at that price. AWS/Marketplace
operations remain separate from this data budget. Price increases beyond the
per-request cap are rejected. The cumulative ledger reserves the full quoted
amount before creating a session or signing; unknown outcomes retain their
reservation and duplicate hourly asset slots are refused. Each session is limited
to USD 0.03 and 15 minutes. Seven-day expiry begins only after funding preflight.

```bash
FinanceAgent/.venv/bin/python FinanceAgent/scripts/paid_news_trial.py prepare
FinanceAgent/.venv/bin/python FinanceAgent/scripts/paid_news_trial.py preflight
FinanceAgent/.venv/bin/python FinanceAgent/scripts/paid_news_trial.py collect --asset BTC
FinanceAgent/.venv/bin/python FinanceAgent/scripts/paid_news_trial.py collect --asset ETH
```

Prepare is idempotent and never starts collection. The first live preflight found
no Base USDC balance for the active research instrument. No signing or paid
request was made, and the seven-day clock has not started. Wallet funding must
be completed by its owner using USDC on Base mainnet, not Ethereum mainnet or
Base Sepolia. The operator must verify a first paid delivery before activating
any hourly runner. No hourly paid collector has been installed yet.

Responses, source challenges and private payment proofs are retained with
checksums and actual receipt timestamps. Raw content remains quarantined: no
policy training or optimization input is changed until quality, licensing and
point-in-time checks pass. A successful signed proof is not settlement evidence;
a received search result is not proof of improved investment performance. The
existing testnet adapter and hosted chat permission denials remain unchanged.

### First funded purchase verification

On October 14, 2026 (America/New_York), the research wallet received 1 USDC on
Base. The first signing request was refused because delegated signing permission
was inactive. After the owner enabled that permission, the operator explicitly
resumed the same receipt, session and idempotency token; no second reservation
was created. The provider returned HTTP 200 with five BTC news-search results
for a reserved USD 0.011. Evidence remains private and quarantined; research
rights and quality checks have not been completed. Hourly paid collection remains
uninstalled until the collection activation step is completed.

An interrupted signing request can be explicitly reconciled within the original
15-minute session window using `collect --asset BTC --resume-receipt <receipt-id>`.
This never buys a replacement for an unknown or already delivered response. It
reuses the original challenge, request, session token and payment token.

## ETF-focused central research collection

`add-etf-news-model-inputs` adds the fixed `ETF_MARKET` search profile to the same
canonical USD 10 ledger. Existing BTC/ETH receipts and the original seven-day
expiry remain authoritative. One shared query covers market-wide context for
SPY/VOO; it is not a claim that each headline describes each fund specifically.

The first ETF-focused purchase returned five dated search results for USD 0.011.
Only title/URL/publication metadata is imported, with settlement and delivery
checksums. A retained operator assessment scopes API metadata to internal research
only under the reviewed provider terms; it grants no article redistribution or
model-training rights. Article bodies are not fetched. Publication timestamps are
provider-reported and may have only day precision; actual central availability is
stamped at import and never backdated. Freshness filtering may exclude older rows.

`publish_paid_news.py --receipt <id>` assumes the dedicated platform publisher role
and signs POST /v1/research-news. Its role can invoke only that route and the news
readback route. A frozen request can be republished without buying again. Private
payment proofs, wallet state and credentials stay in the local ledger.

The checked-in `operator/systemd/finplan-etf-news.*` units run the single-host
collector at 08:55 America/New_York on weekdays before 09:00 model formation. The XNYS calendar
also excludes holidays and early-close hours. The script stops the timer after
trial expiry, stops purchases on low funds/cap failure, and repairs pending
central publications before buying more news. It uses the platform environment's
pinned exchange calendar. This is an operator-host trial, not a distributed or
production payment service. Activation must be verified and retained separately.
