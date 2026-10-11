"""Scientific checkpoint invariants and actual local process identity tests."""
import json
import sys
import time
import pytest
from research.agents.lifecycle import (initialize, checkpoint_round, load_accounting,
    save_session, ReconciliationRequired, progress_facts, digest)
from research.agents.runtime import ToolRuntime


def test_checkpoint_keeps_full_scientific_state_and_original_ledger_reference(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    state = {'run_id': 'original-run', 'transcript': [], 'totals': {'input_tokens': 99, 'cost': 2},
        'active_seconds': 12, 'execution_snapshot': {
            'external_ledger_id': 'original-supervisor',
            'goal_contract': {'goal': 'unchanged', 'success_conditions': ['validate']},
            'research_graph': {'nodes': [{'id': 'hypothesis'}], 'edges': [], 'revision': 5},
            'evidence_state': {'current': [{'artifact': 'immutable-original', 'sha256': 'hash'}], 'failures': ['observed']},
            'execution': {'stage': 'validation', 'task_queue': ['next'], 'pending_action': {'id': 'original-action'}},
            'experiment': {'configuration': {'seed': 17}, 'processes': [{'process_id': 'original-job'}]},
            'decision_history': [{'excluded': ['failed-path'], 'selected': 'next'}]}}
    initialize(state, {})
    checkpoint_round(state, reason='boundary')
    state['execution_snapshot']['execution']['next_operation'] = state['wait_for']
    save_session(state, workspace)
    restored = json.loads((workspace / 'agent_session.json').read_text())
    assert restored['execution_snapshot'] == state['execution_snapshot']
    assert not set(restored) & {'totals', 'active_seconds', 'cost_complete', 'usage_complete'}
    assert not set(restored['lifecycle']['checkpoints'][0]) & {'resource_totals', 'active_seconds'}
    assert not (workspace / 'agent_usage.json').exists()
    archive = json.loads(next((tmp_path / 'checkpoints').glob('*.json')).read_text())
    logical = archive['scientific_state']
    graph_ref = logical.pop('research_graph_reference')
    graph = json.loads(next((tmp_path / 'graph-snapshots').glob('*.json')).read_text())
    assert digest(graph) == graph_ref['sha256']
    logical['research_graph'] = graph
    assert digest(logical) == archive['checkpoint']['snapshot_sha256']
    assert archive['checkpoint']['snapshot_sha256'] == restored['lifecycle']['checkpoints'][-1]['snapshot_sha256']
    load_accounting(restored, workspace)
    assert restored['totals']['input_tokens'] == 99
    assert restored['execution_snapshot']['external_ledger_id'] == 'original-supervisor'


def test_ledger_ahead_of_checkpoint_requires_reconciliation_without_reset(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ledger = {'run_id': 'same', 'totals': {'input_tokens': 91}, 'accounted_transcript_steps': 2}
    (tmp_path / 'agent_usage.json').write_text(json.dumps(ledger))
    with pytest.raises(ReconciliationRequired, match='advanced'):
        load_accounting({'run_id': 'same', 'transcript': [{'step': 1}]}, workspace)
    assert json.loads((tmp_path / 'agent_usage.json').read_text()) == ledger


def test_unchanged_verified_file_pages_are_reused_without_protocol_reread(tmp_path):
    (tmp_path / 'protocol.txt').write_text('validated controls')
    tool = ToolRuntime('local-observation', tmp_path, config={'execution_backend': 'local'})
    first = tool.dispatch('read_file', {'path': 'protocol.txt', 'limit': 100}, None, 'read-one')
    second = tool.dispatch('read_file', {'path': 'protocol.txt', 'limit': 200}, None, 'read-two')
    assert second['unchanged_already_observed'] and second['prior_action_id'] == 'read-one'
    cache = json.loads((tmp_path / '.forest-read-cache.json').read_text())
    assert len(cache) == 1 and next(iter(cache.values()))['content'] == first['content']
    (tmp_path / 'protocol.txt').write_text('actually changed')
    changed = tool.dispatch('read_file', {'path': 'protocol.txt', 'limit': 100}, None, 'read-three')
    assert not changed.get('unchanged_already_observed')
    assert changed['sha256'] != first['sha256']


def test_notes_and_unrelated_file_churn_do_not_reset_cross_round_progress():
    context = {'evidence_fingerprint': 'original-evidence', 'nodes': []}
    state = {'execution_snapshot': {'artifact_hashes': {'unrelated.txt': {'sha256': 'one'}}, 'file_observations': {}}}
    before = digest(progress_facts(state, ['requested.py'], [], context))
    state['execution_snapshot']['artifact_hashes']['unrelated.txt']['sha256'] = 'two'
    state['messages'] = [{'content': 'Another plan claiming progress'}]
    assert digest(progress_facts(state, ['requested.py'], [], context)) == before


def test_new_action_reattaches_same_actual_job_and_does_not_repeat_effect(tmp_path):
    tool = ToolRuntime('actual-deduplication', tmp_path, config={'execution_backend': 'local'})
    command = [sys.executable, '-c', 'from pathlib import Path; p=Path("effects.txt"); p.write_text(p.read_text()+"x" if p.exists() else "x")']
    first = tool.dispatch('start_process', {'command': command}, None, 'action-one')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        receipt = tool.processes.inspect(first['process_id'])
        if receipt['status'] in ('completed', 'failed', 'lost', 'reconciliation'):
            break
        time.sleep(.02)
    assert receipt['status'] == 'completed'
    second = tool.dispatch('start_process', {'command': command}, None, 'action-two')
    assert second['process_id'] == first['process_id'] and second['status'] == 'completed'
    assert (tmp_path / 'effects.txt').read_text() == 'x'

def test_repeated_failed_jobs_and_comments_do_not_extend_research_fuse(tmp_path):
    from research.agents.lifecycle import artifact_manifest
    context = {'evidence_fingerprint': 'unchanged', 'nodes': []}
    (tmp_path / 'train.py').write_text('raise RuntimeError("same")\n')
    state = {'transcript': [], 'execution_snapshot': {'artifact_hashes': artifact_manifest({}, tmp_path, ['train.py'])}}
    first = digest(progress_facts(state, ['train.py'], [{'process_id': 'one', 'status': 'failed', 'exit_code': 1}], context))
    (tmp_path / 'train.py').write_text('# harmless comment\nraise RuntimeError("same")\n')
    state['execution_snapshot']['artifact_hashes'] = artifact_manifest({}, tmp_path, ['train.py'])
    second = digest(progress_facts(state, ['train.py'], [{'process_id': 'two', 'status': 'failed', 'exit_code': 1}], context))
    assert first == second


def test_new_original_context_coverage_advances_but_notes_do_not():
    context = {'evidence_fingerprint': 'same', 'nodes': []}
    pages = {'segments': {'protocol': {'sha256': 'immutable', 'origin': 'Original user controls'}}, 'coverage': {'protocol': [[0, 100]]}}
    state = {'execution_snapshot': {'context_observations': pages}}
    before = digest(progress_facts(state, [], [], context))
    pages['notes'] = ['A new summary']
    pages['segments']['notes'] = {'sha256': 'summary', 'progress_eligible': False}
    pages['coverage']['notes'] = [[0, 50]]
    assert digest(progress_facts(state, [], [], context)) == before
    pages['coverage']['protocol'] = [[0, 200]]
    assert digest(progress_facts(state, [], [], context)) != before


def test_reading_new_bookkeeping_or_authored_notes_cannot_create_progress():
    context = {'evidence_fingerprint': 'same', 'nodes': []}
    state = {'execution_snapshot': {'file_observations': {}, 'artifact_hashes': {'notes.md': {'sha256': 'new'}}}}
    before = digest(progress_facts(state, [], [], context))
    for path in ['agent_session.json', 'task_progress.json', 'agent_result.json', 'research_memory.json', '.forest-context/index.json', 'notes.md']:
        state['execution_snapshot']['file_observations'][path] = {'path': path, 'sha256': 'changed', 'offset': 0, 'next_offset': 100}
    state['execution_snapshot']['artifact_hashes']['notes/summary.md'] = {'sha256': 'new'}
    state['execution_snapshot']['file_observations']['nested'] = {'path': '/workspace/notes/summary.md', 'sha256': 'new', 'offset': 0, 'next_offset': 100}
    assert digest(progress_facts(state, [], [], context)) == before
    state['execution_snapshot']['file_observations']['protocol.txt'] = {'path': 'protocol.txt', 'sha256': 'original', 'offset': 0, 'next_offset': 100}
    assert digest(progress_facts(state, [], [], context)) != before


def test_mismatched_external_ledger_cannot_be_rebound(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    (tmp_path / 'agent_usage.json').write_text(json.dumps({'run_id': 'same', 'totals': {}, 'external_ledger_id': 'different'}))
    with pytest.raises(ReconciliationRequired, match='different supervisors'):
        load_accounting({'run_id': 'same', 'transcript': [], 'execution_snapshot': {'external_ledger_id': 'original'}}, workspace)

def test_corrupt_usage_ledger_requires_reconciliation_without_overwrite(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    path = tmp_path / 'agent_usage.json'; path.write_text('{broken')
    with pytest.raises(ReconciliationRequired, match='cannot be read'):
        load_accounting({'run_id': 'same', 'transcript': []}, workspace)
    assert path.read_text() == '{broken'


def test_successful_comment_only_reexecution_does_not_reset_progress():
    context = {'evidence_fingerprint': 'unchanged', 'nodes': []}
    state = {'execution_snapshot': {'job_bindings': {'action-one': {'job_id': 'job-one', 'progress_identity': 'same-semantics'},
        'action-two': {'job_id': 'job-two', 'progress_identity': 'same-semantics'}}}}
    one = {'process_id': 'job-one', 'configuration_hash': 'bytes-one', 'status': 'completed', 'exit_code': 0,
        'artifacts': [{'sha256': 'same-output', 'relative_path': 'stdout.log'}]}
    two = {**one, 'process_id': 'job-two', 'configuration_hash': 'bytes-two'}
    assert digest(progress_facts(state, [], [one], context)) == digest(progress_facts(state, [], [one, two], context))


def test_authored_numeric_metrics_are_not_progress_without_original_execution_artifact():
    context = {'evidence_fingerprint': 'same', 'nodes': []}
    state = {'execution_snapshot': {'artifact_hashes': {}}}
    before = digest(progress_facts(state, ['metrics.json'], [], context))
    state['execution_snapshot']['artifact_hashes']['metrics.json'] = {'sha256': 'fabricated'}
    assert digest(progress_facts(state, ['metrics.json'], [], context)) == before
    receipt = {'status': 'completed', 'exit_code': 0, 'command': ['actual-fit'],
               'artifacts': [{'relative_path': 'metrics.json', 'sha256': 'measured'}]}
    state['execution_snapshot']['artifact_hashes']['metrics.json']['sha256'] = 'measured'
    assert progress_facts(state, ['metrics.json'], [receipt], context)['task_outputs'] == {'metrics.json': 'measured'}
