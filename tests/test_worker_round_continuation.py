"""Durable round handoffs through the actual executor, SQLite, and worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from test_worker import Harness, wait_until


@pytest.fixture(scope='module')
def round_worker(tmp_path_factory):
    harness = Harness(tmp_path_factory.mktemp('round-worker'))
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


def _round_node(harness, content, *, timeout=30):
    project = harness.request('POST', '/api/projects', json={
        'name': 'Durable round test', 'goal': 'Finish the registered work rounds',
        'budget': {'seconds': 90, 'allow_paid': False, 'max_runs': 1},
    })
    graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
    branch = graph['branches'][0]
    branch_root = harness.directory / 'data' / 'projects' / project['id'] / branch['workspace']
    branch_root.mkdir(parents=True, exist_ok=True)
    (branch_root / 'round_work.py').write_text(content)
    ident = str(uuid.uuid4())
    result = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
        'operation': 'add_node', 'targets': [], 'params': {
            'id': ident, 'branch_id': branch['id'], 'type': 'experiment',
            'title': 'Deterministic bounded work', 'config': {
                'kind': 'experiment', 'entrypoint': 'round_work:run', 'timeout': timeout,
            },
        },
    })
    return next(node for node in result['graph']['nodes'] if node['id'] == ident)


ROUND_SOURCE = '''
from pathlib import Path
import json, time
from research.agents.runtime import AgentYield

def run(parameters, output):
    root = Path(output)
    checkpoint = root / 'workspace' / 'round_checkpoint.json'
    n = json.loads(checkpoint.read_text())['round'] + 1 if checkpoint.exists() else 1
    with (root / 'observed_work.txt').open('a') as stream:
        stream.write(str(n) + '\\n')
    checkpoint.write_text(json.dumps({'round': n}))
    time.sleep(0.1)
    if n < 4:
        raise AgentYield(status='waiting', reason='Work round checkpointed',
            wait_for={'type': 'work_round', 'round_id': n, 'reason': 'bounded work',
                      'resume_after': time.time() + 0.4})
    return {'observed_rounds': n, 'checkpoint': 'round_checkpoint.json'}
'''


def test_real_rounds_continue_same_run_and_preserve_accounting(round_worker):
    h = round_worker
    node = _round_node(h, ROUND_SOURCE)
    run = h.launch(node)
    complete = h.terminal(run, timeout=40)
    assert complete['status'] == 'completed', complete
    assert complete['metrics']['observed_rounds'] == 4
    attempts = complete['resource']['attempts']
    assert len(attempts) == 4
    assert len({attempt['id'] for attempt in attempts}) == 4
    assert [attempt['status'] for attempt in attempts] == ['waiting'] * 3 + ['completed']
    assert complete['resource']['elapsed_seconds'] == pytest.approx(
        sum(attempt['elapsed_seconds'] for attempt in attempts))
    assert 'waiting_started_at' not in complete['resource']
    assert (h.output(run) / 'observed_work.txt').read_text().splitlines() == ['1', '2', '3', '4']
    runs = h.request('GET', f"/api/projects/{run['project_id']}/runs")
    assert [entry['id'] for entry in runs] == [run['id']]


def test_round_receipt_waits_for_executor_exit_and_survives_worker_restart(round_worker):
    h = round_worker
    source = ROUND_SOURCE.replace('if n < 4:', 'if n < 2:').replace(
        "    time.sleep(0.1)",
        "    import atexit\n    atexit.register(time.sleep, 2)\n    time.sleep(0.1)")
    run = h.launch(_round_node(h, source))
    h.running(run)
    receipt = h.output(run) / 'result.json'
    wait_until(lambda: receipt.is_file())
    # The entrypoint has yielded and flushed its receipt, but an actual exit
    # hook is still running. No second executor may own the same workspace.
    time.sleep(0.5)
    assert h.run(run)['status'] == 'running'
    assert (h.output(run) / 'observed_work.txt').read_text().splitlines() == ['1']
    h.stop(h.worker)
    h.start_worker()
    complete = h.terminal(run, timeout=40)
    assert complete['status'] == 'completed', complete
    assert (h.output(run) / 'observed_work.txt').read_text().splitlines() == ['1', '2']
    assert len(complete['resource']['attempts']) == 2


def test_uncommitted_orphan_executor_cannot_execute_or_overwrite_receipt(round_worker):
    h = round_worker
    h.stop(h.worker)
    _, node = h.project_node(seconds=0.2, timeout=20)
    run = h.launch(node)
    orphan = subprocess.Popen([sys.executable, '-m', 'services.worker.execute', '--run-id', run['id']],
        cwd=Path(__file__).resolve().parents[1], env={**h.env, 'FOREST_ATTEMPT_ID': 'rolled-back-attempt'},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    h.processes.append(orphan)
    h.start_worker()
    complete = h.terminal(run)
    assert complete['status'] == 'completed', complete
    assert orphan.wait(timeout=15) == 75
    assert len(complete['resource']['attempts']) == 1
    receipt = json.loads((h.output(run) / 'result.json').read_text())
    assert receipt['attempt_id'] == complete['config']['execution_attempt']['id']


def test_work_round_checkpoint_does_not_charge_idle_scheduling_time():
    from services.worker.main import _waiting_outcome_resource
    resource = _waiting_outcome_resource({'elapsed_seconds': 15}, {
        'status': 'waiting', 'wait_for': {'type': 'work_round', 'round_id': 400},
    }, observed_at=1000)
    assert resource['elapsed_seconds'] == 15
    assert 'waiting_started_at' not in resource


def test_legacy_migration_rejects_actual_token_and_time_exhaustion(tmp_path):
    from services.worker.main import _legacy_step_continuation
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checkpoint = {
        'run_id': 'legacy-run', 'status': 'budget_exhausted', 'transcript': [{}] * 150,
        'totals': {'input_tokens': 1000, 'output_tokens': 100, 'cost': 0}, 'active_seconds': 10,
        'budget_reason': 'Configured agent budget reached: steps=150 (limit 150). Increase this editable budget to continue the same session.',
    }
    (workspace / 'agent_session.json').write_text(json.dumps(checkpoint))
    value = {'id': 'legacy-run', 'status': 'budget_exhausted', 'kind': 'agent',
        'config': {'agent_budget': {'steps': 150}}, 'resource': {'elapsed_seconds': 15}}
    assert _legacy_step_continuation(value, tmp_path)
    assert not _legacy_step_continuation({**value, 'status': 'paused'}, tmp_path)
    assert not _legacy_step_continuation({**value, 'config': {
        'agent_budget': {'steps': 150, 'input_tokens': 1000}}}, tmp_path)
    assert not _legacy_step_continuation({**value, 'config': {'timeout': 15}}, tmp_path)
    checkpoint['run_id'] = 'different-run'
    (workspace / 'agent_session.json').write_text(json.dumps(checkpoint))
    assert not _legacy_step_continuation(value, tmp_path)


def test_crash_fuse_is_scoped_to_checkpoint_progress(tmp_path):
    from services.worker.main import _checkpoint_recovery_config
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checkpoint = {'run_id': 'agent-run', 'transcript': [{'step': 401}], 'pending_action': None}
    path = workspace / 'agent_session.json'
    path.write_text(json.dumps(checkpoint))
    run = SimpleNamespace(id='agent-run', kind='agent', config={'_recovery_failures': 300}, resource={})
    assert _checkpoint_recovery_config(run, tmp_path)['_recovery_failures'] == 0
    assert _checkpoint_recovery_config(run, tmp_path)['_recovery_failures'] == 1
    assert _checkpoint_recovery_config(run, tmp_path)['_recovery_failures'] == 2
    checkpoint['transcript'].append({'step': 402})
    path.write_text(json.dumps(checkpoint))
    assert _checkpoint_recovery_config(run, tmp_path)['_recovery_failures'] == 0


def test_step_migration_loads_external_usage_without_changing_scientific_checkpoint(tmp_path):
    from services.worker.main import _legacy_step_continuation
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checkpoint = {'run_id':'legacy-run','status':'budget_exhausted','transcript':[{}] * 400,
        'budget_reason':'Configured agent budget reached: steps=400 (limit 400).'}
    path=workspace / 'agent_session.json'
    path.write_text(json.dumps(checkpoint))
    usage={'run_id':'legacy-run','totals':{'input_tokens':500,'output_tokens':200,'cost':0},
        'active_seconds':10}
    (workspace / 'agent_usage.json').write_text(json.dumps(usage))
    value={'id':'legacy-run','kind':'agent','status':'budget_exhausted',
        'config':{'agent_budget':{'steps':400,'input_tokens':500}},'resource':{}}
    assert not _legacy_step_continuation(value,tmp_path)
    value['config']['agent_budget']['input_tokens']=501
    assert _legacy_step_continuation(value,tmp_path)
    assert json.loads(path.read_text())==checkpoint
    (workspace / 'agent_usage.json').write_text('{truncated')
    assert not _legacy_step_continuation(value,tmp_path)


def test_workspace_preparation_resumes_partial_first_attempt_and_preserves_later_edits(tmp_path):
    from services.worker.execute import prepare_task_workspace
    root=tmp_path/'project'
    source=root/'branch'
    output=root/'runs'/'same-run'
    workspace=output/'workspace'
    source.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (source/'method.py').write_text('original method')
    (root/'dataset.csv').write_text('registered training data')
    (workspace/'method.py').write_text('partial copy')
    marker=output/'workspace_preparation.json'
    marker.write_text(json.dumps({'status':'preparing'}))
    config={'execution_attempt':{'number':2},'resolved_inputs':[
        {'source_path':'dataset.csv','destination':'data/train.csv'}]}
    prepare_task_workspace(root,source,output,config)
    assert (workspace/'method.py').read_text()=='original method'
    assert (workspace/'data/train.csv').read_text()=='registered training data'
    prepared=json.loads(marker.read_text())
    assert prepared['status']=='completed'
    assert len(prepared['input_hashes']['data/train.csv'])==64
    (workspace/'method.py').write_text('agent progress after preparation')
    config['execution_attempt']['number']=401
    prepare_task_workspace(root,source,output,config)
    assert (workspace/'method.py').read_text()=='agent progress after preparation'


def test_graph_handoff_retains_live_job_time_and_capacity_reservation(tmp_path):
    import datetime as dt
    from research.agents.processes import ManagedProcesses
    from test_agent_processes import completed
    from services.worker.main import _admit_graph_recovery_handoff
    from services.worker.scheduler import _elapsed_seconds_at, _time_budget_reservation_remaining
    workspace=tmp_path/'workspace'; workspace.mkdir()
    manager=ManagedProcesses(workspace)
    job=manager.start([sys.executable,'-c','import time; time.sleep(1)'],process_id='still-owned')
    run=SimpleNamespace(id='source',status='waiting_input',config={'timeout':10},
        resource={'elapsed_seconds':3,'pending_graph_recovery':{'context':{},'checkpoint_state_key':'same-state'},
            'time_budget':{'effective_total_timeout_seconds':10}},error=None)
    try:
        # No DB admission may occur while the already launched job remains
        # alive. Its consumed and reserved time still participates in the same
        # outer project accounting despite the yielded Agent executor.
        _admit_graph_recovery_handoff(None,run,tmp_path)
        assert run.resource['graph_recovery_compute_hold']
        assert run.resource['pending_graph_recovery']
        future=dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=.2)
        assert _elapsed_seconds_at(run,future)>=3.2
        assert _time_budget_reservation_remaining(run,future)<=6.8
        assert completed(manager,job['process_id'])['status']=='completed'
    finally:
        manager.cancel_all()


def test_graph_handoff_keeps_capacity_when_original_registry_is_unreadable(tmp_path,monkeypatch):
    import services.worker.main as worker
    output=tmp_path/'runs'/'source'; workspace=output/'workspace'; workspace.mkdir(parents=True)
    registry=workspace/'.forest-processes'; registry.mkdir()
    def unavailable_registry():
        raise OSError('Original job registry cannot be read')
    monkeypatch.setattr(worker,'process_manager',lambda *args: SimpleNamespace(all=unavailable_registry))
    run=SimpleNamespace(id='source',project_id='project',output_path='runs/source',
        status='waiting_input',pid=None,process_created=None,config={'resources':{'cpu':2,'gpu':1}},
        resource={'elapsed_seconds':3,'pending_graph_recovery':{'context':{},'checkpoint_state_key':'same-state'}},error=None)
    monkeypatch.setattr(worker,'project_dir',lambda ident: tmp_path)
    worker._admit_graph_recovery_handoff(None,run,output)
    assert run.resource['graph_recovery_compute_hold']
    assert run.resource['blocked_reason']=='execution_reconciliation'
    reserved=worker.reservation_for(run)
    assert reserved is not None
    assert reserved['cpu']==2
    assert reserved['gpu']==1
    assert run.resource['pending_graph_recovery']
