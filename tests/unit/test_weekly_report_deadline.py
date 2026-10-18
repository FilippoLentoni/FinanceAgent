"""Report-only time allowance cannot expand native/model/provider budgets."""
from pathlib import Path
from configparser import ConfigParser
import pytest
from finplan_agent.modeling import jobs
from scripts.install_modeling_jobs import units

CFG={'platform_root':'/release/platform','runtime_python':'/release/python','runtime_root':'/runtime','project_root':'/project','experiment_id':'isolated'}
SCRIPT='/release/platform/scripts/modeling_report.py'

@pytest.mark.parametrize('flag',['--weekly','--repair-weekly-from-queue'])
def test_only_exact_configured_weekly_script_receives_report_bound(flag):
    assert jobs.command_deadline(CFG,['/release/python',SCRIPT,flag])==1500

@pytest.mark.parametrize('command',[
 ['/release/python',SCRIPT,'--finalized'],
 ['/release/python','/other/scripts/modeling_report.py','--weekly'],
 ['/other/python',SCRIPT,'--weekly'],
 ['/release/python','-c','pass',SCRIPT,'--weekly'],
 ['/release/python','-m','finplan_model.forward.hourly','--weekly'],
 ['vendor','--weekly'],['/release/python',SCRIPT,'--weekly=true'],
 ['/release/python',SCRIPT,'--output','/tmp/--weekly'],
])
def test_generic_flags_and_daily_native_commands_keep900(command):
    assert jobs.command_deadline(CFG,command)==900
    assert jobs.command_deadline({},command)==900


def test_weekly_and_repair_bounded_command_records_do_not_expand_native(tmp_path,monkeypatch):
    elapsed=[0.0];calls=[]
    monkeypatch.setattr(jobs.time,'monotonic',lambda:elapsed[0])
    def command(argv,**kwargs):
        calls.append(kwargs['timeout']);elapsed[0]+=kwargs['timeout']-1;return 0
    cfg={**CFG,'commands':{'weekly':[['/release/python',SCRIPT,'--weekly','--closed-week'],['/release/python',SCRIPT,'--weekly'],['/release/python','-m','finplan_model.forward.hourly']]}}
    result=jobs.run_job(cfg,'weekly','2026-10-18',tmp_path,execute_fn=command)
    assert calls==[1500,1500,900]
    assert [r['deadline_seconds']for r in result['commands']]==calls


def test_weekly_service_bound_and_other_service_bounds_unchanged(tmp_path):
    cfg={**CFG,'runtime_pythonpath':'/runtime/src','storage_pythonpath':'/platform','scratch_dir':str(tmp_path),'source_commits':{k:'a'*40 for k in ('financemodel','financialplanning','financeagent')}}
    for job,expected in {'weekly':3050,'hourly':3400,'eod':950,'research':1550,'snapshot':70}.items():
        service,_=units(job,cfg,tmp_path/'config.json',Path(cfg['runtime_python']));parser=ConfigParser(strict=False,interpolation=None);parser.read_string(service)
        assert parser.getint('Service','TimeoutStartSec')==expected


def test_weekly_timer_moves_after_hourly_without_changing_other_cadences():
    from scripts.install_modeling_jobs import CALENDARS
    assert CALENDARS['weekly']=='*-*-* 17:25:00 America/New_York'
    assert CALENDARS['eod']=='*-*-* 17:00:00 America/New_York'
    assert CALENDARS['hourly']=='*-*-* *:05:00 UTC'
    assert CALENDARS['research']=='*-*-* *:00,30:00 UTC'
    assert CALENDARS['snapshot']=='*-*-* *:*:00 UTC'
