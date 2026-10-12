"""Nonformal actual CPU publication/recovery fixtures; no model or GPU calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from research.experiments.gpu_supervisor import ComputeSupervisor
from research.experiments.study_runtime import StudyRuntime
from research.experiments.study_scheduler import StudyScheduler
from test_study_scheduler import ChildRuntime, document, rows, run_until, scheduler


def controller_fixture_client(item):
    """Importable scripted client; only explicitly nonformal OS worker fixtures."""
    from test_aide_runtime import NonformalClient, _review
    from research.experiments.end_to_end_runtime import _load
    payload = item['payload']
    root = Path(payload['nonformal_controller_fixture_root'])
    runtime = StudyRuntime(campaign=root, ledger=root / 'study.sqlite', ledger_id='nonformal-ledger',
        manifest=root / 'study_execution_manifest.json')
    request = runtime.build_episode_request({**payload, 'work_id': item['work_id']})
    state = _load(runtime, item['work_id'])
    action = 'select' if state['objective']['version'] == 2 and not state['comparison_records'] else 'inspect'
    binding = next(value for value in request['action_bindings']
        if value['work_id'] in request['current_action_work_ids'] and value['action_name'] == action)
    decision = {'mode': 'action', 'actions': [{'kind': action, 'action_work_id': binding['work_id']}],
        'submission': {}, 'rationale': 'Nonformal actual OS controller/recovery fixture'}
    if payload['arm'] == 'AIDE':
        program = 'import json\ndecision = ' + repr(decision) + '\nprint(json.dumps(decision))\n'
        responses = ['Nonformal native action.\n```python\n' + program + '```', _review(buggy=True)]
    else:
        responses = [json.dumps(decision)]
    client = NonformalClient(responses)
    original = client.complete
    def complete(messages, **kwargs):
        time.sleep(payload.get('nonformal_controller_delay', 0))
        return original(messages, **kwargs)
    client.complete = complete
    return client


def controller_fixture_runtime(root, arm='FULL', revised=True, delay=0):
    from test_aide_runtime import NonformalClient
    from test_end_to_end_runtime import fixture_runtime, _frontier, _complete_fixture_fit, _complete_fixture_score
    root.mkdir(parents=True, exist_ok=True)
    runtime, payload = fixture_runtime(root)
    upstream = Path(os.environ.get('FOREST_AIDE_UPSTREAM', str(root / 'executors' / 'aide_upstream')))
    if arm == 'AIDE' and not (upstream / 'aide' / 'agent.py').is_file():
        pytest.skip('Actual official AIDE clone not available; set FOREST_AIDE_UPSTREAM')
    payload.update(arm=arm, controller_restart={'required': True,
        'milestone': 'after_c1_checkpoint_before_confirmation'},
        nonformal_controller_fixture_root=str(root), nonformal_controller_delay=delay,
        aide_upstream_root=str(upstream),
        aide_search={'num_drafts': 1, 'debug_prob': 0.0, 'max_debug_depth': 3})
    manifest = runtime.manifest
    manifest.update(nonformal_fixture=True, model_provider=NonformalClient([]).provider, manifest_version=2)
    manifest['execution_state']['task_queue'] = [payload]
    runtime.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    runtime.reload_manifest()
    request = runtime.build_episode_request(payload)
    _complete_fixture_fit(runtime, _frontier(request, 'train')[0])
    request = runtime.build_episode_request(payload)
    _complete_fixture_score(runtime, _frontier(request, 'score')[0])
    if revised:
        _complete_fixture_fit(runtime, _frontier(request, 'train')[0], revised=.5)
        request = runtime.build_episode_request(payload)
        for binding in _frontier(request, 'score'):
            _complete_fixture_score(runtime, binding)
        runtime.build_episode_request(payload)
    return runtime, payload


def controller_service(runtime):
    return StudyScheduler(runtime, client_factory=controller_fixture_client, poll_seconds=.01)


def restart_row(runtime):
    with runtime.ledger.connect() as db:
        row = db.execute('SELECT * FROM forest_study_controller_restarts').fetchone()
    return dict(row) if row else None


def wait_controller_future(service, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if service._futures and all(future.done() for future in service._futures):
            for future in service._futures:
                future.result()
            return
        time.sleep(.02)
    raise AssertionError('Nonformal actual controller process did not publish completion')


@pytest.mark.parametrize('arm', ['FULL', 'AIDE'])
def test_production_controller_route_exits_and_reloads_actual_os_checkpoint(tmp_path, arm):
    from research.agents.processes import is_alive
    runtime, payload = controller_fixture_runtime(tmp_path, arm)
    service = controller_service(runtime)
    try:
        service.tick()
        wait_controller_future(service)
        checkpoint_row = restart_row(runtime)
        assert checkpoint_row['state'] == 'requested'
        assert checkpoint_row['request_pid'] != os.getpid()
        assert not is_alive(checkpoint_row['request_pid'], checkpoint_row['request_process_created'])
        before = json.loads(checkpoint_row['checkpoint_json'])
        assert len(before['model_calls']) == (2 if arm == 'AIDE' else 1)
        service.tick()
        wait_controller_future(service)
        restarted = restart_row(runtime)
        receipt = json.loads(restarted['receipt_json'])
        assert restarted['state'] == 'resumed'
        assert restarted['resume_pid'] not in (os.getpid(), restarted['request_pid'])
        assert receipt['previous_exit']['status'] == 'completed'
        assert receipt['previous_exit']['completion_receipt']
        assert receipt['checkpoint_receipt']['checkpoint'] == before
        assert Path(receipt['checkpoint_receipt']['artifact']['path']).is_file()
        assert receipt['checkpoint_reloaded'] is True
        assert receipt['original_children'] == before['children']
        assert receipt['original_model_calls'] == before['model_calls']
        assert receipt['reload_proof']['arm'] == arm
        if arm == 'AIDE':
            native = before['ordinary_state']['forest_study_aide_sessions']
            assert len(receipt['reload_proof']['native_nodes']) == native['completed_steps'] == 1
            assert receipt['reload_proof']['rng_state'] == native['rng_state']
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_controller_restarts').fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='controller_restart_resumed'").fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == len(before['model_calls']) * 2
            assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind='fit'").fetchone()[0] == 2
            assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='end_to_end_comparison_recorded'").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='forest_compute_work'").fetchone()[0] == 0
    finally:
        service.close()


def test_pre_c1_worker_change_is_not_the_required_restart(tmp_path):
    runtime, _ = controller_fixture_runtime(tmp_path, revised=False)
    service = controller_service(runtime)
    try:
        service.tick()
        wait_controller_future(service)
        assert restart_row(runtime) is None
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_controller_processes').fetchone()[0] == 1
    finally:
        service.close()


def test_missing_original_controller_completion_waits_without_new_query(tmp_path, monkeypatch):
    from research.experiments import study_scheduler
    runtime, _ = controller_fixture_runtime(tmp_path)
    service = controller_service(runtime)
    original = study_scheduler._original_controller_result
    delayed = tmp_path / 'delayed-controller-completion.json'
    held = []
    def hold_exit(folder, dispatch):
        observed, publication = original(folder, dispatch)
        if publication is not None and not held:
            completion = Path(observed['completion_receipt'])
            completion.replace(delayed)
            held.append(completion)
            return original(folder, dispatch)
        return observed, publication
    monkeypatch.setattr(study_scheduler, '_original_controller_result', hold_exit)
    try:
        service.tick()
        deadline = time.monotonic() + 15
        while not held and time.monotonic() < deadline:
            time.sleep(.002)
        row = restart_row(runtime)
        with runtime.ledger.connect() as db:
            folder = Path(db.execute('SELECT folder FROM forest_study_controller_processes').fetchone()[0])
        assert held
        state, result = original(folder, row['request_dispatch_id'])
        assert state['status'] == 'reconciliation' and result is None
        for _ in range(4):
            service.tick()
            time.sleep(.01)
        assert not any(future.done() for future in service._futures)
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM forest_study_controller_processes').fetchone()[0] == 1
        delayed.replace(held[0])
        wait_controller_future(service)
        service.tick()
        wait_controller_future(service)
        assert restart_row(runtime)['state'] == 'resumed'
    finally:
        service.close()


def test_changed_raw_checkpoint_cannot_satisfy_controller_restart(tmp_path):
    runtime, _ = controller_fixture_runtime(tmp_path)
    service = controller_service(runtime)
    try:
        service.tick()
        wait_controller_future(service)
        row = restart_row(runtime)
        with runtime.ledger.connect() as db:
            folder = Path(db.execute('SELECT folder FROM forest_study_controller_processes WHERE dispatch_id=?',
                (row['request_dispatch_id'],)).fetchone()[0])
        (folder / 'controller_checkpoint.json').write_text('{}', encoding='utf-8')
        service.tick()
        deadline = time.monotonic() + 15
        while not all(future.done() for future in service._futures) and time.monotonic() < deadline:
            time.sleep(.02)
        assert any('checkpoint publication is not bound' in str(future.exception()) for future in service._futures)
        assert restart_row(runtime)['state'] == 'requested'
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 1
    finally:
        service.close()


def test_same_os_pid_cannot_claim_the_required_controller_restart(tmp_path):
    from research.experiments.study_scheduler import _resume_controller_checkpoint
    runtime, payload = controller_fixture_runtime(tmp_path)
    service = controller_service(runtime)
    try:
        service.tick()
        wait_controller_future(service)
        row = restart_row(runtime)
        with pytest.raises(ValueError, match='distinct worker'):
            _resume_controller_checkpoint(runtime, {'work_id': payload['work_id'], 'kind': 'episode', 'payload': payload},
                'nonformal-same-pid', {'pid': row['request_pid'], 'process_created': row['request_process_created']})
        assert restart_row(runtime)['state'] == 'requested'
    finally:
        service.close()


def test_changed_durable_checkpoint_rejects_resume_before_another_model_call(tmp_path):
    runtime, _ = controller_fixture_runtime(tmp_path)
    service = controller_service(runtime)
    try:
        service.tick()
        wait_controller_future(service)
        with runtime.ledger.connect() as db:
            db.execute("UPDATE forest_study_work SET result_json='{}' WHERE kind='episode'")
        service.tick()
        deadline = time.monotonic() + 15
        while not all(future.done() for future in service._futures) and time.monotonic() < deadline:
            time.sleep(.02)
        errors = [future.exception() for future in service._futures]
        assert any('changed before controller reload' in str(error) for error in errors)
        assert restart_row(runtime)['state'] == 'requested'
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 1
    finally:
        service.close()


def crash_fixture_coordinator(root):
    """Actual coordinator exits while its independently managed worker lives."""
    runtime = StudyRuntime(campaign=Path(root), ledger=Path(root) / 'study.sqlite', ledger_id='nonformal-ledger',
        manifest=Path(root) / 'study_execution_manifest.json')
    service = controller_service(runtime)
    service.tick()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with runtime.ledger.connect() as db:
            row = db.execute('SELECT worker_pid FROM forest_study_controller_processes').fetchone()
        if row and row[0]:
            os._exit(23)
        time.sleep(.01)
    os._exit(24)


def test_actual_coordinator_death_observes_original_live_worker_without_resend(tmp_path):
    runtime, _ = controller_fixture_runtime(tmp_path, delay=.6)
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source), str(Path(__file__).parent)]))
    result = subprocess.run([sys.executable, '-c',
        'from test_study_scheduler_recovery import crash_fixture_coordinator; import sys; crash_fixture_coordinator(sys.argv[1])',
        str(tmp_path)], cwd=source, env=environment, timeout=15)
    assert result.returncode == 23
    with runtime.ledger.connect() as db:
        original = dict(db.execute('SELECT * FROM forest_study_controller_processes').fetchone())
    service = controller_service(runtime)
    try:
        service.acquire()
        assert len(service._futures) == 1
        assert service._unsettled_model_owners() == []
        assert next(iter(service._futures.values()))['dispatch_id'] == original['dispatch_id']
        wait_controller_future(service)
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_controller_processes').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 1
        service.tick()
        wait_controller_future(service)
        assert restart_row(runtime)['state'] == 'resumed'
    finally:
        service.close()


def crash_legacy_fixture_coordinator(root):
    import psutil
    runtime = ChildRuntime(Path(root), [
        {'work_id': 'a-original', 'kind': 'episode', 'context': 'nonformal original provider child', 'seconds': 1.5},
        {'work_id': 'b-ready', 'kind': 'episode', 'context': 'nonformal ready provider child', 'seconds': .05}])
    service = scheduler(runtime, maximum_model_concurrency=1, model_concurrency=1)
    service.tick()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        children = psutil.Process().children()
        if children:
            child = children[0]
            (Path(root) / 'original-provider-process.json').write_text(json.dumps(
                {'pid': child.pid, 'process_created': child.create_time()}), encoding='utf-8')
            os._exit(23)
        time.sleep(.005)
    os._exit(24)


def test_surviving_legacy_provider_retains_model_capacity_until_original_reconciled(tmp_path):
    from research.agents.processes import is_alive
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source), str(Path(__file__).parent)]))
    result = subprocess.run([sys.executable, '-c',
        'from test_study_scheduler_recovery import crash_legacy_fixture_coordinator; import sys; crash_legacy_fixture_coordinator(sys.argv[1])',
        str(tmp_path)], cwd=source, env=environment, timeout=10)
    assert result.returncode == 23
    original = json.loads((tmp_path / 'original-provider-process.json').read_text())
    assert is_alive(original['pid'], original['process_created'])
    runtime = ChildRuntime(tmp_path, [
        {'work_id': 'a-original', 'kind': 'episode', 'context': 'nonformal original provider child', 'seconds': 1.5},
        {'work_id': 'b-ready', 'kind': 'episode', 'context': 'nonformal ready provider child', 'seconds': .05}])
    service = scheduler(runtime, maximum_model_concurrency=1, model_concurrency=1)
    try:
        service.tick()
        assert service._unsettled_model_owners() == ['a-original']
        assert not service._futures
        assert next(row for row in rows(runtime) if row['work_id'] == 'b-ready')['state'] == 'queued'
        assert not (tmp_path / 'b-ready.json').exists()
        deadline = time.monotonic() + 5
        while not (tmp_path / 'a-original.json').is_file() and time.monotonic() < deadline:
            service.tick()
            time.sleep(.02)
        observed = json.loads((tmp_path / 'a-original.json').read_text())
        # Original actual CPU provider publication is manually reconciled in
        # this fixture; a dead child alone never proves the missing request.
        service.tick()
        assert not service._futures
        with runtime.ledger.connect() as db:
            db.execute("UPDATE forest_study_model_calls SET status='completed',response_json=?,finished_at=?,elapsed_seconds=? WHERE work_id='a-original'",
                (json.dumps(observed), observed['finished_at'], observed['finished_at']-observed['started_at']))
        runtime.ledger.set_state(runtime.campaign_id, 'a-original', 'completed', result=observed)
        run_until(service, lambda: next(row for row in rows(runtime) if row['work_id'] == 'b-ready')['state'] == 'completed')
        assert service._unsettled_model_owners() == []
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 2
    finally:
        service.close()


def test_nullable_original_wall_policy_is_not_an_experimental_stop(tmp_path):
    runtime = ChildRuntime(tmp_path, [])
    with runtime.ledger.connect() as db:
        db.execute('CREATE TABLE forest_compute_policies(ledger_id TEXT PRIMARY KEY,configuration_json TEXT)')
        db.execute('INSERT INTO forest_compute_policies VALUES(?,?)',
            (runtime.ledger_id, json.dumps({'wall_start': time.time()-100, 'wall_seconds': None})))
    service = scheduler(runtime)
    try:
        assert service._hard_allowance() is None
        assert service.tick()['state'] != 'budget_exhausted'
    finally:
        service.close()


PUBLISH = """
import json,pathlib,sys
out=pathlib.Path(sys.argv[1]);config=json.loads(pathlib.Path(sys.argv[2]).read_text())
(out/'effective_config.json').write_text(json.dumps(config))
(out/'checkpoint.pt').write_bytes(b'nonformal CPU fixture, not a trained checkpoint')
(out/'training.jsonl').write_text('{"nonformal_fixture":true}\\n')
(out/'validation_predictions.npz').write_bytes(b'nonformal CPU fixture, not scientific predictions')
(out/'execution_receipt.json').write_text(json.dumps({'status':'SUCCEEDED','work_id':config['work_id'],
 'effective_config':config,'hidden_test_opened':False,'nonformal_fixture':True,
 'validation_prediction_file':'validation_predictions.npz','validation_metrics':{}}))
print('Actual CPU publication fixture, not a scientific fit')
"""


def original_cpu_fit(root):
    root.mkdir(parents=True, exist_ok=True)
    output = root / 'original-output'
    output.mkdir()
    config = {'work_id': 'original-fit', 'phase': 'formal', 'nonformal_fixture': True, 'seed': 17}
    config_path = root / 'source-config.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    payload = {'configuration_path': str(config_path), 'output_directory': str(output), 'fit_timeout_seconds': 10,
               'effective_config': config}
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps(document([{'work_id': 'original-fit', 'kind': 'fit', 'payload': payload}])), encoding='utf-8')
    runtime = StudyRuntime(campaign=root, ledger=root / 'study.sqlite', ledger_id='test-ledger', manifest=manifest)
    supervisor = ComputeSupervisor(runtime.ledger_path, runtime.ledger_id)
    supervisor.configure(gpu_budget_seconds=100, wall_start=time.time(), wall_seconds=60)
    supervisor.submit([sys.executable, '-c', PUBLISH, str(output), str(config_path)], output,
        work_id='original-fit', timeout_seconds=10, gpu_devices=(),
        configuration={'phase': 'formal', 'effective_config': config, 'source_files': [str(config_path)]})
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = supervisor.status('original-fit')
        if status['status'] == 'completed':
            break
        time.sleep(.02)
    assert status['status'] == 'completed'
    runtime.ledger.set_state(runtime.campaign_id, 'original-fit', 'awaiting_reconciliation',
        supervisor_work_id='original-fit', result={'supervisor': {'status': 'reconciliation'}},
        error='Nonformal transient original publication fixture')
    return runtime, supervisor, output


def test_actual_cpu_receipt_publication_recovers_original_without_resubmit(tmp_path, monkeypatch):
    runtime, supervisor, output = original_cpu_fit(tmp_path)
    receipt = output / 'execution_receipt.json'
    delayed = tmp_path / 'delayed-receipt.json'
    receipt.replace(delayed)
    executions = []
    original = runtime.execute_fit
    def reconcile(item, **kwargs):
        executions.append(item['work_id'])
        return original(item, **kwargs)
    monkeypatch.setattr(runtime, 'execute_fit', reconcile)
    monkeypatch.setattr(ComputeSupervisor, 'submit', lambda *args, **kwargs: pytest.fail('Recovery submitted new physical work'))
    service = scheduler(runtime, gpu_devices=['nonformal-existing-CPU-job'])
    try:
        for _ in range(3):
            service.tick()
        assert rows(runtime)[0]['state'] == 'awaiting_reconciliation' and not executions
        with runtime.ledger.connect() as db:
            assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='original_execution_reconciliation_pending'").fetchone()[0] == 1
        delayed.replace(receipt)
        service.tick()
        assert rows(runtime)[0]['state'] == 'completed' and executions == ['original-fit']
        service.tick()
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_compute_work').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM forest_study_dispatches').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 0
    finally:
        service.close()


def test_unknown_original_completion_waits_then_reconciles_restored_bound_receipt(tmp_path, monkeypatch):
    runtime, supervisor, output = original_cpu_fit(tmp_path)
    completion = supervisor.directory / 'original-fit' / 'processes' / 'original-fit' / 'completion.json'
    delayed = tmp_path / 'delayed-completion.json'
    completion.replace(delayed)
    monkeypatch.setattr(ComputeSupervisor, 'submit', lambda *args, **kwargs: pytest.fail('Unknown work was resubmitted'))
    service = scheduler(runtime, gpu_devices=['nonformal-existing-CPU-job'])
    try:
        service.tick()
        assert rows(runtime)[0]['state'] == 'awaiting_reconciliation'
        delayed.replace(completion)
        service.tick()
        assert rows(runtime)[0]['state'] == 'completed'
    finally:
        service.close()


def test_changed_raw_artifact_does_not_promote_completed_physical_work(tmp_path):
    runtime, supervisor, output = original_cpu_fit(tmp_path)
    (output / 'checkpoint.pt').write_bytes(b'foreign edited checkpoint fixture')
    service = scheduler(runtime, gpu_devices=['nonformal-existing-CPU-job'])
    try:
        service.tick()
        assert rows(runtime)[0]['state'] == 'awaiting_reconciliation'
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_fit_attempts').fetchone()[0] == 0
    finally:
        service.close()


def test_pending_parent_wakes_only_after_children_settle_without_extra_query(tmp_path):
    runtime = ChildRuntime(tmp_path, [{'work_id': 'parent', 'kind': 'episode', 'context': 'saved'}])
    runtime.ledger.register_action(runtime.campaign_id, 'child', {'kind': 'fit', 'work_id': 'child'}, parent_id='parent')
    runtime.ledger.set_state(runtime.campaign_id, 'child', 'awaiting_reconciliation')
    runtime.ledger.set_state(runtime.campaign_id, 'parent', 'awaiting_reconciliation',
        decision={'child_id': 'child'}, result={'pending_work_ids': ['child']})
    service = scheduler(runtime)
    try:
        service.tick()
        assert next(row for row in rows(runtime) if row['work_id'] == 'parent')['state'] == 'awaiting_reconciliation'
        runtime.ledger.set_state(runtime.campaign_id, 'child', 'completed', result={'nonformal_fixture': True})
        run_until(service, lambda: next(row for row in rows(runtime) if row['work_id'] == 'parent')['state'] == 'completed')
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0] == 0
        assert json.loads(next(row for row in rows(runtime) if row['work_id'] == 'parent')['result_json'])['resumed_saved_decision'] is True
    finally:
        service.close()


def test_saved_completed_action_still_gets_client_for_next_actual_decision(tmp_path):
    action_id = 'nonformal-inspect'
    payload = {'arm': 'FULL', 'messages': [{'role': 'user', 'content': 'Nonformal two-turn fixture'}],
        'resource_policy': {'actual_gpu_seconds': 480, 'active_wall_seconds': 1800},
        'decision_schema': {'type': 'object', 'required': ['mode', 'actions', 'submission', 'rationale']},
        'allowed_decisions': ['inspect'], 'action_bindings': [{'work_id': action_id, 'kind': 'analysis', 'action_name': 'inspect',
            'analysis_operation': 'inspect', 'evidence': [{'work_id': 'nonformal-unrun', 'state': 'not_run', 'metrics': {}}]}]}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(document([{'work_id': 'parent', 'kind': 'episode', 'payload': payload}])), encoding='utf-8')
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / 'study.sqlite', ledger_id='test-ledger', manifest=manifest)
    calls = []
    class NonformalClient:
        provider = {'id': 'cpu-test', 'model': 'infrastructure-child'}
        config = {'reasoning_effort': 'medium'}
        timeout = 10
        def complete(self, messages, **kwargs):
            calls.append(messages)
            return {'text': json.dumps({'mode': 'action', 'actions': [{'kind': 'inspect', 'action_work_id': action_id}],
                'submission': {}, 'rationale': 'Nonformal actual common CPU operation'}), 'usage': {}, 'model': 'infrastructure-child'}
    service = StudyScheduler(runtime, client_factory=lambda item: NonformalClient(), poll_seconds=.01)
    try:
        service.tick()
        wait_controller_future(service)
        assert len(calls) == 1
        with runtime.ledger.connect() as db:
            assert db.execute('SELECT state FROM forest_study_work WHERE work_id=?', (action_id,)).fetchone()[0] == 'completed'
            parent = db.execute("SELECT state,decision_json FROM forest_study_work WHERE work_id='parent'").fetchone()
            assert parent['state'] == 'retryable'
            assert json.loads(parent['decision_json'])['mode'] == 'action'
        service.tick()
        wait_controller_future(service)
        assert len(calls) == 2
        with runtime.ledger.connect() as db:
            assert db.execute("SELECT count(*) FROM forest_study_model_calls WHERE status='completed'").fetchone()[0] == 2
    finally:
        service.close()


def test_two_local_contract_failures_block_only_the_work_source(tmp_path):
    attempts = []
    class LocalFailureRuntime(ChildRuntime):
        def run_claimed(self, item, **kwargs):
            if item['work_id'] == 'a-local-contract':
                attempts.append(item['work_id'])
                self.ledger.set_state(self.campaign_id, item['work_id'], 'retryable')
                raise ValueError('Nonformal local scientific binding mismatch')
            return super().run_claimed(item, **kwargs)
    runtime = LocalFailureRuntime(tmp_path, [
        {'work_id': 'a-local-contract', 'kind': 'episode', 'context': 'local'},
        {'work_id': 'b-independent', 'kind': 'episode', 'context': 'viable'}])
    service = scheduler(runtime)
    try:
        run_until(service, lambda: next(row for row in rows(runtime) if row['work_id'] == 'b-independent')['state'] == 'completed')
        for _ in range(3):
            service.tick()
        assert attempts == ['a-local-contract', 'a-local-contract']
        with runtime.ledger.connect() as db:
            circuits = [dict(row) for row in db.execute('SELECT * FROM forest_study_scheduler_circuits')]
        assert any(json.loads(row['source_id']).get('work_id') == 'a-local-contract' and row['state'] == 'blocked' for row in circuits)
        assert not any(json.loads(row['source_id']).get('failure_scope') == 'provider' and row['state'] == 'blocked' for row in circuits)
    finally:
        service.close()


@pytest.mark.parametrize('code,shared', [('transport_error', True), ('configuration', True), ('authorization', True),
                                       ('context_window_exceeded', False), ('incomplete_response', False)])
def test_provider_failure_scope_uses_structured_code(code, shared):
    from research.agents.provider import ProviderError
    assert StudyScheduler._provider_failure(ProviderError('Nonformal taxonomy fixture', code=code)) is shared
