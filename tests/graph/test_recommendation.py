import hashlib
import json
from pathlib import Path

from finplan_contracts.schemas import schema_id

from finplan_agent.providers.bedrock import BedrockProvider
from finplan_agent.skills import load_skills
from finplan_agent.tools.catalog import ToolCatalog
from scripts.build_stage import PACKAGE_PATHS
from tests.fakes.agent import FakeToolClient, StubBedrockClient, bedrock_config, catalog_document, make_service, run, text_response, tool_use_response
from tests.unit.test_budget import Reader

ROOT = Path(__file__).resolve().parents[2]
PPO = 'generate_portfolio_recommendation_ppo'
UID = '01JABCDEFGHJKMNPQRSTVWXY01'
REQUEST = {'input_snapshot_id':'snap_'+UID,'as_of':'2026-10-08','instruments':['VOO'],
           'holdings':{'weights':[],'cash_weight':1.,'portfolio_value':1000.,'high_watermark':1000.}}
RECOMMENDATION = {'mode':'advisory_paper','strategy':'min_variance','policy_source_run_id':'run_'+UID,
                  'configuration_id':'cfg_'+'a'*64,'policy_artifact_checksum':'sha256:'+'b'*64,
                  'target_weights':[{'instrument_id':'VOO','weight':.5}], 'cash_weight':.5,
                  'decisions':[{'instrument_id':'VOO','action':'buy','delta_weight':.5,'indicative_notional':500.}],
                  'diagnostics':{'bounded_detail':'x'*5000}}


def service(*, error=None, provider=None, provider_config=None):
    tools = FakeToolClient({PPO:lambda a:{'recommendation':RECOMMENDATION,'synthetic':True}},
                           errors={PPO:error} if error else None,extra_tools=[PPO])
    svc, tools = make_service(tools=tools, provider=provider, provider_config=provider_config,budget_reader=Reader())
    doc = catalog_document()
    doc['tools'].append({'name':PPO,'description':'PPO recommendation for an explicit universe',
                         'input_schema_id':schema_id('finance','tools/generate-portfolio-recommendation-ppo-request'),
                         'output_schema_id':schema_id('finance','tools/generate-portfolio-recommendation-ppo-response'),
                         'state_changing':False,'role_class':'reader',
                         'lambda_ref_parameter':'/finplan/beta/financelambdastool/lambda/generate-portfolio-recommendation-ppo-arn'})
    svc.deps.catalog_loader = lambda:ToolCatalog.from_document(doc,environment='beta')
    return svc,tools


def test_structured_request_preserves_full_allocation_and_provenance_without_confirmation():
    svc,tools = service()
    final = run(svc,{'recommendation':REQUEST,'stream':False})
    assert final['status'] == 'completed' and final['confirmation'] is None
    # No model named: the PPO tool.
    assert tools.calls == [(PPO,REQUEST)]
    assert final['answer']['recommendation'] == RECOMMENDATION
    assert 'min_variance' in final['answer']['narrative']
    # A later unrelated turn cannot accidentally return the earlier allocation.
    later = run(svc,{'prompt':'Hello','stream':False})
    assert later['answer']['recommendation'] is None


def test_denied_or_missing_state_does_not_create_jobs_or_invent_weights():
    for code in ('FORBIDDEN','VALIDATION_FAILED'):
        err = {'code':code,'message':'Provide valid authorized portfolio state','retryable':False,'details':{}}
        svc,tools = service(error=err)
        final = run(svc,{'recommendation':{'instruments':['VOO']} if code == 'VALIDATION_FAILED' else REQUEST,'stream':False})
        assert final['answer']['recommendation'] is None
        assert final['answer']['evidence'][0]['error']['code'] == code
        assert [name for name,_ in tools.calls] == [PPO]


def test_natural_language_planner_can_use_selected_strategy_and_preserves_producer_result():
    cfg = bedrock_config('beta')
    stub = StubBedrockClient([tool_use_response(PPO,REQUEST),text_response('The selected strategy recommends buying VOO. This is advisory only.')])
    svc,tools = service(provider=BedrockProvider(cfg,stub),provider_config=cfg)
    final = run(svc,{'prompt':'What should I buy or sell for the portfolio state I supplied?','stream':False})
    assert tools.calls == [(PPO,REQUEST)]
    assert final['answer']['recommendation'] == RECOMMENDATION
    assert final['usage']['provider_kind'] == 'bedrock'


def test_skill_inventory_and_image_build_include_versioned_instructions():
    inventory,instructions = load_skills(ROOT/'skills')
    sk = next(s for s in inventory if s['name'] == 'generate-portfolio-recommendation-ppo')
    text = (ROOT/'skills/generate-portfolio-recommendation-ppo/SKILL.md').read_text()
    assert sk['instructions_checksum'] == 'sha256:'+hashlib.sha256(text.encode()).hexdigest()
    assert sk['version'] == '0.4.0' and PPO in sk['tools'] and text in instructions
    assert 'skills' in PACKAGE_PATHS
    assert 'COPY skills/' in (ROOT/'container/Dockerfile').read_text()


def test_beta_mcp_deadline_covers_tool_and_backend_deadlines():
    cfg = json.loads((ROOT/'config/beta.json').read_text())
    assert cfg['gateway']['request_timeout_seconds'] == 330
    assert cfg['explanation']['provider'] == 'bedrock'
    assert cfg['guard_defaults']['max_cost_session_usd'] <= .5


def test_hosted_beta_token_cap_fits_the_real_catalog_and_skills():
    from finplan_agent.graph.policy import SYSTEM_PROMPT
    from finplan_agent.providers.base import GenerateRequest, ToolSpec, estimate_tokens
    from finplan_agent.skills import provider_tool_specs
    from infra.stacks.tool_policy import decide
    from infra.stacks.tool_schemas import contract_tools, tool_definition
    cfg = json.loads((ROOT/'config/beta.json').read_text())
    _, instructions = load_skills(ROOT/'skills')
    definitions = [tool_definition(t) for t in contract_tools() if decide(t, groups=['researcher'])]
    specs = tuple(ToolSpec(name=d.name,description=d.description,input_schema=d.input_schema) for d in definitions)
    request = GenerateRequest(purpose='plan',system=SYSTEM_PROMPT,stable_instructions=instructions,
                              messages=[{'role':'user','content':[{'text':'Recommend from my portfolio state.'}]}],
                              tools=provider_tool_specs(specs),max_tokens=cfg['guard_defaults']['max_tokens_invocation'])
    first_call = estimate_tokens(request.prompt_chars()) + request.max_tokens
    # Planning, a second pass after the tool result, and bounded narration must fit.
    assert cfg['guard_defaults']['max_tokens_turn'] >= 2 * first_call + 5000
    assert cfg['guard_defaults']['max_tokens_session'] >= cfg['guard_defaults']['max_tokens_turn']
