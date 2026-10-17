"""Expiring single-host ETF research collection; no trading or model promotion."""
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'agent')]
from finplan_agent.payments.execution import PrivateLedger
from finplan_agent.payments.news_trial import CONFIG, CAP
from scripts.paid_news_trial import DIRECTORY
from scripts.publish_paid_news import publish

CALENDAR_PYTHON=ROOT.parent/'FinancialPlanning/.venv/bin/python'


def stop_timer():
    subprocess.run(['systemctl','--user','disable','--now','finplan-etf-news.timer'],check=True,capture_output=True,timeout=15)


def market_open(now):
    code="""import exchange_calendars as xc, pandas as pd, sys
cal=xc.get_calendar('XNYS')
t=pd.Timestamp(sys.argv[1]);day=t.tz_convert('America/New_York').date().isoformat()
print('yes' if cal.is_session(day) and cal.session_open(day)<=t<cal.session_close(day) else 'no')
"""
    result=subprocess.run([str(CALENDAR_PYTHON),'-c',code,now.isoformat()],capture_output=True,text=True,check=True,timeout=30)
    return result.stdout.strip()=='yes'


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
        if record.get('profile')=='ETF_MARKET' and record.get('status')=='received_unvalidated' and not record.get('central_import_response'):
            publish(path.stem)
    if not market_open(now):
        return {'status':'market_closed','purchases':0}
    reserved=sum(int(json.loads(path.read_text()).get('reserved_atomic',0)) for path in DIRECTORY.glob('*.json'))
    if reserved>=CAP:
        stop_timer()
        return {'status':'cap_exhausted','purchases':0}
    slot=now.strftime('%Y%m%d%H')+'-ETF_MARKET'
    import hashlib
    identifier=hashlib.sha256(slot.encode()).hexdigest()[:32]
    if ledger.path(identifier).exists():
        return {'status':'slot_already_reserved','purchases':0}
    try:
        result=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/paid_news_trial.py'),'collect','--asset','ETF_MARKET'],capture_output=True,text=True,timeout=150)
    except subprocess.TimeoutExpired:
        stop_timer()
        return {'status':'collection_stopped','reason':'unknown_outcome_requires_reconciliation'}
    if result.returncode:
        # Funding, project budget and uncertain outcomes all require operator review.
        # Never keep automatically invoking the payment path after a failed attempt.
        stop_timer()
        return {'status':'collection_stopped','reason':'preflight_or_collection_failed_requires_review'}
    data=json.loads(result.stdout)
    publish(data['receipt_id'])
    return {'status':'collected_and_published','receipt_id':data['receipt_id'],'reserved_usd':data['reserved_usd']}


if __name__=='__main__':
    print(json.dumps(cycle()))
