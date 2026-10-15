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
