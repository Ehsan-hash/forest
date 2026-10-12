"""Atomic process publications must be observed as a consistent generation.

These tests execute one actual short CPU command and retain its original
records. Republishing those records at a controlled reader boundary makes
the host's multi-file publication race deterministic without a substitute
execution transport or scientific result.
"""
import json
import sys
from pathlib import Path

import pytest

from research.agents import processes
from research.agents.processes import ManagedProcesses, atomic_json
from test_agent_processes import completed


@pytest.fixture
def observed_job(tmp_path):
    manager = ManagedProcesses(tmp_path)
    command = [sys.executable, '-c',
               "from pathlib import Path; p=Path('launch_count'); p.write_text('x')"]
    process_id = 'publication-observation'
    result = completed(manager, manager.start(command, process_id=process_id)['process_id'])
    assert result['status'] == 'completed'
    folder = manager.folder(process_id)
    originals = {name: json.loads((folder / (name + '.json')).read_text())
                 for name in ('request', 'state', 'identity', 'completion')}
    return manager, process_id, folder, originals


@pytest.mark.parametrize('publish_completion', [False, True])
def test_absent_state_then_atomic_identity_publication_retries_original_snapshot(
        observed_job, monkeypatch, publish_completion):
    manager, process_id, folder, originals = observed_job
    for name in ('state', 'identity', 'completion'):
        (folder / (name + '.json')).unlink()
    original_read = processes.read_json
    state_reads = 0

    def publish_after_absent_state(path, default=None):
        nonlocal state_reads
        value = original_read(path, default)
        if Path(path) == folder / 'state.json':
            state_reads += 1
            if state_reads == 1:
                assert value == {'status': 'starting'}
                running = {key: value for key, value in originals['state'].items()
                           if key not in ('exit_code', 'finished_at', 'elapsed_seconds')}
                running['status'] = 'running'
                atomic_json(folder / 'state.json', running)
                atomic_json(folder / 'identity.json', originals['identity'])
                if publish_completion:
                    # The real host commits its original receipt before state.
                    atomic_json(folder / 'completion.json', originals['completion'])
                    atomic_json(folder / 'state.json', originals['state'])
        return value

    monkeypatch.setattr(processes, 'read_json', publish_after_absent_state)
    observed = manager.inspect(process_id)
    assert observed['status'] == ('completed' if publish_completion else 'running')
    assert observed['launch_id'] == originals['identity']['launch_id']
    assert observed['configuration'] == originals['request']['configuration']
    assert state_reads >= 2
    assert (manager.workspace / 'launch_count').read_text() == 'x'


@pytest.mark.parametrize('record_name', ['state', 'identity', 'completion'])
def test_stable_original_binding_mismatch_remains_reconciliation(
        observed_job, monkeypatch, record_name):
    manager, process_id, folder, originals = observed_job
    changed = {**originals[record_name], 'configuration_version': 2}
    atomic_json(folder / (record_name + '.json'), changed)
    original_read = processes.read_json
    state_reads = 0

    def count_reads(path, default=None):
        nonlocal state_reads
        if Path(path) == folder / 'state.json':
            state_reads += 1
        return original_read(path, default)

    monkeypatch.setattr(processes, 'read_json', count_reads)
    observed = manager.inspect(process_id)
    assert observed['status'] == 'reconciliation'
    assert 'work records disagree' in observed['error']
    assert state_reads == 1
    assert (manager.workspace / 'launch_count').read_text() == 'x'


def test_stable_unreadable_original_receipt_remains_reconciliation(observed_job):
    manager, process_id, folder, _ = observed_job
    (folder / 'completion.json').write_text('{not valid JSON')
    observed = manager.inspect(process_id)
    assert observed['status'] == 'reconciliation'
    assert 'unreadable' in observed['error']


def test_stable_other_launch_identity_remains_reconciliation(observed_job):
    manager, process_id, folder, originals = observed_job
    atomic_json(folder / 'identity.json',
                {**originals['identity'], 'launch_id': 'different-original-launch'})
    observed = manager.inspect(process_id)
    assert observed['status'] == 'reconciliation'
    assert 'does not belong to the committed launch' in observed['error']


def test_stable_missing_completion_remains_reconciliation(observed_job):
    manager, process_id, folder, _ = observed_job
    (folder / 'completion.json').unlink()
    observed = manager.inspect(process_id)
    assert observed['status'] == 'reconciliation'
    assert 'no consistent completion receipt' in observed['error']


def test_changed_unreadable_receipt_retries_republished_original(observed_job, monkeypatch):
    manager, process_id, folder, originals = observed_job
    (folder / 'completion.json').write_text('{not valid JSON')
    original_read = processes.read_json
    receipt_reads = 0

    def repair_during_read(path, default=None):
        nonlocal receipt_reads
        if Path(path) == folder / 'completion.json':
            receipt_reads += 1
            if receipt_reads == 1:
                try:
                    return original_read(path, default)
                finally:
                    atomic_json(folder / 'completion.json', originals['completion'])
        return original_read(path, default)

    monkeypatch.setattr(processes, 'read_json', repair_during_read)
    observed = manager.inspect(process_id)
    assert observed['status'] == 'completed'
    assert receipt_reads == 2
    assert (manager.workspace / 'launch_count').read_text() == 'x'


def test_continuous_atomic_publication_has_bounded_reads(observed_job, monkeypatch):
    manager, process_id, folder, originals = observed_job
    original_read = processes.read_json
    state_reads = 0

    def republish_every_read(path, default=None):
        nonlocal state_reads
        value = original_read(path, default)
        if Path(path) == folder / 'state.json':
            state_reads += 1
            atomic_json(folder / 'state.json', originals['state'])
        return value

    monkeypatch.setattr(processes, 'read_json', republish_every_read)
    observed = manager.inspect(process_id)
    assert observed['status'] == 'reconciliation'
    assert 'publication' in observed['error']
    assert 2 <= state_reads <= 4
    assert (manager.workspace / 'launch_count').read_text() == 'x'
