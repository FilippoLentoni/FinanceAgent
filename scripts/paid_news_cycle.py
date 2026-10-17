"""Expiring single-host premarket research collection; no model promotion."""
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'agent')]
from finplan_agent.payments.execution import PrivateLedger
from finplan_agent.payments.news_trial import CONFIG, CAP, query_body, collection_slot
from finplan_agent.payments.news_publication import PROFILE_SYMBOLS
from scripts.paid_news_trial import DIRECTORY
from scripts.publish_paid_news import publish

CALENDAR_PYTHON=ROOT.parent/'FinancialPlanning/.venv/bin/python'


def stop_timer():
    subprocess.run(['systemctl','--user','disable','--now','finplan-etf-news.timer'],check=True,capture_output=True,timeout=15)


def market_open(now):
    # Research starts before the 09:00 price pipeline. The core exchange session
    # opens at 09:30; requiring an already-open market silently skipped 08:55.
    # Use the pipeline's shipped, versioned XNYS artifact (including early closes).
    code="""from datetime import datetime,time
import sys
from finplan_platform.core.calendar import xnys_calendar
cal=xnys_calendar();t=datetime.fromisoformat(sys.argv[1]);day=cal.local_date(t)
cal.require_covered(day)
local=t.astimezone(__import__('zoneinfo').ZoneInfo(cal.timezone))
print('yes' if cal.status(day) in ('regular','early_close') and local.time()>=time(8,45) and t<cal.session_close_utc(day) else 'no')
"""
    result=subprocess.run([str(CALENDAR_PYTHON),'-c',code,now.isoformat()],capture_output=True,text=True,check=True,timeout=30)
    return result.stdout.strip()=='yes'


def readiness(*, now, outcome, directory=None):
    """Freeze each attempt's actual collection evidence for the report runner.

    A reserved slot is not a successful delivery; central publication is not
    training permission. Never infer either from a successful process exit.
    """
    ledger=PrivateLedger(directory or DIRECTORY)
    config=ledger.read(CONFIG)
    profiles=config.get('collection_plan',{}).get('profiles',['ETF_MARKET','GOOGL'])
    rows=[]
    import hashlib
    for profile in profiles:
        identifier=hashlib.sha256(collection_slot(profile,query_body(profile),now,config).encode()).hexdigest()[:32]
        record=ledger.read(identifier) if ledger.path(identifier).exists() else {}
        manifest=(record.get('central_curated_response') or record.get('central_import_response') or {}).get('manifest',{})
        published=bool(record.get('central_publication_complete') and manifest.get('record_id'))
        rows.append({'profile':profile,'receipt_id':identifier if record else None,
                     'delivery_status':record.get('status','not_collected'),
                     'central_publication_complete':published,
                     'record_id':manifest.get('record_id'),'available_at':manifest.get('available_at'),
                     'retrieved_at':manifest.get('retrieved_at'),
                     'eligible_for_research':bool(manifest.get('eligible')),
                     'eligible_for_training':bool(manifest.get('eligible') and manifest.get('rights',{}).get('status')=='research_and_training'),
                     'exclusion_reasons':manifest.get('exclusion_reasons',[])})
    return {'decision_session':now.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
            'captured_at':now.isoformat(),'cycle_status':outcome['status'],'profiles':rows,
            'all_profiles_published':bool(rows) and all(r['central_publication_complete'] for r in rows),
            'all_profiles_training_eligible':bool(rows) and all(r['eligible_for_training'] for r in rows),
            'historical_coverage':'forward_collection_only'}


def cycle(*,now=None):
    now=now or datetime.now(UTC)
    ledger=PrivateLedger(DIRECTORY)
    config=ledger.read(CONFIG)
    if config.get('started_at') is None:
        return {'status':'not_started'}
    if now>=datetime.fromisoformat(config['expires_at']):
        stop_timer()
        return {'status':'expired','purchases':0}
    # Retry publication from its frozen request without another merchant purchase.
    for path in sorted(DIRECTORY.glob('*.json')):
        record=json.loads(path.read_text())
        if record.get('profile') in PROFILE_SYMBOLS and record.get('status')=='received_unvalidated' and not record.get('central_publication_complete'):
            publish(path.stem)
    if not market_open(now):
        return {'status':'market_closed','purchases':0}
    reserved=sum(int(json.loads(path.read_text()).get('reserved_atomic',0)) for path in DIRECTORY.glob('*.json'))
    if reserved>=CAP:
        stop_timer()
        return {'status':'cap_exhausted','purchases':0}
    profiles=config.get('collection_plan',{}).get('profiles',['ETF_MARKET','GOOGL'])
    if not profiles or len(profiles)!=len(set(profiles)) or any(p not in PROFILE_SYMBOLS for p in profiles):
        stop_timer()
        return {'status':'collection_stopped','reason':'invalid_collection_profiles','receipts':[]}
    receipts=[]
    import hashlib
    for profile in profiles:
        slot=collection_slot(profile, query_body(profile), now, config)
        identifier=hashlib.sha256(slot.encode()).hexdigest()[:32]
        if ledger.path(identifier).exists():
            continue
        try:
            result=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/paid_news_trial.py'),'collect','--asset',profile],capture_output=True,text=True,timeout=150)
        except subprocess.TimeoutExpired:
            stop_timer()
            return {'status':'collection_stopped','reason':'unknown_outcome_requires_reconciliation','receipts':receipts}
        if result.returncode:
            # Funding, project budget and uncertain outcomes all require review.
            stop_timer()
            return {'status':'collection_stopped','reason':'preflight_or_collection_failed_requires_review','receipts':receipts}
        data=json.loads(result.stdout)
        publish(data['receipt_id'])
        receipts.append({'profile':profile,'receipt_id':data['receipt_id'],'reserved_usd':data['reserved_usd']})
    return {'status':'collected_and_published' if receipts else 'slots_already_reserved','receipts':receipts}



if __name__=='__main__':
    now=datetime.now(UTC)
    result=cycle(now=now)
    report=readiness(now=now,outcome=result)
    output=ROOT.parent/'.worktmp/news-backfill/daily-status'/report['decision_session']
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=output/(datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    # An immutable attempt is auditable even when an earlier slot is uncertain.
    with path.open('x') as stream:json.dump(report,stream,indent=2)
    print(json.dumps({**result,'readiness_report':str(path)}))
