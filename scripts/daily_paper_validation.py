"""Expiring beta-only synthetic-book acceptance test; never an investor approval policy."""
from __future__ import annotations
import argparse,fcntl,json,math,sys,time,urllib.error,urllib.parse,urllib.request,uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'agent')]
from finplan_agent.tools.mcp_client import GatewayMcpClient
from scripts.beta_login import NoRedirect,save_token,validate_metadata


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,default=str)+'\n');temp.replace(path)


def token_for(session,config):
    path=Path(config['token_file']).expanduser()
    if path.stat().st_mode & 0o077:raise ValueError('token file must be private')
    token=json.loads(path.read_text())
    if token.get('expires_at',0)>time.time()+600:return token['access_token']
    meta=json.loads(session.client('ssm').get_parameter(Name='/finplan/beta/financeagent/agent/authorizer-metadata-ref')['Parameter']['Value']);validate_metadata(meta)
    body=urllib.parse.urlencode({'grant_type':'refresh_token','client_id':meta['public_client_id'],'refresh_token':token['refresh_token']}).encode()
    req=urllib.request.Request(meta['token_endpoint'],data=body,headers={'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as r:new=json.load(r)
    if new.get('token_type','').lower()!='bearer' or type(new.get('expires_in')) is not int:raise ValueError('invalid refresh response')
    new['refresh_token']=new.get('refresh_token',token['refresh_token']);new['expires_at']=int(time.time())+new['expires_in'];save_token(new,str(path));return new['access_token']


class Driver:
    def __init__(self,session,config,output):
        self.session,self.config,self.output=session,config,output;self.token=token_for(session,config);self.checks=[]
        self.ssm=session.client('ssm')
        self.primary=GatewayMcpClient(self.param('gateway-endpoint-ref'),lambda:self.token,timeout_seconds=330,forward_identity=True)
        self.classical=GatewayMcpClient(self.param('classical-gateway-endpoint-ref'),lambda:self.token,timeout_seconds=330,forward_identity=True)
    def param(self,key):return self.ssm.get_parameter(Name='/finplan/beta/financeagent/agent/'+key)['Parameter']['Value']
    def record(self,label,data):
        if self.token in json.dumps(data,default=str):raise ValueError('credential in receipt')
        save(self.output/(label+'.json'),data);print(label,flush=True)
    def direct(self,label,tool,args):
        client=self.classical if tool in {'recommend_classical_portfolio','run_recursive_improvement','research_market_events','submit_portfolio_feedback'} else self.primary
        t=time.monotonic();out=client.call_tool(tool,args);self.record(label,{'tool':tool,'arguments':args,'seconds':time.monotonic()-t,**asdict(out)})
        if not out.ok:raise RuntimeError(tool+': '+str((out.error or {}).get('code')))
        return out.result
    def hosted(self,label,payload,tool=None,sid=None,status='completed'):
        sid=sid or 'paper_validation_'+uuid.uuid4().hex
        url='https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/'+urllib.parse.quote(self.param('runtime-ref'),safe='')+'/invocations?qualifier=DEFAULT'
        req=urllib.request.Request(url,data=json.dumps({**payload,'stream':False}).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','X-Amzn-Bedrock-AgentCore-Runtime-Session-Id':sid})
        t=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=420) as r:final=json.load(r)
        except urllib.error.HTTPError as error:
            self.record(label,{'payload':payload,'session_id':sid,'seconds':time.monotonic()-t,'http_status':error.code,'error_body':error.read(65536).decode(errors='replace')})
            raise RuntimeError(label+': hosted HTTP '+str(error.code)) from None
        self.record(label,{'payload':payload,'session_id':sid,'seconds':time.monotonic()-t,'final':final})
        answer=final.get('answer') or {};evidence=answer.get('evidence') or []
        ok=final.get('status')==status
        if status=='completed':ok=ok and bool(answer.get('claim_check',{}).get('passed')) and all(x.get('ok') for x in evidence)
        if tool:ok=ok and any(x.get('tool')==tool and x.get('ok') for x in evidence)
        self.checks.append({'case':label,'passed':ok,'status':final.get('status'),'required_tool':tool,'tools':[x.get('tool')for x in evidence],'narrative_characters':len(answer.get('narrative',''))});save(self.output/'checks.json',self.checks)
        if not ok:raise RuntimeError(label+': hosted answer did not satisfy the requested workflow')
        return final,sid
    def book(self,label,pid):
        if pid not in {b['portfolio_id']for b in self.config['books']}:raise ValueError('book outside test allowlist')
        head=self.direct(label,'get_portfolio_history',{'portfolio_id':pid,'limit':3})['history'][0]
        if head.get('synthetic')is not True or head['paper_state'].get('mode')!='paper':raise ValueError('only synthetic paper books may be tested')
        return head
    def resolve(self,label,did,pid,revision,action):
        if pid not in {b['portfolio_id'] for b in self.config['books']}:raise ValueError('resolution outside isolated test allowlist')
        stored=self.direct(label+'-stored','get_portfolio_decision',{'decision_id':did})['decision']
        if stored['portfolio_id']!=pid or stored['portfolio_revision']!=revision or stored.get('synthetic')is not True:raise ValueError('decision outside authorized test context')
        if stored['status']==('accepted'if action=='accept'else'rejected'):return
        pending,sid=self.hosted(label+'-review',{'prompt':action.title()+' '+did},status='awaiting_confirmation')
        calls=pending.get('confirmation',{}).get('calls',[])
        if len(calls)!=1 or calls[0].get('tool',calls[0].get('name'))!='resolve_portfolio_decision':raise ValueError('unexpected confirmation tool')
        args=calls[0]['arguments']
        if any(args.get(k)!=v for k,v in {'portfolio_id':pid,'decision_id':did,'expected_revision':revision,'action':action}.items()):raise ValueError('confirmation changed the reviewed test decision')
        # Simulate the authorized test user's confirmation only on the allowlisted synthetic book.
        self.hosted(label+'-confirm',{'action':'confirm','approve':True},tool='resolve_portfolio_decision',sid=sid)


def validate_recommendation(result,pid,revision):
    rec=result['recommendation'];state=rec['portfolio_state']
    if result.get('synthetic') is not True or state.get('portfolio_id')!=pid or state.get('revision')!=revision:
        raise ValueError('recommendation does not match isolated book revision')
    outcome=rec.get('constraint_outcome',{})
    if rec.get('solution_status') not in {'feasible','optimal'} or outcome.get('violations') or outcome.get('action') not in {'accepted','projected'}:
        raise ValueError('recommendation failed feasibility or constraint checks')
    weights=[x['weight'] for x in rec['target_weights']]+[rec['cash_weight']]
    if any(not isinstance(w,(int,float)) or not math.isfinite(w) or w<0 or w>1 for w in weights) or abs(sum(weights)-1)>1e-6:
        raise ValueError('invalid portfolio weights')
    if not rec.get('input_snapshot_id') or not rec.get('as_of') or not rec.get('snapshot_checksum'):
        raise ValueError('missing frozen market evidence')
    if any(x.get('price_as_of')!=rec['as_of'] or x.get('reference_price',0)<=0 for x in rec.get('decisions',[])):
        raise ValueError('inconsistent reference prices')


def main():
    import boto3
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--report-only',action='store_true');args=parser.parse_args()
    config=json.loads(args.config.read_text());today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    if config.get('environment')!='beta' or not config.get('isolated_test_authorization') or not config['start_date']<=today<=config['end_date']:raise ValueError('outside the authorized beta test window')
    output=Path(config['output_dir']).expanduser()/today;output.mkdir(parents=True,exist_ok=True)
    with (output/'run.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        driver=Driver(boto3.Session(region_name='us-east-2'),config,output/'attempts'/uuid.uuid4().hex);summary={'date':today,'scope':'isolated_beta_paper_test','profitability_proven':False,'books':[],'failures':[]}
        for book in config['books']:
            name,pid=book['name'],book['portfolio_id']
            try:
                before=driver.book(name+'-before',pid);progress_path=output/(name+'-progress.json');progress=json.loads(progress_path.read_text())if progress_path.exists()else{}
                if progress.get('complete'):
                    driver.hosted(name+'-completed-explain',{'prompt':'Why does decision '+progress['decision_id']+' recommend this action for Google?'},tool='explain_portfolio_decision')
                    driver.hosted(name+'-completed-deep-dive',{'prompt':'Deep dive into performance and news for '+progress['decision_id']},tool='evaluate_portfolio_decision')
                    if before['revision']!=progress['after_revision']:raise ValueError('completed test book changed unexpectedly')
                    summary['books'].append(progress);continue
                previous=driver.direct(name+'-previous','list_portfolio_decisions',{'portfolio_id':pid,'limit':10})['decisions']
                for old in previous[:2]:
                    if old['decision_id'] not in progress.get('evaluated_ids',[]):
                        driver.hosted(name+'-evaluate-'+old['decision_id'],{'prompt':'Evaluate the strategy horizon and observed paper performance for '+old['decision_id']},tool='evaluate_portfolio_decision')
                        progress.setdefault('evaluated_ids',[]).append(old['decision_id']);save(progress_path,progress)
                if args.report_only:continue
                tool='recommend_portfolio'if book['strategy']=='ppo'else'recommend_classical_portfolio';request={'portfolio_id':pid}
                if tool=='recommend_classical_portfolio':request['algorithm']=book['strategy']
                if not progress.get('decision_id'):
                    result=driver.direct(name+'-recommendation',tool,request);progress.update(name=name,portfolio_id=pid,decision_id=result['decision_id'],before_revision=before['revision'],snapshot=result['recommendation']['input_snapshot_id'],market_date=result['recommendation']['as_of']);save(progress_path,progress)
                if any(row.get('decision_id')!=progress['decision_id'] and row.get('status')=='accepted' and row.get('execution',{}).get('reference_date','')>=progress['market_date'] for row in previous):
                    raise ValueError('no newer completed market session; check ingestion or exchange holiday before another acceptance')
                did=progress['decision_id'];stored=driver.direct(name+'-acceptance-evidence','get_portfolio_decision',{'decision_id':did})['decision']
                validate_recommendation({'recommendation':stored['recommendation'],'synthetic':stored['synthetic']},pid,progress['before_revision'])
                hosted,_=driver.hosted(name+'-recommendation-hosted',{'tool_request':{'name':tool,'arguments':request}},tool=tool)
                issued=hosted.get('answer',{}).get('portfolio_decisions',[])
                matched=next((x['recommendation'] for x in issued if x.get('decision_id')==did),None)
                if matched!=stored['recommendation']:raise ValueError('hosted and direct MCP recommendation evidence differs')
                driver.hosted(name+'-explain',{'prompt':'Why does decision '+did+' recommend this action for Google?'},tool='explain_portfolio_decision')
                deep,_=driver.hosted(name+'-deep-dive',{'prompt':'Deep dive into performance and news for '+did},tool='evaluate_portfolio_decision')
                analyses=deep.get('answer',{}).get('portfolio_analyses',[])
                performance=next((a for a in analyses if a.get('analysis_kind')=='performance'),None)
                if not performance:raise ValueError('deep dive did not return persisted performance evidence')
                driver.direct(name+'-feedback','submit_portfolio_feedback',{'analysis_id':performance['analysis_id'],'idempotency_key':'daily-validation-'+name+'-'+today,'text':'Synthetic operational validation '+today+': hosted and MCP recommendation evidence, feasibility, explanation and performance checks passed. No profitability claim; no model change or paid experiment is authorized by this feedback.'})
                older=next((r for r in previous if r.get('algorithm')==book['strategy']and r.get('execution',{}).get('reference_date')!=progress['market_date']),None)
                if older:driver.hosted(name+'-comparison',{'prompt':'Compare decisions '+older['decision_id']+' and '+did},tool='compare_portfolio_decisions')
                if not progress.get('alternative_id'):
                    alt=driver.direct(name+'-reject-alternative','recommend_classical_portfolio',{'portfolio_id':pid,'algorithm':'min_variance','settings':{'max_weight':0.4},'idempotency_key':'test-reject-'+name+'-'+today});progress['alternative_id']=alt['decision_id'];save(progress_path,progress)
                if progress['alternative_id']==did:raise ValueError('reject alternative must differ from accepted proposal')
                driver.hosted(name+'-model-comparison',{'prompt':'Compare decisions '+did+' and '+progress['alternative_id']},tool='compare_portfolio_decisions')
                driver.resolve(name+'-reject',progress['alternative_id'],pid,progress['before_revision'],'reject')
                current=driver.book(name+'-after-reject',pid)
                accepted=driver.direct(name+'-accept-status','get_portfolio_decision',{'decision_id':did})['decision']['status']=='accepted'
                if current['revision']!=progress['before_revision']+(1 if accepted else 0):raise ValueError('rejection changed holdings or unrelated revision occurred')
                driver.resolve(name+'-accept',did,pid,progress['before_revision'],'accept');after=driver.book(name+'-after',pid)
                if after['revision']!=progress['before_revision']+1:raise ValueError('acceptance must create exactly one revision')
                next_result=driver.direct(name+'-next-state',tool,request)
                if next_result['recommendation']['portfolio_state']['revision']!=after['revision']:raise ValueError('next inference did not read accepted holdings')
                progress.update(complete=True,after_revision=after['revision']);save(progress_path,progress);summary['books'].append(progress)
            except Exception as error:summary['failures'].append({'book':name,'error':str(error)})
        summary['checks']=driver.checks;summary['passed']=not summary['failures']and(args.report_only or len(summary['books'])==len(config['books']));save(output/'summary.json',summary)
        print(json.dumps({'date':today,'passed':summary['passed'],'completed_books':len(summary['books']),'failures':summary['failures']}),flush=True);return 0 if summary['passed']else 1


if __name__=='__main__':raise SystemExit(main())
