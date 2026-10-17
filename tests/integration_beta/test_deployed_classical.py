"""Real beta dual-Gateway calls; no paid jobs or strategy changes."""
import uuid

import pytest

from finplan_agent.skills import CLASSICAL_SKILLS, load_skills
from finplan_agent.tools.mcp_client import GatewayMcpClient
from infra.stacks.tool_policy import CLASSICAL_POLICY_FILE, grants, load_policy
from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed


@pytest.fixture(scope='module')
def classical():
    env = deployed()
    if env.env != 'beta':
        pytest.skip('classical Gateway is opted into beta only')
    url = env.param(env.own('agent', 'classical-gateway-endpoint-ref'))
    assert url != env.param(env.names.gateway_endpoint_ref)
    client = GatewayMcpClient(url, env.ci_token, timeout_seconds=330, forward_identity=True)
    return env, client


def test_independent_gateway_discovery_and_packaged_skill_parity(classical):
    _, client = classical
    offered = {tool.name: tool for tool in client.list_tools()}
    ci_grants = {tool: roles["ci_test"] for tool, roles in grants(load_policy(CLASSICAL_POLICY_FILE)).items() if "ci_test" in roles}
    unrestricted = {tool for tool, limit in ci_grants.items() if limit is None}
    assert unrestricted <= set(offered) <= set(ci_grants)
    assert 'record_agent_activity' in offered and 'resolve_portfolio_decision' not in offered
    assert 'recommend_portfolio' not in offered
    inventory, _ = load_skills()
    by_name = {s['name']: s for s in inventory}
    for name in set(CLASSICAL_SKILLS) & set(offered):
        skill = CLASSICAL_SKILLS[name]
        assert by_name[skill]['instructions_checksum'] in offered[name].description
        assert f"{skill}@{by_name[skill]['version']}" in offered[name].description


def test_classical_recommendation_is_persisted_and_reproduced(classical):
    _, client = classical
    issued = client.call_tool('recommend_classical_portfolio', {'algorithm':'min_variance'})
    assert issued.ok, issued.error
    aid = issued.result['analysis_id']
    retrieved = client.call_tool('get_classical_analysis', {'analysis_id':aid})
    assert retrieved.ok and retrieved.result == issued.result
    why = client.call_tool('explain_classical_recommendation', {'analysis_id':aid,'instrument_id':'GOOGL'})
    assert why.ok, why.error
    assert why.result['source_analysis_id'] == aid
    assert why.result['recommendation'] == issued.result['recommendation']
    assert why.result['explanation']['reproduction']['status'] == 'verified'
    shapley = why.result['explanation']['shapley']
    assert abs(shapley['reconciliation_residual']) <= shapley['tolerance']


def test_hosted_agent_uses_both_gateways_with_skill_traces(classical):
    env, _ = classical
    status, result = env.invoke_agent({'prompt':'Compare PPO and traditional portfolio planning recommendations today','stream':False}, 'dual-'+uuid.uuid4().hex)
    assert status == 200, result
    assert result['status'] == 'completed', result
    answer = result['answer']
    assert answer['recommendation']['strategy'] == 'ppo'
    assert answer['portfolio_analyses'][0]['recommendation']['strategy'] == 'min_variance'
    assert {(e['tool'],e['gateway']) for e in answer['evidence']} == {('recommend_portfolio','primary'),('recommend_classical_portfolio','classical')}
    assert {s['name'] for s in answer['skills_used']} == {'recommend-portfolio','recommend-classical-portfolio'}
    assert answer['claim_check']['passed'], answer['claim_check']
    assert result['usage']['invocations'] == 0


def test_ci_principal_cannot_start_paid_research_with_forwarded_token(classical):
    _, client = classical
    result = client.call_tool('run_portfolio_research', {'review_id':'ca_'+'a'*32,'dry_run':False,'confirmed_by_user':True,'idempotency_key':'beta-ci-paid-denial-v1'})
    assert not result.ok and result.error['code'] in ('FORBIDDEN','OPERATION_NOT_PERMITTED'), result
