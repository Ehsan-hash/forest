"""Scheduler infrastructure tests; Python children are not scientific fits/models."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import threading
from types import SimpleNamespace
import uuid

import pytest

from research.experiments.study_runtime import StudyLedger
from research.experiments.study_scheduler import (
    CoordinatorBusy, StudyScheduler, interval_measure, intersect_intervals, measured_concurrency)


def document(items):
    return {'campaign_id': 'test-campaign', 'manifest_id': 'test-manifest', 'manifest_version': 1,
        'external_ledger_id': 'test-ledger',
        'model_provider': {'id': 'cpu-test', 'model': 'infrastructure-child', 'config': {'reasoning_effort': 'medium'}},
        'execution_state': {'task_queue': items}}


CHILD = """
import json, pathlib, sys, time
started=time.time()
time.sleep(float(sys.argv[2]))
pathlib.Path(sys.argv[1]).write_text(json.dumps({'work_id':sys.argv[3],
    'independent_context':sys.argv[4], 'started_at':started,'finished_at':time.time()}))
"""


class ChildRuntime:
    """Real SQLite leases and Python OS effects, explicitly no model/GPU inference."""
    def __init__(self, directory, items):
        self.campaign = Path(directory)
        self.campaign.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.campaign / 'study.sqlite'
        self.ledger_id = 'test-ledger'
        self.campaign_id = 'test-campaign'
        self.manifest = document(items)
        self.ledger = StudyLedger(self.ledger_path, self.ledger_id)
        self.ledger.register(self.manifest)

    def reload_manifest(self):
        return self.manifest

    def claim_work(self, owner, *, work_ids=None, kinds=None):
        return self.ledger.claim(self.campaign_id, owner, work_ids=work_ids, kinds=kinds)

    def run_claimed(self, item, *, gpu_devices=(), client=None, defer_gpu=False):
        work_id = item['work_id']
        self.ledger.set_state(self.campaign_id, work_id, 'running')
        payload = item['payload']
        if item.get('decision_json'):
            child_id = json.loads(item['decision_json'])['child_id']
            with self.ledger.connect() as db:
                child = db.execute('SELECT state FROM forest_study_work WHERE work_id=?', (child_id,)).fetchone()
            if child['state'] != 'completed':
                raise AssertionError('Scheduler repeated a parent while its accepted child was unfinished')
            result = {'resumed_saved_decision': True, 'child_id': child_id}
            self.ledger.set_state(self.campaign_id, work_id, 'completed', result=result)
            return result
        if payload.get('fail'):
            from research.agents.provider import ProviderError
            self.ledger.set_state(self.campaign_id, work_id, 'failed', error='SourceUnavailable: offline test source')
            raise ProviderError('offline test source', code='transport_error')
        request_id = str(uuid.uuid4())
        started = time.time()
        if item['kind'] in ('episode', 'replay'):
            with self.ledger.connect() as db:
                db.execute('INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at,started_at) VALUES(?,?,?,?,?,?,?)',
                    (request_id, self.campaign_id, work_id, json.dumps({'work_id': work_id, 'model': 'infrastructure-child', 'context': payload['context']}), 'requested', started, started))
        barrier = getattr(self, 'request_barriers', {}).get(work_id)
        if barrier is not None:
            # Both actual request records exist before either child can finish.
            barrier.wait(timeout=5)
        output = self.campaign / (work_id + '.json')
        subprocess.run([sys.executable, '-c', CHILD, str(output), str(payload.get('seconds', .15)), work_id, payload.get('context', work_id)],
            check=True, timeout=client.timeout if client is not None else 5)
        result = json.loads(output.read_text())
        finished = time.time()
        with self.ledger.connect() as db:
            if item['kind'] in ('episode', 'replay'):
                db.execute("UPDATE forest_study_model_calls SET response_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                    (json.dumps(result), finished, finished-started, request_id))
        if payload.get('deferred_child'):
            assert defer_gpu is True
            child = payload['deferred_child']
            self.ledger.register_action(self.campaign_id, child, {'kind': 'fit', 'work_id': child, 'seconds': .1}, parent_id=work_id)
            self.ledger.set_state(self.campaign_id, work_id, 'retryable',
                decision={'child_id': child}, result={'pending_work_ids': [child], 'deferred_to_gpu_queue': True})
            return {'deferred_to_gpu_queue': True, 'work_id': child}
        self.ledger.set_state(self.campaign_id, work_id, 'completed', result=result)
        return result


def scheduler(runtime, **kwargs):
    return StudyScheduler(runtime, client_factory=lambda item: SimpleNamespace(timeout=10, work_id=item['work_id']),
        poll_seconds=.01, **kwargs)


def run_until(service, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        service.tick()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Scheduler fixture did not reach its actual terminal state')


def rows(runtime):
    with runtime.ledger.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM forest_study_work ORDER BY work_id')]


def test_interval_union_does_not_add_overlapping_resources_as_wall_time():
    assert interval_measure([(1, 4), (2, 5)]) == {'summed_seconds': 6, 'wall_union_seconds': 4, 'interval_count': 2}
    assert intersect_intervals([(1, 5)], [(2, 3), (2.5, 4)]) == [(2, 4)]


def test_current_formal_progress_excludes_historical_and_withdrawn_cells(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': name, 'kind': 'replay', 'phase': 'REPLAY', 'context': name}
        for name in ('old', 'omitted', 'current', 'paused')])
    runtime.ledger.set_state(runtime.campaign_id, 'old', 'completed')
    runtime.ledger.set_state(runtime.campaign_id, 'omitted', 'not_run')
    runtime.ledger.set_state(runtime.campaign_id, 'paused', 'paused_no_progress')
    runtime.manifest.update(cells=[{'episode_id': 'current'}, {'episode_id': 'paused'}],
        formal_batch_id='new-batch', agent_execution_protocol_version='state-driven-v2',
        selection_revision={'revision_id': 'scope-v2'})
    service = scheduler(runtime)
    try:
        value = service.metrics()
        assert value['formal_progress'] == {
            'protocol_version': 'state-driven-v2', 'selection_revision': 'scope-v2',
            'formal_batch_id': 'new-batch', 'total': 2, 'registered': 2,
            'completed': 0, 'states': {'queued': 1, 'paused_no_progress': 1},
            'paused_no_progress': 1, 'percent': 0.0}
        stage = next(row for row in value['stage_throughput'] if row['stage'] == 'REPLAY')
        assert stage['registered'] == stage['remaining'] == 2
        assert stage['completed'] == 0 and stage['eta_remaining_seconds'] is None
        assert value['paused_no_progress_work_ids'] == ['paused']
        assert 'paused' not in service._ready_ids(('replay',), {})
        assert value['work_states']['completed'] == 1  # Historical resource/readback record persists.
    finally:
        service.close()


def pilot():
    return {'pilot_id': 'actual-fixture-timing', 'model': 'infrastructure-child', 'reasoning_effort': 'medium',
        'batches': [
            {'concurrency': 1, 'started_at_epoch': 1., 'finished_at_epoch': 4., 'completed_calls': 1,
             'calls': [{'status': 'completed', 'request_id': 'r1', 'elapsed_seconds': 2.9,'started_at_epoch':1.05,'finished_at_epoch':3.95}]},
            {'concurrency': 2, 'started_at_epoch': 4., 'finished_at_epoch': 7., 'completed_calls': 2,
             'calls': [{'status': 'completed', 'request_id': 'r2', 'elapsed_seconds': 2.9,'started_at_epoch':4.05,'finished_at_epoch':6.95},
                       {'status': 'completed', 'request_id': 'r3', 'elapsed_seconds': 2.8,'started_at_epoch':4.1,'finished_at_epoch':6.9}]}]}


def test_pilot_uses_only_observed_successful_authorized_degrees():
    assert measured_concurrency(pilot(), 2)['degree'] == 2
    assert measured_concurrency(pilot(), 1)['degree'] == 1
    data = pilot()
    data['batches'][1]['calls'][0]['status'] = 'failed'
    assert measured_concurrency(data, 9)['degree'] == 1


def test_serial_calls_are_not_evidence_of_declared_parallel_capacity():
    data=pilot()
    batch=data['batches'][1]
    batch['finished_at_epoch']=10.
    batch['calls'][0].update(started_at_epoch=4.05,finished_at_epoch=6.95)
    batch['calls'][1].update(started_at_epoch=7.,finished_at_epoch=9.8)
    assert measured_concurrency(data,2)['degree']==1


def test_finished_worker_refills_while_measurement_wave_has_slow_worker(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'a-short', 'kind': 'episode', 'context': 'a', 'seconds': .05},
        {'work_id': 'b-slow', 'kind': 'episode', 'context': 'b', 'seconds': .7},
        {'work_id': 'c-ready', 'kind': 'episode', 'context': 'c', 'seconds': .05},
    ])
    service = scheduler(runtime, model_concurrency=2, maximum_model_concurrency=2)
    try:
        run_until(service, lambda: all(row['state'] == 'completed' for row in rows(runtime)))
        ready = json.loads((tmp_path / 'c-ready.json').read_text())
        slow = json.loads((tmp_path / 'b-slow.json').read_text())
        assert ready['finished_at'] < slow['finished_at']
        assert ready['independent_context'] == 'c'
    finally:
        service.close()


def test_live_sequential_requests_use_actual_overlap_degree(tmp_path):
    runtime = ChildRuntime(tmp_path, [])
    service = scheduler(runtime, model_concurrency=2, maximum_model_concurrency=2)
    service._tested_degrees = {1, 2}
    with runtime.ledger.connect() as db:
        db.execute('INSERT INTO forest_study_dispatches(dispatch_id,campaign_id,scheduler_id,session_id,owner,work_id,lane,state,accepted_at,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('dispatch', runtime.campaign_id, service.scheduler_id, service.session_id, 'owner', 'native-node', 'model', 'returned', 0., 0., 5.))
        for request_id, left, right in [('first', 1., 2.), ('second', 3., 4.)]:
            db.execute('INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at,started_at,finished_at,elapsed_seconds) VALUES(?,?,?,?,?,?,?,?,?)',
                (request_id, runtime.campaign_id, 'native-node', json.dumps({'model': 'infrastructure-child'}), 'completed', left, left, right, right-left))
    service._wave = {'degree': 2, 'dispatch_ids': ['dispatch']}
    try:
        service._evaluate_wave()
        assert service._observed_samples[-1]['degree'] == 1
    finally:
        service.close()


def test_exhausted_cell_is_terminal_for_scheduler(tmp_path):
    runtime = ChildRuntime(tmp_path, [{'work_id': 'exhausted', 'kind': 'episode', 'context': 'own'}])
    runtime.ledger.set_state(runtime.campaign_id, 'exhausted', 'budget_exhausted')
    service = scheduler(runtime)
    try:
        outcome = service.tick()
        assert outcome['state'] == 'idle'
        assert outcome['unresolved_work_items'] == 0
    finally:
        service.close()


def test_stage_throughput_uses_execution_claims_and_audit_excludes_model_wait(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'inference', 'kind': 'episode', 'stage': 'LIVE_MAIN', 'context': 'own', 'seconds': .05},
        {'work_id': 'audit', 'kind': 'analysis', 'stage': 'validation', 'context': 'cpu', 'seconds': .05},
    ])
    with runtime.ledger.connect() as db:
        db.execute('UPDATE forest_study_work SET created_at=created_at-86400')
    service = scheduler(runtime)
    try:
        run_until(service, lambda: all(row['state'] == 'completed' for row in rows(runtime)))
        metrics = service.metrics()
        stage = next(value for value in metrics['stage_throughput'] if value['stage'] == 'LIVE_MAIN')
        assert stage['registered'] == stage['completed'] == 1
        assert 0 < stage['observed_wall_seconds'] < 10
        assert stage['remaining'] == stage['eta_remaining_seconds'] == 0
        assert metrics['categories']['audit']['summed_seconds'] > 0
        with runtime.ledger.connect() as db:
            spans = [dict(row) for row in db.execute("SELECT * FROM forest_study_scheduler_intervals WHERE work_id='inference' AND kind='audit'")]
            call = db.execute("SELECT started_at,finished_at FROM forest_study_model_calls WHERE work_id='inference'").fetchone()
        assert spans
        assert all(not intersect_intervals([(row['started_at'], row['finished_at'])],
            [(call['started_at'], call['finished_at'])]) for row in spans)
    finally:
        service.close()


def test_pilot_profile_must_match_saved_model(tmp_path):
    runtime = ChildRuntime(tmp_path, [])
    path = tmp_path / 'pilot.json'
    data = pilot()
    data['model'] = 'unobserved-other-model'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='model profile'):
        scheduler(runtime, pilot_path=path)


def test_real_independent_children_overlap_and_gpu_lane_is_serial(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'fit-a', 'kind': 'fit', 'seconds': .3},
        {'work_id': 'fit-b', 'kind': 'fit', 'seconds': .2},
        {'work_id': 'episode-a', 'kind': 'episode', 'context': 'only-A', 'seconds': .35},
        {'work_id': 'episode-b', 'kind': 'episode', 'context': 'only-B', 'seconds': .35},
    ])
    service = scheduler(runtime, model_concurrency=2)
    try:
        run_until(service, lambda: all(row['state'] == 'completed' for row in rows(runtime)))
        a,b = [json.loads((tmp_path / (name + '.json')).read_text()) for name in ('episode-a', 'episode-b')]
        assert a['started_at'] < b['finished_at'] and b['started_at'] < a['finished_at']
        assert a['independent_context'] == 'only-A' and b['independent_context'] == 'only-B'
        fits = [json.loads((tmp_path / (name + '.json')).read_text()) for name in ('fit-a', 'fit-b')]
        fits.sort(key=lambda value: value['started_at'])
        assert fits[0]['finished_at'] <= fits[1]['started_at']
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT lane FROM forest_study_dispatches ORDER BY accepted_at LIMIT 1').fetchone()[0] == 'gpu'
            assert db.execute('SELECT count(*) FROM forest_study_dispatches WHERE state=\'returned\'').fetchone()[0] == 4
        value = service.metrics()
        assert value['categories']['model_wait']['summed_seconds'] > value['categories']['model_wait']['wall_union_seconds']
        assert value['completed_work_items'] == 4 and value['remaining_ready'] == 0
        assert value['categories_can_overlap'] is True
    finally:
        service.close()


def test_deferred_fit_releases_model_slot_and_resumes_original_decision_once(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'episode-a', 'kind': 'episode', 'context': 'decision-A', 'seconds': .05, 'deferred_child': 'fit-child'},
        {'work_id': 'episode-b', 'kind': 'episode', 'context': 'decision-B', 'seconds': .3},
    ])
    service = scheduler(runtime, model_concurrency=2)
    try:
        run_until(service, lambda: len(rows(runtime)) == 3 and all(row['state'] == 'completed' for row in rows(runtime)))
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls WHERE work_id=\'episode-a\'').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM forest_study_dispatches WHERE work_id=\'fit-child\'').fetchone()[0] == 1
            gpu_start = db.execute('SELECT started_at FROM forest_study_dispatches WHERE work_id=\'fit-child\'').fetchone()[0]
            other_end = db.execute('SELECT finished_at FROM forest_study_model_calls WHERE work_id=\'episode-b\'').fetchone()[0]
            assert gpu_start < other_end
        parent = next(row for row in rows(runtime) if row['work_id'] == 'episode-a')
        assert json.loads(parent['result_json'])['resumed_saved_decision'] is True
    finally:
        service.close()


def test_two_coordinators_cannot_own_same_campaign(tmp_path):
    runtime = ChildRuntime(tmp_path, [])
    first,second = scheduler(runtime), scheduler(runtime)
    try:
        original = first.acquire()
        with pytest.raises(CoordinatorBusy, match='still alive'):
            second.acquire()
        first.close()
        assert second.acquire() == original
    finally:
        first.close()
        second.close()


def test_crash_after_claim_releases_unstarted_work_but_not_unknown_model_request(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'safe', 'kind': 'episode', 'context': 'safe'},
        {'work_id': 'unknown', 'kind': 'episode', 'context': 'unknown'},
    ])
    service = scheduler(runtime, model_concurrency=2)
    service.acquire()
    service.close()
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_schedulers SET state='running',pid=-999,process_created=0")
        for work_id,state in [('safe','claimed'), ('unknown','running')]:
            owner = 'dead:' + work_id
            db.execute('UPDATE forest_study_work SET state=?,lease_owner=? WHERE work_id=?', (state,owner,work_id))
            db.execute('INSERT INTO forest_study_dispatches(dispatch_id,campaign_id,scheduler_id,session_id,owner,work_id,lane,state,accepted_at,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (work_id,runtime.campaign_id,service.scheduler_id,'dead-session',owner,work_id,'model','running',time.time(),time.time()))
        db.execute('INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at,started_at) VALUES(?,?,?,?,?,?,?)',
            ('accepted-original',runtime.campaign_id,'unknown','{}','requested',time.time(),time.time()))
    # One original unknown request reserves one slot; the independently safe
    # claim can run only in the second authorized physical model slot.
    resumed = scheduler(runtime, model_concurrency=2)
    try:
        resumed.acquire()
        state = {row['work_id']: row['state'] for row in rows(runtime)}
        assert state == {'safe': 'queued', 'unknown': 'awaiting_reconciliation'}
        run_until(resumed, lambda: next(row for row in rows(runtime) if row['work_id']=='safe')['state']=='completed')
        assert not (tmp_path / 'unknown.json').exists()
        metrics = resumed.metrics()
        assert metrics['categories']['unreconciled_model_interval']['summed_seconds'] > 0
        assert metrics['remaining_blocked_or_waiting'] == 1
    finally:
        resumed.close()


def test_two_identical_source_failures_stop_blind_calls_but_keep_other_work(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'a', 'kind': 'episode', 'context': 'a', 'fail': True},
        {'work_id': 'b', 'kind': 'episode', 'context': 'b', 'fail': True},
        {'work_id': 'c', 'kind': 'episode', 'context': 'c'},
        {'work_id': 'fit', 'kind': 'fit', 'seconds': .03},
    ])
    service = scheduler(runtime, maximum_model_concurrency=1)
    try:
        run_until(service, lambda: len([row for row in rows(runtime) if row['state']=='failed']) == 2)
        for _ in range(10):
            service.tick()
            time.sleep(.01)
        states = {row['work_id']:row['state'] for row in rows(runtime)}
        assert states == {'a':'failed','b':'failed','c':'queued','fit':'completed'}
        assert not (tmp_path / 'c.json').exists()
        assert service.metrics()['remaining_blocked_or_waiting'] == 1
    finally:
        service.close()


def test_compute_wall_policy_does_not_limit_model_controller_or_request(tmp_path):
    from research.experiments.gpu_supervisor import ComputeSupervisor
    runtime = ChildRuntime(tmp_path, [{'work_id':'a','kind':'episode','context':'a'}])
    supervisor = ComputeSupervisor(runtime.ledger_path,runtime.ledger_id)
    supervisor.configure(gpu_budget_seconds=10,wall_start=time.time()-20,wall_seconds=10,campaign_id=runtime.campaign_id)
    service = scheduler(runtime)
    try:
        assert service._hard_allowance() < 0  # Diagnostic only; GPU policy remains.
        assert service._make_client({'work_id':'a','payload':{},'kind':'episode'}).timeout == 10
        run_until(service, lambda: rows(runtime)[0]['state'] == 'completed')
        assert (tmp_path / 'a.json').is_file()
    finally:
        service.close()


def test_live_wave_measures_authorized_next_degree_and_persists_choice(tmp_path):
    runtime = ChildRuntime(tmp_path, [
        {'work_id': f'episode-{i}', 'kind': 'episode', 'context': str(i), 'seconds': .15} for i in range(5)])
    barrier = threading.Barrier(2)
    runtime.request_barriers = {'episode-1': barrier, 'episode-2': barrier}
    service = scheduler(runtime, maximum_model_concurrency=2)
    try:
        run_until(service, lambda: all(row['state']=='completed' for row in rows(runtime)))
        service.tick()
        with runtime.ledger.connect() as db:
            decisions = [json.loads(row[0]) for row in db.execute('SELECT decision_json FROM forest_study_scheduler_decisions')]
        assert any(row.get('probe') is True and row['degree']==2 for row in decisions)
        assert any(row.get('sample',{}).get('degree')==2 for row in decisions)
        assert service.degree <= 2
        stable_id = service.scheduler_id
    finally:
        service.close()
    second = scheduler(runtime, maximum_model_concurrency=2)
    try:
        assert second.acquire() == stable_id
        assert second._tested_degrees == {1,2}
    finally:
        second.close()


def test_real_sqlite_claim_unique_across_processes(tmp_path):
    runtime = ChildRuntime(tmp_path,[{'work_id':'only-one','kind':'fit'}])
    script = """
import json,pathlib,sys
from research.experiments.study_runtime import StudyLedger
ledger=StudyLedger(pathlib.Path(sys.argv[1]),'test-ledger')
result=ledger.claim('test-campaign',sys.argv[2])
print(json.dumps(result))
"""
    children = [subprocess.Popen([sys.executable,'-c',script,str(runtime.ledger_path),'process-'+str(i)],stdout=subprocess.PIPE,text=True) for i in range(2)]
    values = [json.loads(child.communicate(timeout=5)[0]) for child in children]
    assert all(child.returncode == 0 for child in children)
    assert sum(value is not None for value in values) == 1
    assert rows(runtime)[0]['state'] == 'claimed'


def test_real_coordinator_kill_restores_identity_and_unstarted_claim(tmp_path):
    runtime = ChildRuntime(tmp_path,[{'work_id':'unstarted','kind':'fit'}])
    script = """
import pathlib,sys,time
sys.path.insert(0,sys.argv[2])
from test_study_scheduler import ChildRuntime,scheduler
r=ChildRuntime(sys.argv[1],[])
s=scheduler(r);s.acquire()
owner='original-owner'
item=r.claim_work(owner)
with r.ledger.connect() as db:
 db.execute('INSERT INTO forest_study_dispatches(dispatch_id,campaign_id,scheduler_id,session_id,owner,work_id,lane,state,accepted_at) VALUES(?,?,?,?,?,?,?,?,?)',
  ('accepted',r.campaign_id,s.scheduler_id,s.session_id,owner,item['work_id'],'gpu','accepted',time.time()))
pathlib.Path(sys.argv[1],'ready.json').write_text(s.scheduler_id)
time.sleep(30)
"""
    child = subprocess.Popen([sys.executable,'-c',script,str(tmp_path),str(Path(__file__).parent)])
    deadline = time.monotonic()+5
    while not (tmp_path/'ready.json').is_file() and time.monotonic()<deadline:
        time.sleep(.01)
    try:
        assert (tmp_path/'ready.json').is_file()
        service = scheduler(runtime)
        with pytest.raises(CoordinatorBusy):
            service.acquire()
        child.kill()
        child.wait(timeout=5)
        assert service.acquire() == (tmp_path/'ready.json').read_text()
        assert rows(runtime)[0]['state']=='queued'
        run_until(service,lambda: rows(runtime)[0]['state']=='completed')
        service.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_actual_original_cpu_receipt_is_polled_without_repeated_dispatch(tmp_path):
    from research.experiments.gpu_supervisor import ComputeSupervisor

    class AsyncRuntime(ChildRuntime):
        def run_claimed(self,item,*,gpu_devices=(),client=None,defer_gpu=False):
            if item['kind'] != 'fit':
                return super().run_claimed(item,gpu_devices=gpu_devices,client=client,defer_gpu=defer_gpu)
            self.ledger.set_state(self.campaign_id,item['work_id'],'running')
            supervisor=ComputeSupervisor(self.ledger_path,self.ledger_id)
            try:
                observation=supervisor.status(item['work_id'])
            except ValueError:
                output=self.campaign/'native-child.json'
                observation=supervisor.submit([sys.executable,'-c',CHILD,str(output),'.35',item['work_id'],'CPU-only receipt fixture'],
                    self.campaign,work_id=item['work_id'],timeout_seconds=8,gpu_devices=(),configuration={'infrastructure_test':True})
            state='completed' if observation['status']=='completed' else 'awaiting_reconciliation' if observation['status']=='reconciliation' else 'retryable'
            self.ledger.set_state(self.campaign_id,item['work_id'],state,result={'supervisor':observation},supervisor_work_id=item['work_id'])
            return {'supervisor':observation}

    runtime=AsyncRuntime(tmp_path,[{'work_id':'real-original','kind':'fit'}])
    supervisor=ComputeSupervisor(runtime.ledger_path,runtime.ledger_id)
    supervisor.configure(gpu_budget_seconds=100,wall_start=time.time(),wall_seconds=30)
    service=scheduler(runtime)
    try:
        outcome=service.run(wait_timeout=10)
        assert outcome['state']=='idle'
        assert rows(runtime)[0]['state']=='completed'
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_compute_work').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM forest_study_dispatches WHERE work_id=\'real-original\'').fetchone()[0]==2
        status=supervisor.status('real-original')
        assert status['status']=='completed' and status['elapsed_ms']>0
        assert status['gpu_ms']==0
        with runtime.ledger.connect() as db:
            assert db.execute("SELECT SUM(quantity) FROM forest_compute_usage WHERE resource_type='cpu_ms'").fetchone()[0]==status['elapsed_ms']
        assert json.loads((tmp_path/'native-child.json').read_text())['independent_context']=='CPU-only receipt fixture'
    finally:
        service.close()


def test_real_study_runtime_cli_executes_registered_analysis(tmp_path):
    evidence=tmp_path/'original.json'
    evidence.write_text(json.dumps({'status':'FAILED','configuration_id':'cfg','reason':'declared infrastructure negative input'}))
    data=document([{'work_id':'analysis','kind':'analysis','analysis_operation':'inspect',
        'evidence':[{'receipt_path':str(evidence),'configuration_id':'cfg'}]}])
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps(data))
    result=subprocess.run([sys.executable,'-m','research.experiments.study_scheduler','run',
        '--campaign',str(tmp_path),'--manifest',str(manifest),'--ledger',str(tmp_path/'study.sqlite'),
        '--ledger-id','test-ledger','--wait-timeout','5'],check=True,capture_output=True,text=True,timeout=8)
    value=json.loads(result.stdout)
    assert value['metrics']['completed_work_items']==1
    with sqlite3.connect(tmp_path/'study.sqlite') as db:
        state,raw=db.execute('SELECT state,result_json FROM forest_study_work WHERE work_id=\'analysis\'').fetchone()
    assert state=='completed'
    result=json.loads(raw)
    assert result['operation']=='inspect' and result['evidence'][0]['state']=='FAILED'
    assert result['evidence'][0]['metrics']=={}


def test_status_never_registers_new_manifest_work_or_downgrades_version(tmp_path):
    runtime=ChildRuntime(tmp_path,[{'work_id':'existing','kind':'analysis'}])
    with runtime.ledger.connect() as db:
        db.execute('UPDATE forest_study_campaigns SET manifest_version=5')
        before=db.execute('SELECT count(*) FROM forest_study_events').fetchone()[0]
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps(document([{'work_id':'new-unregistered','kind':'episode','context':'unaccepted'}])))
    result=subprocess.run([sys.executable,'-m','research.experiments.study_scheduler','status',
        '--campaign',str(tmp_path),'--manifest',str(manifest),'--ledger',str(runtime.ledger_path),
        '--ledger-id',runtime.ledger_id],capture_output=True,text=True,check=True,timeout=5)
    assert json.loads(result.stdout)['work_states']=={'queued':1}
    with runtime.ledger.connect() as db:
        assert db.execute('SELECT manifest_version FROM forest_study_campaigns').fetchone()[0]==5
        assert db.execute('SELECT count(*) FROM forest_study_events').fetchone()[0]==before
        assert db.execute('SELECT count(*) FROM forest_study_work').fetchone()[0]==1


@pytest.mark.parametrize('settled_state',['completed','failed','cancelled','not_run'])
def test_requires_settled_waits_for_original_terminal_outcome_including_failure(tmp_path,settled_state):
    runtime=ChildRuntime(tmp_path,[{'work_id':'source','kind':'fit'},
        {'work_id':'decision','kind':'calibration_decision'}])
    with runtime.ledger.connect() as db:
        db.execute('INSERT INTO forest_study_edges VALUES(?,?,?,?)',
            (runtime.campaign_id,'source','decision','requires_settled'))
    service=scheduler(runtime)
    try:
        assert service._ready_ids(('calibration_decision',),{})==[]
        runtime.ledger.set_state(runtime.campaign_id,'source',settled_state)
        assert service._ready_ids(('calibration_decision',),{})==['decision']
        assert runtime.claim_work('decision-owner',work_ids=['decision'])['work_id']=='decision'
    finally:
        service.close()
