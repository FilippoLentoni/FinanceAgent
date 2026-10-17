"""Deploy an explicit reviewed commit through Build/Beta, then stop before Gamma."""
import argparse
import json
import time
import re
from datetime import datetime, timezone
from pathlib import Path

import boto3

p=argparse.ArgumentParser()
p.add_argument('repo',choices=['financialplanning','financemodel','financelambdastool','financeagent'])
p.add_argument('commit')
a=p.parse_args()
if not re.fullmatch(r'[0-9a-f]{40}',a.commit):
    p.error('an exact reviewed 40-character commit is required')
client=boto3.client('codepipeline',region_name='us-east-2')
name=f'finplan-shared-{a.repo}-pipeline'
out=Path('/home/ec2-user/projects/.worktmp/beta-modeling/deployments')
out.mkdir(parents=True,exist_ok=True)
state=client.get_pipeline_state(name=name)
active=[e for e in client.list_pipeline_executions(pipelineName=name,maxResults=20)['pipelineExecutionSummaries'] if e['status'] in ('InProgress','Stopping')]
if active:
    raise RuntimeError('An execution is already active; inspect its owner before starting another beta deployment')
gamma=next(s for s in state['stageStates'] if s['stageName']=='Gamma')
restore=gamma['inboundTransitionState']['enabled']
(out/f'{a.repo}-transition-before.json').write_text(json.dumps({'enabled':restore,'stage':'Gamma'},indent=2))
execution=None
terminal=False

def log(message):
    print(datetime.now(timezone.utc).isoformat(), message, flush=True)


def execution_details():
    # StartPipelineExecution can return before its read API is consistent.
    for attempt in range(12):
        try:
            return client.get_pipeline_execution(pipelineName=name,pipelineExecutionId=execution)['pipelineExecution']
        except client.exceptions.PipelineExecutionNotFoundException:
            if attempt == 11:
                raise
            time.sleep(5)

try:
    if restore:
        client.disable_stage_transition(pipelineName=name,stageName='Gamma',transitionType='Inbound',reason='Owner-authorized beta-only modeling deployment; restore after stopping execution')
    execution=client.start_pipeline_execution(name=name,sourceRevisions=[{'actionName':'Source','revisionType':'COMMIT_ID','revisionValue':a.commit}])['pipelineExecutionId']
    (out/f'{a.repo}-execution.json').write_text(json.dumps({'pipeline':name,'execution':execution,'commit':a.commit,'restore_gamma_transition':restore},indent=2))
    log(f'Started {a.repo} {execution} at {a.commit}; Gamma held')
    previous=None
    deadline=time.monotonic()+5400
    while time.monotonic()<deadline:
        details=execution_details()
        actions=client.list_action_executions(pipelineName=name,filter={'pipelineExecutionId':execution})['actionExecutionDetails']
        summary=sorted((r['stageName'],r['actionName'],r['status']) for r in actions)
        if summary != previous:
            log(json.dumps(summary));previous=summary
        failures=[r for r in actions if r['status'] in ('Failed','Abandoned')]
        if failures or details['status'] in ('Failed','Superseded','Stopped'):
            terminal=details['status'] in ('Failed','Superseded','Stopped')
            raise RuntimeError(f'Execution failed/stopped: {details["status"]}; failed actions: {[(r["stageName"],r["actionName"],r.get("output",{})) for r in failures]}')
        beta=[r for r in actions if r['stageName']=='Beta']
        expected=len(next(s for s in client.get_pipeline(name=name)['pipeline']['stages'] if s['name']=='Beta')['actions'])
        if len(beta)==expected and all(r['status']=='Succeeded' for r in beta):
            log('All Beta actions succeeded; stopping before Gamma')
            client.stop_pipeline_execution(pipelineName=name,pipelineExecutionId=execution,abandon=False,reason='Beta-only modeling release deployed; never promote this execution')
            while time.monotonic()<deadline:
                status=execution_details()['status']
                if status in ('Stopped','Failed','Superseded'):
                    terminal=True;break
                time.sleep(5)
            if not terminal:raise TimeoutError('Pipeline stop did not complete')
            (out/f'{a.repo}-result.json').write_text(json.dumps({'execution':execution,'build':'Succeeded','beta':'Succeeded','gamma':'Not run','execution_status':status,'source_commit':a.commit},indent=2))
            log('Beta deployment succeeded; Gamma and Prod not run')
            break
        time.sleep(20)
    else:
        raise TimeoutError('Build/Beta deployment exceeded 90 minutes')
finally:
    # Never release a live beta-only execution into Gamma.
    if execution and not terminal:
        try:
            client.stop_pipeline_execution(pipelineName=name,pipelineExecutionId=execution,abandon=False,reason='Stop beta-only execution before restoring Gamma transition')
        except client.exceptions.PipelineExecutionNotStoppableException:
            pass
        for _ in range(60):
            status=execution_details()['status']
            if status in ('Stopped','Failed','Superseded','Succeeded'):
                terminal=True;break
            time.sleep(5)
    if restore and (not execution or terminal):
        client.enable_stage_transition(pipelineName=name,stageName='Gamma',transitionType='Inbound')
        log('Restored the original Gamma transition setting')
    elif restore:
        log('Execution not terminal: Gamma remains held to prevent an unintended deployment')
