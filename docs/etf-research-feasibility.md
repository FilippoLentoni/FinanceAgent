# ETF research and eventual Coinbase execution

Research checked October 14, 2026 (America/New_York). This records feasibility;
it does not claim a deployed ETF-only experiment or authenticated ETF trade.

## Findings

ETF data acquisition is feasible through the existing AgentCore Payments / x402
architecture. StableFinance advertises index-fund holdings and ticker coverage,
historical OHLCV, snapshots, financial news and central-bank rates for USD 0.02
per successful call. ETF ticker coverage, dates, adjustment methodology and rights
must be checked before use. Our direct unpaid holdings probe did not yield a
usable JSON challenge, so this provider is documented rather than delivery-verified.
Massive documents x402 market bars, snapshots, news/sentiment and filings; its
intraday FMV prices are indications, not guaranteed executable quotes. Specific
ETF coverage and endpoint price must be verified independently.

The existing platform already configures SPY daily data and a mixed research
universe containing VOO plus individual stocks. This is useful infrastructure,
but it is not an ETF-only research universe or a deployed ETF news enrichment.

Coinbase offers stocks and ETFs for eligible brokerage accounts. Its Coinbase
for Agents MCP documents equities orders and product discovery. Individual ETF
eligibility must be verified through the authenticated product catalog; generic
stock support does not prove every ETF or every account can trade it. Equity
product reference prices do not prove candle, order-book or preview support.
Orders default to regular market hours and current tool schemas govern sessions,
sizing and supported order types. Coinbase's app advertises broader extended
hours; do not transfer that assumption to each agent order endpoint.

AgentCore Payments only signs payments for resources. It does not place ETF
orders. Eventual execution would require a separate Coinbase brokerage OAuth/MCP
or correctly scoped supported API/CLI integration callable by LangGraph running
on AgentCore. Remote MCP custom harnesses require Coinbase client approval;
dynamic registration alone is insufficient. A CLI/API alternative requires
separate credentials and validation. Existing CDP wallet credentials and the
research wallet do not grant brokerage access. Coinbase documents isolated agent
portfolios as spot-crypto only; ETF isolation needs account-specific validation
and application-enforced limits instead of assuming that portfolio feature.

## Recommended experiment adaptation

Use an ETF-only synthetic USD 100 book across broad equity, international equity,
bond and cash exposures. Select actual research symbols only after coverage
checks. Use adjusted total returns with splits/distributions handled consistently,
market calendars, point-in-time holdings and real publication/availability times.
Start with daily allocation decisions and hourly news collection during regular
US market sessions. Intraday trading is a separate controlled ablation; ETF
planning does not require 24/7 orders or treating closed-market prices as fresh.

Shared features can include rates and yield-curve changes, market regime,
volatility/correlation, fund overlap, sector concentration and relevant news.
Optimization needs explicit forecast/risk mapping; PPO needs a matching ETF
observation/action definition and retraining; LLM policy needs a separate frozen
protocol and evaluation. Compare price-only and enriched variants on identical
chronological windows, fees, turnover and controls. Collecting news now does not
create historical training coverage. No automatic policy promotion or real trades.

Preserve the existing cumulative USD 10 data cap and ledger; do not create a new
ETF budget by resetting it. The last observed Base balance is 0.989 USDC after a
USD 0.011 BTC proof-of-delivery test. As an illustrative low-cost design, one
USD 0.011 shared macro/ETF search across seven hourly collection slots per normal
weekday costs USD 0.385 over five full days. Holdings refreshes can be less
frequent. Holidays, provider quote changes, paid price requests and AWS/Marketplace
operations alter the total. The existing fixed BTC/ETH buyer has not been switched
to ETF queries or activated as an hourly paid collector. ETF collection, model
updates and hosted ETF evidence tools still require implementation and tests.

## Primary sources

- https://stablefinance.dev/
- https://www.massive.com/blog/x402-payments-for-ai-agents
- https://help.coinbase.com/en/trading-and-funding/buy-sell-stocks-funds
- https://help.coinbase.com/en/trading-and-funding/trading-hours-market-closures
- https://docs.cdp.coinbase.com/ai-agents/coinbase-for-agents/coinbase-mcp
- https://github.com/coinbase/agents
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-how-it-works.html
