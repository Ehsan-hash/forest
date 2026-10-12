"""Durable work rounds without a cumulative research lifetime allowance."""
from __future__ import annotations

from copy import deepcopy
import json
import time
import uuid
from pathlib import Path

from services.api.common import safe_path

DEFAULT_ROUND_STEPS = 30
_MANIFEST_CACHE = {}


def initialize(state, config):
    policy = config.get('execution_round') or {}
    if not isinstance(policy, dict):
        raise ValueError('execution_round must be an object')
    legacy = config.get('step_budget_policy') or {}
    steps = policy.get('steps', config.get('round_steps'))
    if steps is None:
        steps = (config.get('agent_budget') or {}).get('steps')
    if steps is None:
        steps = config.get('max_steps', legacy.get('initial_steps', DEFAULT_ROUND_STEPS))
    if steps is None:
        steps = DEFAULT_ROUND_STEPS
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError('execution_round.steps must be a positive integer work-round hint')
    lifecycle = state.setdefault('lifecycle', {
        'version': 1, 'state': 'running', 'round_index': 1,
        'research_state': 'active', 'round_state': 'running',
        'round_start_step': len(state.get('transcript', [])),
        'checkpoints': [], 'recovery': {},
    })
    lifecycle['round_steps'] = steps
    if 'dynamic_step_budget' in state:
        state.pop('dynamic_step_budget')
        lifecycle.setdefault('migration', 'legacy_step_allowance_to_work_rounds')
    return lifecycle


def round_due(state):
    life = state['lifecycle']
    return (not state.get('pending_action') and
            len(state.get('transcript', [])) - life['round_start_step'] >= life['round_steps'])


def resume_round(state):
    wait = state.get('wait_for') or {}
    if wait.get('type') != 'work_round':
        return False
    life = state['lifecycle']
    if life.get('resumed_checkpoint_id') != wait['checkpoint_id']:
        life.update(round_index=life['round_index'] + 1,
                    round_start_step=len(state.get('transcript', [])),
                    resumed_checkpoint_id=wait['checkpoint_id'])
    life['state'] = 'running'
    life['round_state'] = 'running'
    state['wait_for'] = None
    state.pop('budget_reason', None)
    return True


def artifact_manifest(state, workspace, required):
    """Record output versions and original references without reading datasets."""
    from .artifacts import record_file
    names = set(required)
    for turn in state.get('transcript', []):
        action = turn.get('executed_action') or {}
        if action.get('tool') in {'write_file', 'write_file_chunk', 'python'}:
            name = (action.get('arguments') or {}).get('path')
            if name:
                names.add(name)
    manifest = {}
    for name in sorted(names):
        try:
            path = safe_path(Path(workspace), name)
            if path.is_file():
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                cached = _MANIFEST_CACHE.get(str(path))
                if cached and cached[0] == identity:
                    manifest[name] = deepcopy(cached[1])
                    continue
                manifest[name] = record_file(path, Path(workspace).parent / 'artifact-records', relative_path=name)
                _MANIFEST_CACHE[str(path)] = (identity, deepcopy(manifest[name]))
        except (ValueError, OSError):
            manifest[name] = {'unavailable': True}
    return manifest


def progress_facts(state, required, processes, research_context, *, metrics_file=None):
    """External observations excluding counters, notes and arbitrary file churn."""
    reads = state.get('execution_snapshot', {}).get('file_observations', {})
    outputs = state.get('execution_snapshot', {}).get('artifact_records', {})
    generated = set(outputs) | set(required)
    generated.update(artifact.get('relative_path') for process in processes
                     for artifact in process.get('artifacts', []))
    from .processes import _CHECKPOINT_FILES
    bookkeeping = _CHECKPOINT_FILES | {'agent_job_bindings.json'}
    def original_observation(value):
        path = Path(value.get('path') or value.get('resolved_path') or '')
        relative = value.get('workspace_relative_path')
        # Old cached observations have no canonical workspace-relative field.
        # Match whole path components, including absolute reads of nested
        # generated files, so spelling a path differently cannot renew progress.
        authored = relative in generated if relative else any(
            str(path).replace('\\', '/').endswith('/' + str(name).replace('\\', '/'))
            for name in generated if name)
        return (path.name not in bookkeeping and
                not any(part.startswith('.forest-') for part in path.parts) and
                str(path) not in generated and path.name not in generated and not authored)
    pages = sorted((key, value.get('file_revision'), value.get('offset'), value.get('next_offset'))
                   for key, value in reads.items() if original_observation(value))
    context_pages = state.get('execution_snapshot', {}).get('context_observations', {})
    def original_segment(ident):
        metadata = context_pages.get('segments', {}).get(ident, {})
        if 'progress_eligible' in metadata:
            return metadata['progress_eligible'] is True
        return metadata.get('origin', '').startswith(('Original ', 'Task materials ', 'Branch material '))
    coverage = sorted((ident, intervals)
                      for ident, intervals in context_pages.get('coverage', {}).items()
                      if intervals and original_segment(ident))
    bindings = state.get('execution_snapshot', {}).get('job_bindings', {})
    semantic_jobs = {value['job_id']: value.get('progress_identity') for value in bindings.values()}
    receipts = []
    from .artifacts import equal_files, matches_record
    for process in processes:
        if process.get('status') != 'completed' or process.get('exit_code') != 0:
            continue
        receipt = {'configuration': semantic_jobs.get(process.get('process_id')) or process.get('command'),
                   'artifacts': [{'snapshot_path': artifact.get('snapshot_path') or artifact.get('path'),
                                  'relative_path': artifact.get('relative_path') or artifact.get('stream')}
                                 for artifact in process.get('artifacts', [])]}
        # Compare real original artifacts when distinct job identities produced
        # the same observations. Administrative IDs cannot manufacture progress.
        def same(previous):
            if previous['configuration'] != receipt['configuration']:
                return False
            a, b = previous['artifacts'], receipt['artifacts']
            return len(a) == len(b) and all(x['relative_path'] == y['relative_path'] and
                equal_files(x['snapshot_path'], y['snapshot_path']) for x, y in zip(a, b)
                if x.get('snapshot_path') and y.get('snapshot_path')) and all(
                x.get('snapshot_path') and y.get('snapshot_path') for x, y in zip(a, b))
        if not any(same(previous) for previous in receipts):
            receipts.append(receipt)
    def measured_output(key):
        if key not in {'metrics.json', metrics_file}:
            return True
        return any(matches_record(outputs[key].get('snapshot_path', ''), artifact) and
                   artifact.get('relative_path') == key
                   for process in processes if process.get('status') == 'completed' and process.get('exit_code') == 0
                   for artifact in process.get('artifacts', []))
    task_outputs = {key: outputs[key].get('semantic_source', outputs[key].get('artifact_id'))
                    for key in required if key in outputs and measured_output(key)}
    return {'input_observations': pages, 'context_coverage': coverage, 'task_outputs': task_outputs,
            'terminal_receipts': receipts,
            'research_evidence': research_context.get('evidence_state', research_context.get('semantic_evidence', [])),
            'graph_tasks': research_context.get('semantic_tasks', [])}


def checkpoint_round(state, *, reason, decision=None):
    """Prepare an atomic session checkpoint; save before relinquishing the slot."""
    life = state['lifecycle']
    ident = str(uuid.uuid4())
    entry = {'checkpoint_id': ident, 'round_index': life['round_index'],
             'step': len(state.get('transcript', [])), 'reason': reason,
             'created_at': time.time(),
             'snapshot_version': len(life.get('checkpoints', [])) + 1,
             'pending_action': deepcopy(state.get('pending_action')),
             'decision': deepcopy(decision)}
    life.setdefault('checkpoints', []).append(entry)
    life['state'] = 'checkpointed'
    life['round_state'] = 'checkpointed'
    life['research_state'] = 'active'
    wait = {'type': 'work_round', 'checkpoint_id': ident, 'round_index': life['round_index'],
            'reason': reason, 'next_action': deepcopy(decision)}
    state.update(status='waiting', wait_for=wait)
    return wait


ACCOUNTING_FIELDS = ('totals', 'active_seconds', 'cost_complete', 'usage_complete')


class ReconciliationRequired(ValueError):
    """Persisted side effects cannot safely be replayed from this checkpoint."""


def load_accounting(state, workspace):
    """Migrate old sessions into the separate supervisory ledger, never reset it."""
    from .processes import read_json
    path = Path(workspace).parent / 'agent_usage.json'
    if not path.exists():
        path = Path(workspace) / 'agent_usage.json'
    if path.exists():
        try:
            ledger = read_json(path)
        except (ValueError, OSError, UnicodeError) as exc:
            raise ReconciliationRequired('External usage ledger cannot be read; reconcile the original supervisor accounting') from exc
        if not isinstance(ledger, dict) or ledger.get('run_id') != state['run_id'] or not isinstance(ledger.get('totals'), dict):
            raise ReconciliationRequired('Supervisory usage ledger is invalid; reconcile accounting before resuming')
        binding = state.get('execution_snapshot', {}).get('external_ledger_id')
        if binding and ledger.get('external_ledger_id') and binding != ledger['external_ledger_id']:
            raise ReconciliationRequired('Checkpoint and external ledger bind different supervisors; reconnect the original ledger')
        if ledger.get('accounted_transcript_steps', 0) > len(state.get('transcript', [])):
            raise ReconciliationRequired('Supervisory ledger advanced beyond the scientific checkpoint; reconcile ModelRequest receipts before another model call')
        state.update({key: ledger[key] for key in ACCOUNTING_FIELDS if key in ledger})
    elif 'totals' not in state and state.get('transcript'):
        raise ReconciliationRequired('Existing scientific checkpoint has no external usage ledger; reconnect the original supervisor rather than initialize new accounting')
    state.setdefault('totals', {'input_tokens': 0, 'output_tokens': 0, 'cost': None})
    state.setdefault('active_seconds', 0)


def persist_reconciliation(state, workspace, reason):
    """Pause scientific state without overwriting an untrusted usage ledger."""
    from .processes import atomic_json
    state.update(status='waiting_input', reconciliation_reason=reason)
    if state.get('wait_for'):
        state['reconciliation_previous_wait'] = deepcopy(state['wait_for'])
    state['wait_for'] = None
    if isinstance(state.get('execution_snapshot', {}).get('execution'), dict):
        state['execution_snapshot']['execution']['next_operation'] = {'type': 'reconciliation', 'reason': reason}
    life = state.setdefault('lifecycle', {'version': 1})
    life.update(state='blocked', round_state='paused', research_state='blocked')
    snapshot = {key: deepcopy(value) for key, value in state.items() if key not in ACCOUNTING_FIELDS}
    if isinstance(snapshot.get('result'), dict):
        snapshot['result'].pop('usage', None)
        snapshot.setdefault('historical_results', []).append(snapshot.pop('result'))
    atomic_json(Path(workspace) / 'agent_session.json', snapshot)


def save_session(state, workspace):
    """Write accounting first, then atomically replace the scientific checkpoint.

    Transport charges remain authoritative in ModelRequest DB receipts even if
    a process dies between these writes. The usage ledger is supervisory state,
    deliberately excluded from the scientific checkpoint.
    """
    from .processes import atomic_json
    workspace = Path(workspace)
    ledger = {'run_id': state['run_id'], 'updated_at': time.time(),
              'external_ledger_id': state.get('execution_snapshot', {}).get('external_ledger_id'),
              'accounted_transcript_steps': len(state.get('transcript', [])),
              **{key: deepcopy(state[key]) for key in ACCOUNTING_FIELDS if key in state}}
    atomic_json(workspace.parent / 'agent_usage.json', ledger)
    snapshot = {key: deepcopy(value) for key, value in state.items() if key not in ACCOUNTING_FIELDS}
    if isinstance(snapshot.get('result'), dict):
        snapshot['result'].pop('usage', None)
    checkpoints = (snapshot.get('lifecycle') or {}).get('checkpoints') or []
    if checkpoints:
        entry = checkpoints[-1]
        archive = workspace.parent / 'checkpoints' / (entry['checkpoint_id'] + '.json')
        if not archive.exists():
            scientific = deepcopy(snapshot.get('execution_snapshot', {}))
            # The checkpoint identity binds one actual logical snapshot. Graph
            # revisions remain historical state, freely editable in future work.
            entry.pop('snapshot_sha256', None)
            entry.pop('snapshot_digest_scheme', None)
            state['lifecycle']['checkpoints'][-1].update(entry)
            graph = scientific.pop('research_graph', {})
            graph_path = workspace.parent / 'graph-snapshots' / (entry['checkpoint_id'] + '.json')
            if not graph_path.exists():
                atomic_json(graph_path, graph)
            scientific['research_graph_reference'] = {'record_id': entry['checkpoint_id'],
                'revision': graph.get('revision'), 'path': str(graph_path)}
            atomic_json(archive, {'schema_version': 1, 'run_id': state['run_id'],
                'checkpoint': entry, 'scientific_state': scientific,
                'transcript_reference': {'path': 'workspace/agent_transcript.json',
                    'through_step': entry['step']},
                'pending_action': deepcopy(state.get('pending_action'))})
    atomic_json(workspace / 'agent_session.json', snapshot)
    atomic_json(workspace / 'agent_transcript.json', state.get('transcript', []))
