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
