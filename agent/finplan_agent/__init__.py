"""FinanceAgent: the code-based LangGraph explanation agent (design D1).

Hosted in Amazon Bedrock AgentCore Runtime (``finplan_agent.runtime.app``), one Runtime per
environment. Tools are reached only through the environment's AgentCore Gateway over MCP
(``finplan_agent.tools.mcp_client``); the explanation LLM is a pluggable provider
(``finplan_agent.providers``: Amazon Bedrock Converse through the configured inference profile, or
the deterministic fixture provider). Sessions persist as LangGraph checkpoints in AgentCore Memory
(``finplan_agent.session``).
"""

FRAMEWORK = "langgraph"
#: Version of the graph's state layout. Sessions written by a newer graph are not resumed by an
#: older one (design Migration Plan step 5): they start fresh instead of being misread.
GRAPH_VERSION = 1

__all__ = ["FRAMEWORK", "GRAPH_VERSION"]
