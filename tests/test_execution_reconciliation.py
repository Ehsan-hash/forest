"""Unreadable receipts cannot make still-running work free or restart it."""
import datetime as dt
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from research.agents.processes import ManagedProcesses,is_alive
from services.worker.main import _waiting_outcome_resource,_reconcile_execution_hold
from services.worker.scheduler import _elapsed_seconds_at,_time_budget_reservation_remaining
from test_agent_processes import completed
from test_worker import Harness,wait_until


@pytest.mark.parametrize('cancel_error',[False,True])
def test_unreadable_completion_charges_live_work_and_fuses_actual_timeout(tmp_path,monkeypatch,cancel_error):
    workspace=tmp_path/'workspace';workspace.mkdir()
    manager=ManagedProcesses(workspace)
    job=manager.start([sys.executable,'-c','import time;time.sleep(20)'],process_id='owned')
    assert is_alive(job['pid'],job['process_created'])
    receipt=manager.folder(job['process_id'])/'completion.json';receipt.write_text('{unreadable')
    assert manager.inspect(job['process_id'])['status']=='reconciliation'
    resource=_waiting_outcome_resource({'elapsed_seconds':.1},{'status':'waiting_input',
        'wait_for':{'type':'execution_reconciliation','process_id':job['process_id'],'workspace':str(workspace)}})
    run=SimpleNamespace(status='waiting_input',pid=None,process_created=None,config={'timeout':.4},
        resource={**resource,'time_budget':{'effective_total_timeout_seconds':.4}},error=None,exit_code=None)
    cancellations=[]
    original_cancel=ManagedProcesses.cancel_all
    if cancel_error:
        def failed_cancel(self):
            cancellations.append(True);raise OSError('Original stop acknowledgement unavailable')
        monkeypatch.setattr(ManagedProcesses,'cancel_all',failed_cancel)
    try:
        time.sleep(.15)
        now=dt.datetime.now(dt.timezone.utc)
        assert _elapsed_seconds_at(run,now)>=.25
        assert _time_budget_reservation_remaining(run,now)<=.15
        _reconcile_execution_hold(run,tmp_path)
        assert run.resource['execution_reconciliation_compute_hold']
        assert is_alive(job['pid'],job['process_created'])
        time.sleep(.2)
        _reconcile_execution_hold(run,tmp_path)
        assert run.resource['hard_budget_exhausted']['kind']=='seconds'
        if cancel_error:
            assert run.resource['reconciliation_cancel_error']
            assert run.status=='waiting_input'
            assert is_alive(job['pid'],job['process_created'])
            _reconcile_execution_hold(run,tmp_path)
            assert len(cancellations)==1
            assert run.resource['execution_reconciliation_compute_hold']
        else:
            assert completed(manager,job['process_id'])['status']=='cancelled'
            _reconcile_execution_hold(run,tmp_path)
            assert run.status=='budget_exhausted' and run.exit_code==124
            assert .4<=run.resource['elapsed_seconds']<=1.0
            assert not run.resource.get('execution_reconciliation_compute_hold')
            assert not run.config.get('_next_attempt')
    finally:
        monkeypatch.setattr(ManagedProcesses,'cancel_all',original_cancel)
        manager.cancel(job['process_id'])
        completed(manager,job['process_id'])


@pytest.mark.parametrize('mode',['hard_timeout','receipt_recovered'])
def test_actual_worker_reconciliation_hold_enforces_original_hard_timeout(tmp_path,mode):
    h=Harness(tmp_path)
    try:
        h.start_api();h.start_worker()
        project=h.request('POST','/api/projects',json={'name':'Original receipt reconciliation',
            'mode':'manual','budget':{'seconds':20}})
        from test_research_verification_workflow import add_node
        node=add_node(h,project,title='Actual isolated job',type='task',config={'kind':'command',
            'execution_backend':'bubblewrap','timeout':2 if mode=='hard_timeout' else 8,'command':[sys.executable,'-c',
                "from pathlib import Path;import time;p=Path('launch-count.txt');p.write_text(str(int(p.read_text())+1) if p.exists() else '1');Path('started.txt').write_text('one launch');time.sleep("+
                ('20' if mode=='hard_timeout' else '1')+
                ");Path('completed.txt').write_text('actual completion')"]})
        run=h.launch(node);output=h.output(run)
        def running_job():
            for state in output.glob('.forest-processes/workspace/*/state.json'):
                value=json.loads(state.read_text())
                if value.get('status')=='running' and (output/'workspace'/'started.txt').exists():
                    return state.parent,json.loads((state.parent/'identity.json').read_text())
        folder,identity=wait_until(running_job,timeout=15)
        assert is_alive(identity['pid'],identity['process_created'])
        (folder/'completion.json').write_text('{unreadable completion')
        blocked=wait_until(lambda:(row if (row:=h.run(run))['status']=='waiting_input' and
            row['resource'].get('execution_reconciliation_compute_hold') else None),timeout=10)
        assert blocked['resource']['waiting_started_at']
        assert len(blocked['resource']['attempts'])==1
        if mode=='receipt_recovered':
            final=h.terminal(run,timeout=15)
            assert final['status']=='completed',final
            assert 1<=final['resource']['elapsed_seconds']<8
            assert len(final['resource']['attempts'])==2
            assert final['resource']['attempts'][-1]['mode']=='continue'
            assert (output/'workspace'/'completed.txt').read_text()=='actual completion'
            assert (output/'workspace'/'launch-count.txt').read_text()=='1'
            assert len(list(output.glob('.forest-processes/workspace/*/request.json')))==1
            return
        final=wait_until(lambda:(row if (row:=h.run(run))['status']=='budget_exhausted' else None),timeout=15)
        assert final['exit_code']==124
        assert 2<=final['resource']['elapsed_seconds']<=3.2
        assert final['resource']['hard_budget_exhausted']['kind']=='seconds'
        assert not final['resource'].get('execution_reconciliation_compute_hold')
        assert not is_alive(identity['pid'],identity['process_created'])
        assert json.loads((folder/'completion.json').read_text())['status']=='cancelled'
        assert (output/'workspace'/'started.txt').read_text()=='one launch'
        assert (output/'workspace'/'launch-count.txt').read_text()=='1'
        assert not (output/'workspace'/'completed.txt').exists()
        assert len(h.request('GET',f"/api/projects/{project['id']}/runs"))==1
        time.sleep(.3)
        assert len(h.run(run)['resource']['attempts'])==1
    finally:h.cleanup()
