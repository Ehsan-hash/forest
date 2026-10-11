"""Actual short CPU jobs prove ownership and metering without claiming GPU fits."""
import json
import fcntl
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from research.agents.processes import is_alive, read_json
from research.experiments.gpu_supervisor import ComputeSupervisor, resolve_gpu_devices


def configured(tmp_path, *, wall_seconds=120):
    ledger = tmp_path / 'study.sqlite'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE resource_ledger(entry_id TEXT PRIMARY KEY,resource_type TEXT,quantity REAL)')
        db.execute('INSERT INTO resource_ledger VALUES(?,?,?)', ('legacy-original', 'gpu_allocated_ms', 4211))
    supervisor = ComputeSupervisor(ledger, 'original-campaign:study.sqlite')
    supervisor.configure(gpu_budget_seconds=48 * 3600, wall_start=time.time(), wall_seconds=wall_seconds)
    workspace = tmp_path / 'work'; workspace.mkdir()
    return supervisor, workspace


def observed(supervisor, work_id, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = supervisor.status(work_id)
        if predicate(value):
            return value
        time.sleep(.02)
    pytest.fail('Actual supervised job did not reach expected state: ' + str(value))


def row(supervisor, work_id):
    with supervisor.connect() as db:
        return supervisor._row(db, work_id)


def count_command(seconds=.1):
    return [sys.executable, '-c', "from pathlib import Path;import time; p=Path('launches');p.write_text(str(int(p.read_text())+1) if p.exists() else '1');time.sleep(" + str(seconds) + ");Path('answer').write_text(str(sum(range(17))))"]


def test_original_ledger_and_actual_cpu_receipt_idempotency(tmp_path):
    supervisor, workspace = configured(tmp_path)
    command = count_command()
    supervisor.submit(command, workspace, work_id='original', timeout_seconds=10, configuration={'seed': 17}, phase='pilot')
    result = supervisor.wait('original', timeout=10)
    assert result['status'] == 'completed'
    assert (workspace / 'answer').read_text() == '136'
    assert result['legacy_gpu_ms'] == result['gpu_ms'] == 4211
    assert result['new_gpu_ms'] == 0
    assert result['elapsed_ms'] > 0
    receipt = read_json(result['observation']['completion_receipt'])
    assert receipt['work_id'] == 'original'
    assert any(item['relative_path'] == 'answer' and Path(item['path']).read_text() == '136' for item in receipt['artifacts'])
    original_launch = result['observation']['launch_id']
    for _ in range(3):
        replay = supervisor.submit(command, workspace, work_id='original', timeout_seconds=10, configuration={'seed': 17}, phase='pilot')
        assert replay['observation']['launch_id'] == original_launch
        assert replay['elapsed_ms'] == result['elapsed_ms']
    assert (workspace / 'launches').read_text() == '1'
    with supervisor.connect() as db:
        assert db.execute("SELECT SUM(quantity) FROM forest_compute_usage WHERE resource_type='cpu_ms'").fetchone()[0] == result['elapsed_ms']
        assert tuple(db.execute('SELECT * FROM resource_ledger').fetchone()) == ('legacy-original', 'gpu_allocated_ms', 4211)
    with pytest.raises(ValueError, match='different original execution'):
        supervisor.submit(command, workspace, work_id='original', timeout_seconds=10, configuration={'seed': 18})


def test_meter_process_crash_reconnects_original_job_and_charges_once(tmp_path):
    supervisor, workspace = configured(tmp_path)
    supervisor.submit(count_command(1.5), workspace, work_id='continued', timeout_seconds=10)
    before = observed(supervisor, 'continued', lambda value: value['status'] == 'running' and (workspace / 'launches').exists())
    original = before['observation']
    meter = row(supervisor, 'continued')
    os.kill(meter['supervisor_pid'], signal.SIGKILL)
    deadline = time.monotonic() + 2
    while is_alive(meter['supervisor_pid'], meter['supervisor_created']) and time.monotonic() < deadline:
        time.sleep(.02)
    restarted = ComputeSupervisor(supervisor.ledger, supervisor.ledger_id)
    result = restarted.wait('continued', timeout=10)
    assert result['status'] == 'completed'
    assert result['observation']['launch_id'] == original['launch_id']
    assert result['observation']['pid'] == original['pid']
    assert (workspace / 'launches').read_text() == '1'
    assert result['elapsed_ms'] >= 1400
    with restarted.connect() as db:
        assert db.execute('SELECT SUM(quantity) FROM forest_compute_usage WHERE work_id=?', ('continued',)).fetchone()[0] == result['elapsed_ms']
    assert restarted.status('continued')['elapsed_ms'] == result['elapsed_ms']


def test_accepted_launch_crash_gap_reconciles_without_new_execution(tmp_path, monkeypatch):
    supervisor, workspace = configured(tmp_path)
    monkeypatch.setattr(supervisor, '_ensure_supervisor', lambda work_id: None)
    supervisor.submit(count_command(), workspace, work_id='uncertain', timeout_seconds=10)
    with supervisor.transaction() as db:
        db.execute("UPDATE forest_compute_work SET launch_requested=1,state='launching',effective_timeout=10 WHERE work_id='uncertain'")
    restarted = ComputeSupervisor(supervisor.ledger, supervisor.ledger_id)
    result = restarted.wait('uncertain', timeout=5)
    assert result['status'] == 'reconciliation'
    assert not (workspace / 'launches').exists()
    assert not (supervisor.directory / 'uncertain' / 'processes' / 'uncertain' / 'request.json').exists()
    assert restarted.wait('uncertain', timeout=1)['status'] == 'reconciliation'


def test_live_owner_waits_original_request_publication_without_false_reconciliation(tmp_path):
    supervisor, workspace = configured(tmp_path)
    folder = supervisor.directory / 'publishing' / 'processes' / 'publishing'
    folder.mkdir(parents=True)
    # Hold the actual launcher's request lease to reproduce its accepted DB
    # intent before the original process request is atomically published.
    with (folder / 'request.lease').open('a') as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        supervisor.submit(count_command(), workspace, work_id='publishing', timeout_seconds=10)
        pending = observed(supervisor, 'publishing', lambda value: value['observation'].get('pending_original_launch'))
        assert pending['status'] == 'starting'
        assert not (folder / 'request.json').exists()
        assert is_alive(row(supervisor, 'publishing')['supervisor_pid'], row(supervisor, 'publishing')['supervisor_created'])
        fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
    assert supervisor.wait('publishing', timeout=10)['status'] == 'completed'
    assert (workspace / 'launches').read_text() == '1'


def test_unreadable_receipt_keeps_original_time_and_then_recovers(tmp_path):
    supervisor, workspace = configured(tmp_path)
    supervisor.submit(count_command(1), workspace, work_id='receipt', timeout_seconds=10)
    before = observed(supervisor, 'receipt', lambda value: value['status'] == 'running' and (workspace / 'launches').exists())
    original = before['observation']['launch_id']
    manager = supervisor._manager(row(supervisor, 'receipt'))
    (manager.folder('receipt') / 'completion.json').write_text('{broken')
    uncertain = supervisor.wait('receipt', timeout=2)
    assert uncertain['status'] == 'reconciliation'
    time.sleep(.15)
    assert supervisor.status('receipt')['elapsed_ms'] > uncertain['elapsed_ms']
    deadline = time.monotonic() + 5
    while manager.inspect('receipt')['status'] == 'reconciliation' and time.monotonic() < deadline:
        time.sleep(.02)
    result = supervisor.wait('receipt', timeout=5)
    assert result['status'] == 'completed'
    assert result['observation']['launch_id'] == original
    assert (workspace / 'launches').read_text() == '1'


@pytest.mark.parametrize('corruption', ['completion', 'source'])
def test_unknown_first_meter_reads_bound_original_clock_without_success(tmp_path, corruption):
    supervisor, workspace = configured(tmp_path)
    source = tmp_path / 'installed.py'; source.write_text(count_command(1)[2])
    supervisor.submit([sys.executable, str(source)], workspace, work_id='first-observation', timeout_seconds=10,
        configuration={'source_files': [str(source)]})
    observed(supervisor, 'first-observation', lambda value: value['status'] == 'running' and (workspace / 'launches').exists())
    meter = row(supervisor, 'first-observation')
    os.kill(meter['supervisor_pid'], signal.SIGKILL)
    manager = supervisor._manager(meter)
    if corruption == 'completion':
        (manager.folder('first-observation') / 'completion.json').write_text('{broken')
    else:
        Path(json.loads(meter['configuration_json'])['source_originals'][0]['snapshot_path']).unlink()
    # Simulate interruption before the meter's first SQLite clock update, while
    # retaining the actual launch request/state written by its independent host.
    with supervisor.transaction() as db:
        db.execute('DELETE FROM forest_compute_usage WHERE work_id=?', ('first-observation',))
        db.execute('UPDATE forest_compute_work SET started_at=NULL,accounted_ms=0 WHERE work_id=?', ('first-observation',))
    uncertain = supervisor.status('first-observation')
    assert uncertain['status'] == 'reconciliation' and uncertain['elapsed_ms'] > 0
    assert uncertain['observation']['started_at'] == read_json(manager.folder('first-observation') / 'state.json')['started_at']
    deadline = time.monotonic() + 5
    while manager.inspect('first-observation')['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
        time.sleep(.02)
    if corruption == 'completion':
        assert supervisor.wait('first-observation', timeout=5)['status'] == 'completed'
    else:
        stopped = supervisor.status('first-observation')
        assert stopped['status'] == 'reconciliation' and stopped['confirmed_execution_status'] == 'completed'
        time.sleep(.12)
        assert supervisor.status('first-observation')['elapsed_ms'] == stopped['elapsed_ms']
    assert (workspace / 'launches').read_text() == '1'


def test_actual_timeout_stops_term_resistant_original_and_preserves_usage(tmp_path):
    supervisor, workspace = configured(tmp_path)
    command = [sys.executable, '-c', "import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('started').write_text('1');time.sleep(15)"]
    supervisor.submit(command, workspace, work_id='bounded', timeout_seconds=.5)
    result = supervisor.wait('bounded', timeout=8)
    assert result['status'] == 'budget_exhausted'
    assert result['elapsed_ms'] >= 500
    assert result['elapsed_ms'] < 2500
    assert not is_alive(result['observation']['pid'], result['observation']['process_created'])
    assert result['gpu_ms'] == 4211
    assert read_json(result['observation']['completion_receipt'])['status'] in ('failed', 'cancelled')


def test_explicit_source_revision_preserves_original_and_allows_new_work(tmp_path):
    supervisor, workspace = configured(tmp_path)
    source = tmp_path / 'installed.py'
    source.write_text("from pathlib import Path;Path('result').write_text('old')")
    command = [sys.executable, str(source)]
    configuration = {'source_files': [str(source)], 'seed': 7}
    supervisor.submit(command, workspace, work_id='version-one', timeout_seconds=10, configuration=configuration)
    first = supervisor.wait('version-one', timeout=10)
    source.write_text("from pathlib import Path;Path('result').write_text('new')")
    replay = supervisor.submit(command, workspace, work_id='version-one', timeout_seconds=10, configuration=configuration)
    assert replay['status'] == 'completed'
    assert replay['observation']['launch_id'] == first['observation']['launch_id']
    assert 'old' in Path(first['configuration']['source_originals'][0]['snapshot_path']).read_text()
    supervisor.submit(command, workspace, work_id='version-two', timeout_seconds=10, configuration=configuration)
    second = supervisor.wait('version-two', timeout=10)
    assert second['status'] == 'completed' and (workspace / 'result').read_text() == 'new'
    assert second['configuration']['source_originals'][0]['revision'] == 2
    assert any(item['relative_path'] == 'result' and Path(item['path']).read_text() == 'old' for item in first['observation']['artifacts'])


def test_source_edit_before_original_launch_is_not_executed(tmp_path, monkeypatch):
    supervisor, workspace = configured(tmp_path)
    source = tmp_path / 'installed.py'; source.write_text("from pathlib import Path;Path('executed').write_text('old')")
    monkeypatch.setattr(supervisor, '_ensure_supervisor', lambda work_id: None)
    supervisor.submit([sys.executable, str(source)], workspace, work_id='edited', timeout_seconds=10, configuration={'source_files': [str(source)]})
    source.write_text("from pathlib import Path;Path('executed').write_text('changed')")
    restarted = ComputeSupervisor(supervisor.ledger, supervisor.ledger_id)
    result = restarted.wait('edited', timeout=5)
    assert result['status'] == 'reconciliation'
    assert not (workspace / 'executed').exists()


def test_cli_passes_explicit_execution_environment(tmp_path):
    supervisor, workspace = configured(tmp_path)
    command = [sys.executable, '-c', "import os;from pathlib import Path;Path('environment').write_text(os.environ['FOREST_RESEARCH_INPUT']);Path('cuda').write_text(os.environ['CUDA_VISIBLE_DEVICES'])"]
    base = [sys.executable, '-m', 'research.experiments.gpu_supervisor']
    flags = ['--ledger', str(supervisor.ledger), '--ledger-id', supervisor.ledger_id, '--work-id', 'cli']
    subprocess.run(base + ['submit', *flags, '--workspace', str(workspace), '--command-json', json.dumps(command),
        '--env-json', json.dumps({'FOREST_RESEARCH_INPUT': 'actual', 'CUDA_VISIBLE_DEVICES': '0'}), '--timeout-seconds', '10'], check=True, capture_output=True, text=True)
    completed = subprocess.run(base + ['wait', *flags, '--wait-timeout', '10'], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout)['status'] == 'completed'
    assert (workspace / 'environment').read_text() == 'actual'
    assert (workspace / 'cuda').read_text() == ''


def test_expired_actual_wall_does_not_launch_and_policy_versions_retain_ledger(tmp_path):
    supervisor, workspace = configured(tmp_path)
    supervisor.configure(gpu_budget_seconds=48 * 3600, wall_start=time.time() - 100, wall_seconds=1)
    result = supervisor.submit(count_command(), workspace, work_id='wall-expired', timeout_seconds=10)
    if result['status'] not in ('budget_exhausted',):
        result = supervisor.wait('wall-expired', timeout=5)
    assert result['status'] == 'budget_exhausted' and not (workspace / 'launches').exists()
    assert result['gpu_ms'] == 4211
    with supervisor.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM forest_compute_policy_history').fetchone()[0] == 2


def test_allocation_unit_refreshes_other_device_charge_before_remaining_reservation(tmp_path):
    # Constructed resource-policy records, not an executed or claimed GPU fit.
    # A stale first meter must not disappear between consumed and held totals.
    supervisor, workspace = configured(tmp_path)
    supervisor.configure(gpu_budget_seconds=50, wall_start=time.time(), wall_seconds=120)
    conditions = {'version': 1, 'command': ['/bin/true'], 'workspace': str(workspace), 'cwd': '.',
        'env': {}, 'timeout_seconds': 100, 'gpu_devices': ['0'], 'research_configuration': {},
        'phase': 'unit-policy', 'code_inputs': None}
    with supervisor.transaction() as db:
        db.execute('INSERT INTO forest_compute_work(work_id,ledger_id,configuration_json,state,accepted_at,launch_requested,started_at,effective_timeout) VALUES(?,?,?,?,?,?,?,?)',
            ('prior', supervisor.ledger_id, json.dumps(conditions), 'running', time.time() - 7, 1, time.time() - 7, 10))
        new_conditions = {**conditions, 'gpu_devices': ['1']}
        db.execute('INSERT INTO forest_compute_work(work_id,ledger_id,configuration_json,state,accepted_at) VALUES(?,?,?,?,?)',
            ('next', supervisor.ledger_id, json.dumps(new_conditions), 'accepted', time.time()))
        effective, reason = supervisor._allocation(db, supervisor._row(db, 'next'), supervisor._policy(db))
        assert reason is None
        # prior total commitment=10s execution+3.25s termination; next reserves
        # its own 3.25s. Its consumed interval is present exactly once.
        assert effective == pytest.approx(50 - 4.211 - 10 - 3.25 - 3.25)
        assert supervisor._totals(db)['new_gpu_ms'] >= 7000


def test_logical_ledger_revision_cannot_reset_shared_physical_consumption(tmp_path):
    supervisor, workspace = configured(tmp_path)
    with supervisor.transaction() as db:
        db.execute('INSERT INTO forest_compute_usage VALUES(?,?,?,?,?,?,?,?)',
            ('original-new-entry', supervisor.ledger_id, 'historical-work', 'gpu_allocated_ms', 1000, 1000,
             json.dumps({'test_record': 'isolated resource-policy fixture'}), time.time()))
    second = ComputeSupervisor(supervisor.ledger, 'new-owner-revision')
    second.configure(gpu_budget_seconds=48 * 3600, wall_start=time.time(), wall_seconds=120)
    assert second.status()['gpu_ms'] == 5211


def test_gpu_alias_resolution_unit_shares_actual_physical_identity():
    inventory = '0, GPU-first\n1, GPU-second\n'
    assert resolve_gpu_devices(['0'], inventory)[0] == resolve_gpu_devices(['GPU-first'], inventory)[0] == ['GPU-first']
    assert resolve_gpu_devices(['1', '0'], inventory)[0] == ['GPU-second', 'GPU-first']
    with pytest.raises(ValueError, match='same physical GPU twice'):
        resolve_gpu_devices(['0', 'GPU-first'], inventory)
    with pytest.raises(ValueError, match='unavailable'):
        resolve_gpu_devices(['2'], inventory)


def test_changed_historical_artifact_reports_integrity_without_new_compute(tmp_path):
    supervisor, workspace = configured(tmp_path)
    supervisor.submit(count_command(), workspace, work_id='historical', timeout_seconds=10)
    completed = supervisor.wait('historical', timeout=10)
    artifact = next(item for item in completed['observation']['artifacts'] if item['relative_path'] == 'answer')
    Path(artifact['path']).write_text('changed historical evidence')
    first = supervisor.status('historical')
    time.sleep(.12)
    second = supervisor.status('historical')
    assert first['status'] == second['status'] == 'reconciliation'
    assert second['confirmed_execution_status'] == 'completed'
    assert first['elapsed_ms'] == second['elapsed_ms'] == completed['elapsed_ms']
    assert row(supervisor, 'historical')['state'] == 'completed'
    assert json.loads(row(supervisor, 'historical')['receipt_json'])['finished_at'] == completed['observation']['finished_at']


def test_stop_margin_cannot_underreserve_independent_host_termination(tmp_path):
    supervisor, workspace = configured(tmp_path)
    with pytest.raises(ValueError, match='independent process host stop latency'):
        supervisor.configure(gpu_budget_seconds=10, wall_start=time.time(), wall_seconds=60, termination_margin_seconds=.1)


def test_missing_retained_source_is_not_success_and_does_not_recharge(tmp_path):
    supervisor, workspace = configured(tmp_path)
    source = tmp_path / 'installed.py'; source.write_text("from pathlib import Path;Path('answer').write_text('real')")
    supervisor.submit([sys.executable, str(source)], workspace, work_id='source-proof', timeout_seconds=10, configuration={'source_files': [str(source)]})
    completed = supervisor.wait('source-proof', timeout=10)
    Path(completed['configuration']['source_originals'][0]['snapshot_path']).unlink()
    uncertain = supervisor.status('source-proof')
    assert uncertain['status'] == 'reconciliation'
    assert uncertain['elapsed_ms'] == completed['elapsed_ms']
    assert uncertain['confirmed_execution_status'] == 'completed'


def test_aggregate_only_observation_freezes_genuine_stop_before_artifact_error(tmp_path):
    supervisor, workspace = configured(tmp_path)
    supervisor.submit(count_command(.4), workspace, work_id='aggregate', timeout_seconds=10)
    observed(supervisor, 'aggregate', lambda value: value['status'] == 'running' and (workspace / 'launches').exists())
    meter = row(supervisor, 'aggregate'); os.kill(meter['supervisor_pid'], signal.SIGKILL)
    manager = supervisor._manager(meter)
    deadline = time.monotonic() + 5
    while manager.inspect('aggregate')['status'] != 'completed' and time.monotonic() < deadline:
        time.sleep(.02)
    supervisor.status()  # Store the real original stop through aggregate reader.
    measured = row(supervisor, 'aggregate')['accounted_ms']
    original = read_json(manager.folder('aggregate') / 'completion.json')
    artifact = next(item for item in original['artifacts'] if item.get('relative_path') == 'answer')
    Path(artifact['path']).write_text('changed after aggregate completion')
    first = supervisor.status('aggregate'); time.sleep(.12)
    second = supervisor.status('aggregate')
    assert first['status'] == second['status'] == 'reconciliation'
    assert first['elapsed_ms'] == second['elapsed_ms'] == measured
