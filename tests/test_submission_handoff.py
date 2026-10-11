"""Real worker submissions settle allocation before child compute starts.

Deterministic model actions are test inputs only. The child actually fits and
validates a model; no fixture response substitutes for those computations.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest
from test_worker import Harness, wait_until


SUBMISSION_AGENT = '''
from copy import deepcopy
from pathlib import Path
import json
import os
from research.agents import runtime
from services.api.db import Session,TaskRun

class SubmissionModelFixture:
    native_tools=False
    api='ollama'
    request_guard=True
    def __init__(self,provider,credential=None,allow_paid=False):
        self.config=deepcopy(provider.get('config',{}))
    def _discover_local_capabilities(self):pass
    def build_request(self,messages,tools=None,max_output_tokens=None):
        return 'test-fixture',{'messages':messages,'tools':tools}
    def complete(self,messages,**kwargs):
        submitted=any('SUBMISSION SETTLED' in str(message.get('content','')) for message in messages)
        action={'tool':'finish','arguments':{'summary':'Observed real submitted receipt','artifacts':[]}} if submitted else {
            **self.config.get('submission',{'tool':'experiment_run','arguments':{'entrypoint':'fitted_child:run','parameters':{'delay':self.config.get('child_delay',0)}}})}
        return {'model':'deterministic-submission-fixture','text':json.dumps(action),
            'usage':{'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'reasoning_tokens':0,'cost':None}}

def run(parameters,output):
    folder=Path(output)
    if parameters.get('defer_exit'):
        import atexit,time
        atexit.register(time.sleep,2)
    with Session() as session:config=deepcopy(session.get(TaskRun,folder.name).config)
    if parameters.get('child_backend'):config['execution_backend']=parameters['child_backend']
    config['provider_snapshot']={'kind':'codex_cli','model':'deterministic-submission-fixture',
        'config':{'child_delay':parameters.get('child_delay',0)}}
    if parameters.get('submission'):config['provider_snapshot']['config']['submission']=parameters['submission']
    original=runtime.ModelClient
    runtime.ModelClient=SubmissionModelFixture
    submit=runtime.ToolRuntime.submit
    if parameters.get('crash_after_intent'):
        def crash(self,*args,**kwargs):
            value=submit(self,*args,**kwargs)
            (folder/'intent-crash.txt').write_text('one accepted intent')
            os._exit(17)
        runtime.ToolRuntime.submit=crash
    try:return runtime.run_agent(folder.name,folder/'workspace',config)
    finally:
        runtime.ModelClient=original
        runtime.ToolRuntime.submit=submit
'''

FITTED_CHILD = '''
from pathlib import Path
import json,time
import numpy as np
def run(parameters,output):
    time.sleep(parameters.get('delay',0))
    rng=np.random.default_rng(7)
    x=rng.normal(size=24);y=1.7*x-.4+rng.normal(scale=.03,size=24)
    design=np.column_stack([x[:18],np.ones(18)])
    slope,intercept=np.linalg.lstsq(design,y[:18],rcond=None)[0]
    mse=float(np.mean((slope*x[18:]+intercept-y[18:])**2))
    metrics={'slope':float(slope),'intercept':float(intercept),'validation_mse':mse,'train_samples':18,'validation_samples':6}
    folder=Path(output)
    (folder/'real-fit.json').write_text(json.dumps(metrics))
    (folder/'execution-count.txt').write_text('one actual fit')
    return metrics
'''


@pytest.mark.parametrize('failure',['none','executor_crash','worker_restart','hard_budget','parent_cancel','source_timeout','sandbox'])
def test_experiment_submission_keeps_parent_and_shared_actual_budget(tmp_path,failure):
    h=Harness(tmp_path)
    try:
        h.start_api();h.start_worker()
        limit=6 if failure=='hard_budget' else 120
        project=h.request('POST','/api/projects',json={'name':'Durable child allocation',
            'goal':'Fit and validate through a durable submission','mode':'manual','budget':{'seconds':limit}})
        graph=h.request('GET',f"/api/projects/{project['id']}/graph")
        branch=graph['branches'][0]
        root=h.directory/'data'/'projects'/project['id']/branch['workspace']
        root.mkdir(parents=True,exist_ok=True)
        (root/'submission_agent.py').write_text(SUBMISSION_AGENT)
        (root/'fitted_child.py').write_text(FITTED_CHILD)
        source_id=str(uuid.uuid4())
        added=h.request('POST',f"/api/projects/{project['id']}/graph/commands",json={
            'request_id':str(uuid.uuid4()),'expected_revision':graph['revision'],'operation':'add_node',
            'params':{'id':source_id,'branch_id':branch['id'],'type':'experiment','title':'Submission parent',
                'instructions':'Submit the real fit and inspect its receipt',
                'config':{'kind':'experiment','entrypoint':'submission_agent:run','role':'Researcher','execution_backend':'local',
                    **({'timeout':1.5} if failure=='source_timeout' else {}),
                    'tools':['experiment_run','finish'],'parameters':{
                        'crash_after_intent':failure=='executor_crash','defer_exit':failure in ('worker_restart','source_timeout'),
                        'child_backend':'bubblewrap' if failure=='sandbox' else None,
                        'child_delay':12 if failure=='hard_budget' else 2 if failure=='parent_cancel' else .1}}}})
        parent=h.launch(next(n for n in added['graph']['nodes'] if n['id']==source_id))
        assert parent['resource']['time_budget']['requested_task_timeout_seconds']==(1.5 if failure=='source_timeout' else None)
        if failure=='source_timeout':
            final=h.terminal(parent,timeout=20)
            assert final['status']=='failed' and final['exit_code']==124,final
            assert final['resource']['hard_budget_exhausted']['kind']=='seconds'
            assert final['resource']['pending_submission']
            time.sleep(1)
            assert len(h.request('GET',f"/api/projects/{project['id']}/runs"))==1
            assert len(h.run(parent)['resource']['attempts'])==1
            assert not final['resource'].get('submission_receipts')
            return
        if failure=='worker_restart':
            wait_until(lambda:(h.output(parent)/'result.json').exists(),timeout=20)
            assert h.run(parent)['status']=='running'
            assert len(h.request('GET',f"/api/projects/{project['id']}/runs"))==1
            h.stop(h.worker);h.start_worker()
        if failure=='parent_cancel':
            wait_until(lambda:(row if (row:=h.run(parent))['resource'].get('awaited_submission_action_id') else None),timeout=20)
            h.request('POST',f"/api/runs/{parent['id']}/cancel",json={})
            final=wait_until(lambda:(row if (row:=h.run(parent))['status']=='cancelled' else None),timeout=20)
            children=h.request('GET',f"/api/projects/{project['id']}/runs")
            child=next(row for row in children if row['id']!=parent['id'])
            assert h.terminal(child)['status']=='completed'
            final=h.run(parent)
            assert final['status']=='cancelled'
            assert len(final['resource']['attempts'])==1
        elif failure=='hard_budget':
            final=wait_until(lambda:(row if (row:=h.run(parent))['status']=='waiting_input' and
                row['resource'].get('blocked_reason')=='time_budget_exhausted' else None),timeout=30)
        elif failure!='parent_cancel':
            final=h.terminal(parent,timeout=40)
            assert final['status']=='completed',final
        rows=h.request('GET',f"/api/projects/{project['id']}/runs")
        assert len(rows)==2,rows
        child=next(row for row in rows if row['id']!=parent['id'])
        assert len(child['resource']['attempts'])==1
        assert len(final['resource']['submission_receipts'])==1
        receipt=next(iter(final['resource']['submission_receipts'].values()))
        assert receipt['run_ids']==[child['id']]
        assert receipt['handoff_status']=='admitted'
        if failure=='hard_budget':
            assert child['status']=='failed'
            assert child['exit_code']==124
            assert not (h.output(child)/'real-fit.json').exists()
            total=sum(row['resource']['elapsed_seconds'] for row in rows)
            assert limit<=total<=limit+1.0,total
        else:
            assert child['status']=='completed',child
            measured=json.loads((h.output(child)/'real-fit.json').read_text())
            assert measured['validation_mse']<.01
            assert measured['train_samples']==18 and measured['validation_samples']==6
            assert measured==child['metrics']
            if failure=='sandbox':
                assert child['config']['execution_backend']=='bubblewrap'
                assert json.loads((h.output(child)/'execution.json').read_text())['backend']=='bubblewrap'
            if failure=='parent_cancel':
                session=json.loads((h.output(parent)/'workspace'/'agent_session.json').read_text())
                assert len(session['transcript'])==1
                return
            assert final['resource']['attempts'][-1]['mode']=='continue'
            assert len(final['resource']['attempts'])==2
            session=json.loads((h.output(parent)/'workspace'/'agent_session.json').read_text())
            assert len(session['transcript'])==2
            assert not session.get('pending_action')
            if failure=='executor_crash':
                assert final['resource']['attempts'][0]['status']=='interrupted'
                assert (h.output(parent)/'intent-crash.txt').read_text()=='one accepted intent'
    finally:h.cleanup()


def test_verification_submission_uses_actual_independent_receipt(tmp_path):
    from test_research_verification_workflow import add_node,arithmetic_command,verification_config
    h=Harness(tmp_path)
    try:
        h.start_api();h.start_worker()
        project=h.request('POST','/api/projects',json={'name':'Durable independent verification',
            'goal':'Numerically verify the measured sum','mode':'manual','budget':{'seconds':120}})
        producer=add_node(h,project,type='experiment',title='Measured sum',
            config={'kind':'experiment','command':arithmetic_command(500)})
        verifier=add_node(h,project,type='verification',title='Independent formula',
            config=verification_config(producer))
        produced=h.launch(producer)
        assert h.terminal(produced)['status']=='completed'
        graph=h.request('GET',f"/api/projects/{project['id']}/graph")
        branch=graph['branches'][0]
        root=h.directory/'data'/'projects'/project['id']/branch['workspace']
        root.mkdir(parents=True,exist_ok=True)
        (root/'submission_agent.py').write_text(SUBMISSION_AGENT)
        parent_node=add_node(h,project,type='experiment',title='Verification parent',
            config={'kind':'experiment','entrypoint':'submission_agent:run','role':'Researcher','execution_backend':'local',
                'tools':['verification_run','finish'],'parameters':{'child_backend':'bubblewrap','submission':{
                    'tool':'verification_run','arguments':{'node_id':verifier['id']}}}})
        parent=h.launch(parent_node)
        final=h.terminal(parent,timeout=30)
        assert final['status']=='completed',final
        assert len(final['resource']['attempts'])==2
        receipt=next(iter(final['resource']['submission_receipts'].values()))
        checked=h.request('GET','/api/runs/'+receipt['run_ids'][0])
        assert checked['status']=='completed'
        assert checked['config']['execution_backend']=='bubblewrap'
        assert json.loads((h.output(checked)/'execution.json').read_text())['backend']=='bubblewrap'
        assert len(checked['resource']['attempts'])==1
        assert h.request('GET',f"/api/runs/{produced['id']}/verification")['verification_status']=='accepted'
        assert checked['resource']['verification']['checks'][0]['status']=='accepted'
    finally:h.cleanup()


@pytest.mark.parametrize('elapsed',[3,11])
def test_submission_unknown_owned_jobs_keep_actual_time_and_capacity(tmp_path,monkeypatch,elapsed):
    import datetime as dt
    import services.worker.main as worker
    from services.worker.scheduler import _elapsed_seconds_at,_time_budget_reservation_remaining
    folder=tmp_path/'runs'/'source';workspace=folder/'workspace';workspace.mkdir(parents=True)
    (workspace/'.forest-processes').mkdir()
    cancellations=[]
    def unknown():raise OSError('Original job registry cannot be read')
    def cancel():
        cancellations.append(True)
        raise OSError('Original job registry cannot be read')
    monkeypatch.setattr(worker,'process_manager',lambda *args:SimpleNamespace(all=unknown,cancel_all=cancel))
    monkeypatch.setattr(worker,'project_dir',lambda ident:tmp_path)
    run=SimpleNamespace(id='source',project_id='project',output_path='runs/source',status='waiting_input',
        pid=None,process_created=None,error=None,config={'timeout':10,'resources':{'cpu':2,'gpu':1}},
        resource={'elapsed_seconds':elapsed,'time_budget':{'effective_total_timeout_seconds':10},
            'pending_submission':{'action_id':'accepted','tool':'experiment_run','arguments':{}}})
    worker._advance_research_submission(None,run,folder)
    assert run.resource['execution_handoff_compute_hold']
    assert run.resource['blocked_reason']=='execution_reconciliation'
    assert run.resource['pending_submission']
    held=worker.reservation_for(run)
    assert held['cpu']==2 and held['gpu']==1
    future=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=.2)
    assert _elapsed_seconds_at(run,future)>=elapsed+.2
    assert _time_budget_reservation_remaining(run,future)<=max(0,10-elapsed-.2)
    if elapsed>10:
        assert run.resource['hard_budget_exhausted']['kind']=='seconds'
        worker._advance_research_submission(None,run,folder)
        assert len(cancellations)==1


def test_delegate_without_task_timeout_retains_real_child_dispatch(tmp_path):
    """Use the complete HTTP/model/worker support chain with no parent cap."""
    root=Path(__file__).resolve().parents[1]
    source=(root/'tests'/'test_support_workflow_dispatch.py').read_text()
    source=source.replace('ROOT = Path(__file__).resolve().parents[1]','ROOT = Path('+repr(str(root))+')')
    source=source.replace("'agent_budget': {'steps': 8}, 'timeout': 30", "'agent_budget': {'steps': 8}")
    script=tmp_path/'dispatch_no_task_timeout.py';script.write_text(source)
    env={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'integration.db'),
        'FOREST_DATA_DIR':str(tmp_path/'data'),'PYTHONPATH':str(root),'PYTHONUNBUFFERED':'1'}
    completed=subprocess.run([sys.executable,str(script),str(tmp_path)],cwd=root,env=env,
        capture_output=True,text=True,timeout=90)
    assert completed.returncode==0,completed.stdout+completed.stderr
    report=json.loads((tmp_path/'support-dispatch-report.json').read_text())
    assert report['parent_status']==report['child_status']=='completed'
    assert report['model_request_count']==3
    assert report['child_policy_delivered'] is True


@pytest.mark.parametrize('handoff',['submission','graph'])
def test_cancelled_owned_job_does_not_reopen_source_hard_budget(tmp_path,handoff):
    from research.agents.processes import ManagedProcesses
    from test_agent_processes import completed
    import services.worker.main as worker
    workspace=tmp_path/'workspace';workspace.mkdir()
    manager=ManagedProcesses(workspace)
    job=manager.start([sys.executable,'-c','import time;time.sleep(20)'],process_id='original')
    pending_key='pending_submission' if handoff=='submission' else 'pending_graph_recovery'
    run=SimpleNamespace(id='source',status='waiting_input',config={'timeout':1},
        resource={'elapsed_seconds':2,pending_key:{'action_id':'accepted','context':{}}},error=None)
    advance=worker._advance_research_submission if handoff=='submission' else worker._admit_graph_recovery_handoff
    try:
        advance(None,run,tmp_path)
        assert run.resource['hard_budget_exhausted']['kind']=='seconds'
        assert completed(manager,job['process_id'])['status']=='cancelled'
        # A second tick sees terminal owned jobs. It must retain the accepted
        # blocked history, without entering admission or resuming model work.
        advance(None,run,tmp_path)
        assert run.resource['blocked_reason']=='time_budget_exhausted'
        assert run.resource[pending_key]
        assert not run.resource.get('submission_receipts')
        assert not run.resource.get('graph_recovery_handoff')
    finally:manager.cancel_all()
