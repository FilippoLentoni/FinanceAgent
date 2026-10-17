import subprocess
from pathlib import Path
from finplan_agent.brokerage.gateway import BrokerageGatewayReader
from tests.unit.test_etf_brokerage import Transport


def test_node_gateway_readonly_owner_operator_signature_and_payloads():
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run(['node',str(root/'tests/brokerage/read_gateway.test.cjs')],capture_output=True,text=True,check=True)
    assert 'checks passed' in result.stdout


def test_gateway_reader_requires_fixed_tool_and_authenticated_result():
    import pytest
    reader=BrokerageGatewayReader('https://example.test/mcp',lambda:'token',transport=Transport())
    with pytest.raises(ValueError):reader.capabilities()
