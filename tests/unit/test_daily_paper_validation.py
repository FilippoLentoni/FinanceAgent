"""The unattended synthetic test driver cannot authorize an unrelated portfolio."""
from scripts.daily_paper_validation import Driver
import pytest


def driver():
    value=Driver.__new__(Driver)
    value.config={'books':[{'portfolio_id':'isolated-book'}]}
    return value


def test_resolution_refuses_a_book_outside_allowlist_before_any_call():
    value=driver()
    value.direct=lambda *args:pytest.fail('must not read or resolve an unrelated book')
    with pytest.raises(ValueError,match='allowlist'):
        value.resolve('test','decision','customer-book',1,'accept')


def test_resolution_refuses_changed_confirmation_without_confirming():
    value=driver()
    value.direct=lambda *args:{'decision':{'portfolio_id':'isolated-book','portfolio_revision':1,'synthetic':True,'status':'proposed'}}
    calls=[]
    def hosted(label,payload,**kwargs):
        calls.append(payload)
        assert payload.get('action')!='confirm'
        return {'confirmation':{'calls':[{'tool':'resolve_portfolio_decision','arguments':{'decision_id':'different'}}]}},'test-session'
    value.hosted=hosted
    with pytest.raises(ValueError,match='confirmation changed'):
        value.resolve('test','decision','isolated-book',1,'accept')
    assert len(calls)==1


def test_resolution_retry_does_not_confirm_an_already_committed_decision():
    value=driver()
    value.direct=lambda *args:{'decision':{'portfolio_id':'isolated-book','portfolio_revision':1,'synthetic':True,'status':'accepted'}}
    value.hosted=lambda *args,**kwargs:pytest.fail('no duplicate confirmation')
    value.resolve('test','decision','isolated-book',1,'accept')
