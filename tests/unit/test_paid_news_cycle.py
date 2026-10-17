from datetime import UTC,datetime,timedelta
import json

from finplan_agent.payments.execution import PrivateLedger
from finplan_agent.payments.news_trial import CONFIG
from scripts import paid_news_cycle as cycle


def test_expired_cycle_disables_timer_without_publishing_or_buying(tmp_path,monkeypatch):
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,23,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':(now-timedelta(days=8)).isoformat(),'expires_at':(now-timedelta(days=1)).isoformat()})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path)
    calls=[];monkeypatch.setattr(cycle.subprocess,'run',lambda args,**kwargs:calls.append(args))
    monkeypatch.setattr(cycle,'publish',lambda _:(_ for _ in ()).throw(AssertionError('must not publish')))
    result=cycle.cycle(now=now)
    assert result=={'status':'expired','purchases':0} and calls==[['systemctl','--user','disable','--now','finplan-etf-news.timer']]


def test_pending_delivery_is_published_before_market_closed_exit(tmp_path,monkeypatch):
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,15,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':now.isoformat(),'expires_at':(now+timedelta(days=7)).isoformat()})
    ident='1'*32;ledger.write(ident,{'profile':'ETF_MARKET','status':'received_unvalidated'})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:False)
    calls=[];monkeypatch.setattr(cycle,'publish',calls.append)
    result=cycle.cycle(now=now)
    assert calls==[ident] and result=={'status':'market_closed','purchases':0}


def test_exhausted_cap_stops_before_external_collection(tmp_path,monkeypatch):
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,15,15,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':now.isoformat(),'expires_at':(now+timedelta(days=7)).isoformat()})
    ledger.write('2'*32,{'reserved_atomic':cycle.CAP,'status':'delivery_unknown'})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:True)
    stopped=[];monkeypatch.setattr(cycle,'stop_timer',lambda:stopped.append(True))
    monkeypatch.setattr(cycle.subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not collect')))
    assert cycle.cycle(now=now)=={'status':'cap_exhausted','purchases':0}
    assert stopped==[True]


def test_failed_collection_disables_recurring_attempts_and_preserves_reservation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,15,15,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':now.isoformat(),'expires_at':(now+timedelta(days=7)).isoformat()})
    ledger.write('2'*32,{'reserved_atomic':11000,'status':'delivery_unknown'})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:True)
    stopped=[];monkeypatch.setattr(cycle,'stop_timer',lambda:stopped.append(True))
    monkeypatch.setattr(cycle.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert cycle.cycle(now=now)['status']=='collection_stopped'
    assert stopped==[True] and ledger.read('2'*32)['status']=='delivery_unknown'


def test_market_slot_collects_separate_fixed_market_and_company_profiles(tmp_path,monkeypatch):
    from types import SimpleNamespace
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,15,15,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':now.isoformat(),'expires_at':(now+timedelta(days=7)).isoformat()})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:True)
    collected=[];published=[]
    def run(args,**kwargs):
        collected.append(args[-1])
        return SimpleNamespace(returncode=0,stdout=json.dumps({'receipt_id':str(len(collected))*32,'reserved_usd':'0.011'}))
    monkeypatch.setattr(cycle.subprocess,'run',run);monkeypatch.setattr(cycle,'publish',published.append)
    result=cycle.cycle(now=now)
    assert collected==['ETF_MARKET','GOOGL'] and len(published)==2
    assert result['status']=='collected_and_published' and len(result['receipts'])==2


def test_daily_plan_duplicate_hours_do_not_buy_again(tmp_path,monkeypatch):
    import hashlib
    from finplan_agent.payments.news_trial import collection_slot,query_body
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,15,15,tzinfo=UTC)
    config={'started_at':now.isoformat(),'expires_at':(now+timedelta(days=14)).isoformat(),
            'collection_plan':{'cadence':'daily'}}
    ledger.write(CONFIG,config)
    for profile in ('ETF_MARKET','GOOGL'):
        slot=collection_slot(profile,query_body(profile),now,config)
        ledger.write(hashlib.sha256(slot.encode()).hexdigest()[:32],{'profile':profile,'status':'delivery_unknown','reserved_atomic':11000})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:True)
    monkeypatch.setattr(cycle.subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(AssertionError('duplicate purchase')))
    assert cycle.cycle(now=now+timedelta(hours=3))['status']=='slots_already_reserved'


def test_configured_eight_asset_universe_collects_each_profile_once(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from finplan_agent.payments.news_trial import DAILY_UNIVERSE
    ledger=PrivateLedger(tmp_path);now=datetime(2026,10,19,13,35,tzinfo=UTC)
    ledger.write(CONFIG,{'started_at':now.isoformat(),'expires_at':(now+timedelta(days=14)).isoformat(),'collection_plan':{'cadence':'daily','profiles':list(DAILY_UNIVERSE)}})
    monkeypatch.setattr(cycle,'DIRECTORY',tmp_path);monkeypatch.setattr(cycle,'market_open',lambda _:True)
    collected=[];published=[]
    def run(args,**kwargs):
        collected.append(args[-1]);return SimpleNamespace(returncode=0,stdout=json.dumps({'receipt_id':format(len(collected),'032x'),'reserved_usd':'0.011'}))
    monkeypatch.setattr(cycle.subprocess,'run',run);monkeypatch.setattr(cycle,'publish',published.append)
    result=cycle.cycle(now=now)
    assert collected==list(DAILY_UNIVERSE) and len(published)==8
    assert result['status']=='collected_and_published'
