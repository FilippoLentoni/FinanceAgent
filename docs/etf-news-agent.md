# Hosted ETF news research

Ask “Show Coinbase ETF model inputs for SPY and VOO” to call the read-only `get_etf_research_inputs` tool through the classical gateway. Unspecified symbols default to SPY and VOO for this research flow. Explicit supported ETF symbols override that default. A missing tool fails without substituting a crypto or daily-equity recommendation.

A matched optimizer sensitivity requires frozen baseline inputs. Include `baseline_analysis_id` or `input_snapshot_id=<approved ETF snapshot>` and a timezone-aware decision timestamp where appropriate. The comparison retains its baseline instrument order, input checksums and declared assumptions. Allocation changes do not demonstrate better accuracy or performance. Missing news features, historical collection limitations and unavailable PPO/LLM policies are reported explicitly.

The runtime final answer includes `answer.etf_research_inputs`, a list of full validated tool responses retaining features, provenance, model readiness and limitations. Existing `portfolio_analyses` retains its original analysis-ID behavior. `answer.brokerage_connection_status` separately reports local connection readiness; news data neither authenticates brokerage nor permits live orders.

The new response is validated against the pinned `tools/get-etf-research-inputs-response` contract before narration or inclusion in the final answer. Numerical claims are checked against the retained result. The packaged `etf-news-research` skill is exposed through the hosted runtime and classical MCP tool description.
