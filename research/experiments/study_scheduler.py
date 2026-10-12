"""Bounded independent research dispatch, durable ownership and observed timing.

This coordinator schedules only source-bound work already registered by the
StudyRuntime. Its concurrency policy changes capacity, never experiment counts,
contexts, configurations, decisions, or scientific eligibility.

Registered end-to-end payloads may require ``controller_restart`` at
``after_c1_checkpoint_before_confirmation``. Their model controllers run in
managed OS workers. The first completed revised-objective selection writes a
durable ordinary checkpoint and exits; a distinct worker proves the original
exit, reloads the same work/history/native Journal/tree/RNG, and records the
single required restart before requesting another action. Unknown original
workers and legacy accepted model requests retain physical model capacity.
ComputeSupervisor retains its scientific GPU policies; its wall policy does
not limit model-controller lifetime or individual model requests.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from copy import copy, deepcopy
import json
import importlib
import math
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import time
import uuid

from research.agents.processes import ManagedProcesses, artifact_matches, atomic_json, is_alive
from .study_runtime import (BLOCKING_RELATIONS, SETTLEMENT_RELATIONS, StudyLedger, StudyRuntime,
    _decode_study_result_json, _expand_action_history_frontiers, load_saved_codex_client)


MODEL_KINDS = ('episode', 'replay', 'calibration_decision')
GPU_KINDS = ('fit', 'infer')
CPU_KINDS = ('rescore', 'analysis', 'snapshot_stage')
TERMINAL = ('completed', 'failed', 'cancelled', 'not_run', 'budget_exhausted')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS forest_study_schedulers (
 campaign_id TEXT PRIMARY KEY, scheduler_id TEXT NOT NULL, session_id TEXT NOT NULL,
 hostname TEXT NOT NULL, pid INTEGER, process_created REAL, state TEXT NOT NULL,
 configuration_json TEXT NOT NULL, started_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_dispatches (
 dispatch_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, scheduler_id TEXT NOT NULL,
 session_id TEXT NOT NULL, owner TEXT NOT NULL UNIQUE, work_id TEXT, lane TEXT NOT NULL,
 state TEXT NOT NULL, accepted_at REAL NOT NULL, started_at REAL, finished_at REAL,
 result_json TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS forest_study_scheduler_intervals (
 interval_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, scheduler_id TEXT NOT NULL,
 work_id TEXT, kind TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL NOT NULL,
 reference_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_scheduler_decisions (
 decision_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, scheduler_id TEXT NOT NULL,
 decision_json TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_scheduler_circuits (
 campaign_id TEXT NOT NULL, source_id TEXT NOT NULL, last_error TEXT,
 consecutive_errors INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
 PRIMARY KEY(campaign_id,source_id));
CREATE TABLE IF NOT EXISTS forest_study_queue_spans (
 span_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, work_id TEXT NOT NULL,
 lane TEXT NOT NULL, ready_at REAL NOT NULL, claimed_at REAL);
CREATE INDEX IF NOT EXISTS forest_study_dispatch_work ON forest_study_dispatches(work_id,state);
CREATE INDEX IF NOT EXISTS forest_study_dispatch_live_session
 ON forest_study_dispatches(campaign_id,state,session_id)
 WHERE state IN ('claiming','accepted','running');
CREATE TABLE IF NOT EXISTS forest_study_controller_processes (
 dispatch_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, work_id TEXT NOT NULL,
 folder TEXT NOT NULL, state TEXT NOT NULL, worker_pid INTEGER, worker_created REAL,
 started_at REAL NOT NULL, finished_at REAL);
CREATE TABLE IF NOT EXISTS forest_study_controller_restarts (
 campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, request_id TEXT NOT NULL,
 policy_json TEXT NOT NULL, state TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
 request_dispatch_id TEXT NOT NULL, request_pid INTEGER NOT NULL,
 request_process_created REAL NOT NULL, resume_dispatch_id TEXT, resume_pid INTEGER,
 resume_process_created REAL, receipt_json TEXT, requested_at REAL NOT NULL,
 resumed_at REAL, PRIMARY KEY(campaign_id,work_id));
'''

RESTART_MILESTONE = 'after_c1_checkpoint_before_confirmation'


def _controller_policy(item):
    policy = item['payload'].get('controller_restart') or {}
    if policy.get('required') is not True:
        return None
    if policy.get('milestone') != RESTART_MILESTONE or item['kind'] != 'episode':
        raise ValueError('Controller restart requires the registered end-to-end decision milestone')
    from .end_to_end_runtime import is_end_to_end
    if not is_end_to_end(item['payload']):
        raise ValueError('Controller restart policy requires a registered end-to-end episode')
    return deepcopy(policy)


def _controller_checkpoint(runtime, item):
    """Retain the ordinary mutable state, including exact original effects."""
    with runtime.ledger.connect() as db:
        row = db.execute('SELECT * FROM forest_study_work WHERE campaign_id=? AND work_id=?',
            (runtime.campaign_id, item['work_id'])).fetchone()
        stable_fields = ('work_id', 'kind', 'stage', 'payload_json', 'decision_json', 'result_json', 'supervisor_work_id')
        work = {key: row[key] for key in stable_fields}
        children = [dict(value) for value in db.execute(
            "SELECT w.work_id,w.kind,w.stage,w.state,w.payload_json,w.decision_json,w.result_json,w.supervisor_work_id FROM forest_study_work w JOIN forest_study_edges d ON w.work_id=d.target_id WHERE d.campaign_id=? AND d.source_id=? AND d.relation='derived_from' ORDER BY w.work_id",
            (runtime.campaign_id, item['work_id']))]
        calls = [dict(value) for value in db.execute('SELECT * FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? ORDER BY request_id',
            (runtime.campaign_id, item['work_id']))]
        saved = {}
        for table in ('forest_study_end_to_end', 'forest_study_aide_sessions'):
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                value = db.execute('SELECT state_json FROM ' + table + ' WHERE campaign_id=? AND work_id=?',
                    (runtime.campaign_id, item['work_id'])).fetchone()
                if value:
                    saved[table] = json.loads(value[0])
                    if table == 'forest_study_aide_sessions':
                        saved[table]['action_history'] = _expand_action_history_frontiers(
                            saved[table].get('action_history', []))
    return {'work': work, 'children': children, 'model_calls': calls, 'ordinary_state': saved}


def _checkpoint_reload_proof(checkpoint):
    """Actually reconstruct native Journal/tree/RNG without requesting a model."""
    native = checkpoint['ordinary_state'].get('forest_study_aide_sessions')
    if native is None:
        return {'arm': 'FULL', 'action_history': _decode_study_result_json(checkpoint['work']['result_json']).get('action_history', []),
            'model_request_ids': [value['request_id'] for value in checkpoint['model_calls']]}
    from .aide_runtime import load_upstream, _tuple_tree, _node_record
    from omegaconf import OmegaConf
    import random
    upstream = load_upstream(Path(native['upstream_root']))
    journal = upstream['serialize'].loads_json(native['journal'], upstream['journal'].Journal)
    rng = random.Random()
    rng.setstate(_tuple_tree(native['rng_state']))
    agent = upstream['agent'].Agent(task_desc=native['task_desc'], cfg=OmegaConf.create(native['configuration']), journal=journal)
    return {'arm': 'AIDE', 'native_nodes': [_node_record(node) for node in agent.journal.nodes],
        'rng_state': rng.getstate(), 'configuration': native['configuration'],
        'action_history': native.get('action_history', []),
        'model_request_ids': [value['request_id'] for value in checkpoint['model_calls']]}


def _history_work_ids(value):
    if isinstance(value, dict):
        ids = {str(child) for name, child in value.items() if name in ('work_id', 'action_work_id') and isinstance(child, str)}
        for child in value.values():
            ids.update(_history_work_ids(child))
        return ids
    if isinstance(value, list):
        return set().union(*(_history_work_ids(child) for child in value)) if value else set()
    return set()


def _original_controller_result(folder, dispatch):
    """Unknown or changed publication keeps the original controller owned."""
    manager = ManagedProcesses(folder, directory=Path(folder) / 'processes')
    observed = manager.inspect(dispatch)
    if observed.get('status') not in ('completed', 'failed', 'cancelled'):
        return observed, None
    result_path = Path(folder) / 'controller_result.json'
    artifact = next((value for value in observed.get('artifacts', [])
        if Path(value.get('original_path', '')).resolve() == result_path.resolve()), None)
    if artifact is None or not artifact_matches(result_path, artifact):
        return {**observed, 'status': 'reconciliation', 'error': 'Original controller result is not bound to its completion receipt'}, None
    result = json.loads(result_path.read_text(encoding='utf-8'))
    identity = result.get('worker') or {}
    if (result.get('dispatch_id') != dispatch or identity.get('pid') != observed.get('pid') or
            identity.get('process_created') != observed.get('process_created')):
        return {**observed, 'status': 'reconciliation', 'error': 'Controller result disagrees with its original OS worker identity'}, None
    return observed, result


def _resume_controller_checkpoint(runtime, item, dispatch, worker):
    policy = _controller_policy(item)
    with runtime.ledger.connect() as db:
        row = db.execute('SELECT * FROM forest_study_controller_restarts WHERE campaign_id=? AND work_id=?',
            (runtime.campaign_id, item['work_id'])).fetchone()
        previous = db.execute('SELECT * FROM forest_study_controller_processes WHERE dispatch_id=?',
            (row['request_dispatch_id'],)).fetchone() if row else None
    if not row or row['state'] == 'resumed':
        return None
    if json.loads(row['policy_json']) != policy or not previous:
        raise ValueError('Controller restart must match its original registered policy and process')
    observed, result = _original_controller_result(previous['folder'], row['request_dispatch_id'])
    if observed.get('status') != 'completed' or not result or result.get('restart_request_id') != row['request_id']:
        raise ValueError('Original checkpoint worker has not published a confirmed successful exit')
    if (observed.get('pid'), observed.get('process_created')) != (row['request_pid'], row['request_process_created']):
        raise ValueError('Checkpoint request lost the original controller process identity')
    if worker['pid'] == row['request_pid'] or is_alive(row['request_pid'], row['request_process_created']):
        raise ValueError('Controller checkpoint requires a distinct worker after the original process exits')
    saved = json.loads(row['checkpoint_json'])
    checkpoint_path = Path(previous['folder']) / 'controller_checkpoint.json'
    artifact = next((value for value in observed.get('artifacts', [])
        if Path(value.get('original_path', '')).resolve() == checkpoint_path.resolve()), None)
    if artifact is None or not artifact_matches(checkpoint_path, artifact):
        raise ValueError('Original controller checkpoint publication is not bound to its completed exit')
    publication = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    if (publication.get('checkpoint') != saved or publication.get('policy') != policy or
            publication.get('request_id') != row['request_id'] or
            publication.get('dispatch_id') != row['request_dispatch_id'] or
            publication.get('worker') != {'pid': row['request_pid'], 'process_created': row['request_process_created']}):
        raise ValueError('Original checkpoint receipt disagrees with the persisted restart request')
    reloaded = _controller_checkpoint(runtime, item)
    if reloaded != saved:
        raise ValueError('Ordinary checkpoint or original model/GPU effects changed before controller reload')
    proof = _checkpoint_reload_proof(reloaded)
    receipt = {'milestone': RESTART_MILESTONE, 'checkpoint_reloaded': True,
        'previous_worker': {'pid': row['request_pid'], 'process_created': row['request_process_created']},
        'resumed_worker': worker, 'previous_exit': observed, 'reload_proof': proof,
        'checkpoint_receipt': {'path': str(checkpoint_path), 'artifact': artifact, **publication},
        'original_children': reloaded['children'], 'original_model_calls': reloaded['model_calls'],
        'request_id': row['request_id'], 'resume_dispatch_id': dispatch}
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_controller_restarts SET state='resumed',resume_dispatch_id=?,resume_pid=?,resume_process_created=?,receipt_json=?,resumed_at=? WHERE campaign_id=? AND work_id=? AND state='requested'",
            (dispatch, worker['pid'], worker['process_created'], _json(receipt), time.time(), runtime.campaign_id, item['work_id']))
    runtime.ledger.event(runtime.campaign_id, 'controller_restart_resumed', receipt, item['work_id'])
    return receipt


def _request_controller_restart(runtime, item, dispatch, worker):
    policy = _controller_policy(item)
    if not policy:
        return None
    with runtime.ledger.connect() as db:
        existing = db.execute('SELECT request_id FROM forest_study_controller_restarts WHERE campaign_id=? AND work_id=?',
            (runtime.campaign_id, item['work_id'])).fetchone()
    if existing:
        return None
    # Flush completed common operations into the ordinary editable comparison
    # record, without a new model query or scientific execution.
    runtime.build_episode_request({**item['payload'], 'work_id': item['work_id']})
    checkpoint = _controller_checkpoint(runtime, item)
    state = checkpoint['ordinary_state']['forest_study_end_to_end']
    comparisons = state.get('comparison_records') or []
    if state['objective']['version'] < 2 or not comparisons:
        return None
    comparison = comparisons[0]
    if comparison['objective']['version'] < 2:
        return None
    confirmation_ids = {value['fit_id'] for value in state['fits'] if value['stage'] == 'confirmation'}
    if any(value['work_id'] in confirmation_ids for value in checkpoint['children']):
        raise ValueError('Required controller restart was missed before confirmation')
    if any(value['state'] not in TERMINAL for value in checkpoint['children']):
        return None
    native = checkpoint['ordinary_state'].get('forest_study_aide_sessions')
    if native and native.get('pending') is not None:
        return None
    history = native.get('action_history', []) if native else _decode_study_result_json(checkpoint['work']['result_json']).get('action_history', [])
    if (comparison['selection_work_id'] not in _history_work_ids(history) or
            not any(value['status'] == 'completed' for value in checkpoint['model_calls']) or
            any(value['status'] == 'requested' for value in checkpoint['model_calls'])):
        raise ValueError('Restart milestone requires a durable agent decision after C1')
    proof = _checkpoint_reload_proof(checkpoint)
    request_id = str(uuid.uuid4())
    with runtime.ledger.connect() as db:
        folder = db.execute('SELECT folder FROM forest_study_controller_processes WHERE dispatch_id=?', (dispatch,)).fetchone()
    if not folder:
        raise ValueError('Restart checkpoint requires its original managed OS controller')
    atomic_json(Path(folder[0]) / 'controller_checkpoint.json', {'request_id': request_id,
        'dispatch_id': dispatch, 'worker': worker, 'milestone': RESTART_MILESTONE,
        'policy': policy, 'checkpoint': checkpoint})
    with runtime.ledger.connect() as db:
        db.execute('INSERT INTO forest_study_controller_restarts(campaign_id,work_id,request_id,policy_json,state,checkpoint_json,request_dispatch_id,request_pid,request_process_created,requested_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (runtime.campaign_id, item['work_id'], request_id, _json(policy), 'requested', _json(checkpoint), dispatch,
             worker['pid'], worker['process_created'], time.time()))
    runtime.ledger.event(runtime.campaign_id, 'controller_restart_checkpoint',
        {'request_id': request_id, 'milestone': RESTART_MILESTONE, 'worker': worker,
         'comparison_id': comparison['comparison_id'], 'reload_proof': proof}, item['work_id'])
    return request_id


def controller_worker(request_path):
    """One actual controller lifetime; the next dispatch reconstructs from SQLite."""
    import psutil
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    runtime = StudyRuntime(campaign=Path(request['campaign']), ledger=Path(request['ledger']),
        ledger_id=request['ledger_id'], manifest=Path(request['manifest']))
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
        row = db.execute('SELECT * FROM forest_study_work WHERE campaign_id=? AND work_id=?',
            (runtime.campaign_id, request['work_id'])).fetchone()
    if not row or row['state'] != 'claimed' or row['lease_owner'] != request['owner']:
        raise ValueError('OS controller requires its original dispatch lease')
    item = {**dict(row), 'payload': json.loads(row['payload_json'])}
    worker = {'pid': os.getpid(), 'process_created': psutil.Process().create_time()}
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_controller_processes SET state='running',worker_pid=?,worker_created=? WHERE dispatch_id=?",
            (worker['pid'], worker['process_created'], request['dispatch_id']))
    output = {'dispatch_id': request['dispatch_id'], 'worker': worker, 'work_id': item['work_id']}
    try:
        receipt = _resume_controller_checkpoint(runtime, item, request['dispatch_id'], worker)
        if request.get('nonformal_client_factory'):
            if runtime.manifest.get('nonformal_fixture') is not True:
                raise ValueError('Scripted controller factory requires an explicitly nonformal manifest')
            module, name = request['nonformal_client_factory'].split(':', 1)
            factory = importlib.import_module(module)
            for part in name.split('.'):
                factory = getattr(factory, part)
            client = factory(deepcopy(item))
        else:
            spec = item['payload'].get('model_provider') or runtime.manifest.get('model_provider') or {}
            client = load_saved_codex_client(str(spec['id']), model=str(spec['model']),
                reasoning_effort=str((spec.get('config') or {}).get('reasoning_effort', 'medium')))
        output['result'] = runtime.run_claimed(item, client=client, gpu_devices=(), defer_gpu=True)
        output['restart_request_id'] = _request_controller_restart(runtime, item, request['dispatch_id'], worker)
        output['restart_resume_receipt'] = receipt
    except Exception as exc:
        from research.agents.provider import ProviderError
        output['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        if isinstance(exc, ProviderError):
            output['error'].update(provider_error=True, code=exc.code, http_status=exc.http_status,
                retryable=exc.retryable, ambiguous=exc.ambiguous)
        with runtime.ledger.connect() as db:
            current = db.execute('SELECT state FROM forest_study_work WHERE work_id=?', (item['work_id'],)).fetchone()
        if current['state'] == 'claimed':
            runtime.ledger.set_state(runtime.campaign_id, item['work_id'], 'awaiting_reconciliation', error=str(exc))
    finally:
        atomic_json(Path(request_path).parent / 'controller_result.json', output)
    return output


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(name + ' must be a positive integer')
    return value


def merge_intervals(intervals):
    values = sorted((float(start), float(end)) for start,end in intervals
        if math.isfinite(float(start)) and math.isfinite(float(end)) and end > start)
    merged = []
    for start,end in values:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start,end])
    return merged


def interval_measure(intervals):
    values = list(intervals)
    return {'summed_seconds': sum(max(0, end-start) for start,end in values),
        'wall_union_seconds': sum(end-start for start,end in merge_intervals(values)),
        'interval_count': len(values)}


def intersect_intervals(intervals, blockers):
    intersections = []
    for start,end in intervals:
        for other_start,other_end in blockers:
            left,right = max(start,other_start), min(end,other_end)
            if right > left:
                intersections.append((left,right))
    return [tuple(value) for value in merge_intervals(intersections)]


def measured_concurrency(summary, maximum):
    """Choose the best actually observed successful degree, never extrapolate."""
    samples = []
    for batch in summary.get('batches', []):
        degree = batch.get('concurrency')
        calls = batch.get('calls') or []
        if not isinstance(degree, int) or isinstance(degree, bool) or not 1 <= degree <= maximum or not calls:
            continue
        if any(call.get('status') != 'completed' for call in calls):
            continue
        try:
            start,end = float(batch['started_at_epoch']), float(batch['finished_at_epoch'])
            latency = [float(call['elapsed_seconds']) for call in calls]
            spans = [(float(call['started_at_epoch']), float(call['finished_at_epoch'])) for call in calls]
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start or any(not math.isfinite(value) or value <= 0 for value in latency):
            continue
        if batch.get('completed_calls') != len(calls):
            continue
        request_ids = [call.get('request_id') for call in calls]
        if any(not isinstance(value, str) or not value for value in request_ids) or len(set(request_ids)) != len(request_ids):
            continue
        if any(not math.isfinite(left) or not math.isfinite(right) or not start <= left < right <= end for left,right in spans):
            continue
        events = sorted([(left, 1) for left,_ in spans] + [(right, -1) for _,right in spans])
        active,observed_degree = 0,0
        for _,delta in events:
            active += delta
            observed_degree = max(observed_degree, active)
        if observed_degree > degree:
            continue
        samples.append({'degree': observed_degree, 'declared_degree': degree, 'completed_calls': len(calls),
            'wall_seconds': end-start, 'calls_per_second': len(calls)/(end-start),
            'p95_seconds': sorted(latency)[math.ceil(.95*len(latency))-1],
            'request_ids': request_ids})
    if not samples:
        return {'degree': 1, 'reason': 'No verified successful concurrency sample', 'samples': []}
    chosen = max(samples, key=lambda value: (value['calls_per_second'], -value['p95_seconds'], -value['degree']))
    return {'degree': chosen['degree'], 'reason': 'Highest observed successful calls per wall second',
        'samples': samples, 'chosen': chosen}


class CoordinatorBusy(RuntimeError):
    pass


class StudyScheduler:
    def __init__(self, runtime, *, scheduler_id=None, maximum_model_concurrency=2,
                 model_concurrency=1, cpu_concurrency=1, gpu_devices=(),
                 pilot_path=None, client_factory=None, poll_seconds=.2):
        self.runtime = runtime
        self.campaign_id = runtime.campaign_id
        self.ledger = runtime.ledger
        self.scheduler_id = str(scheduler_id or uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.maximum = _positive_integer(maximum_model_concurrency, 'maximum_model_concurrency')
        self.degree = _positive_integer(model_concurrency, 'model_concurrency')
        if self.degree > self.maximum:
            raise ValueError('model_concurrency exceeds authorized model capacity')
        self.cpu_degree = _positive_integer(cpu_concurrency, 'cpu_concurrency')
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError('poll_seconds must be a finite positive technical polling interval')
        self.poll_seconds = poll_seconds
        self.gpu_devices = list(gpu_devices)
        self.client_factory = client_factory
        self.pilot_path = Path(pilot_path).resolve() if pilot_path else None
        self._pilot_identity = None
        self._owned = False
        self._closed = False
        self._futures = {}
        self._wave = None
        self._observed_samples = []
        self._tested_degrees = set()
        self._pending_decisions = []
        self._reconciliation_seen = {}
        self._parent_reconciliation_cache = {}
        self._executors = {'model': ThreadPoolExecutor(max_workers=self.maximum, thread_name_prefix='forest-model'),
            'gpu': ThreadPoolExecutor(max_workers=1, thread_name_prefix='forest-gpu'),
            'cpu': ThreadPoolExecutor(max_workers=self.cpu_degree, thread_name_prefix='forest-audit')}
        with self.ledger.connect() as db:
            db.executescript(SCHEMA)
            compute_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_compute_work'").fetchone()
            if compute_exists:
                db.execute("CREATE INDEX IF NOT EXISTS forest_compute_work_live_state "
                    "ON forest_compute_work(state,work_id) "
                    "WHERE state NOT IN ('completed','failed','cancelled','budget_exhausted')")
        self._read_pilot()

    def _configuration(self):
        return {'version': 1, 'maximum_model_concurrency': self.maximum,
            'model_concurrency': self.degree, 'cpu_concurrency': self.cpu_degree,
            'gpu_devices': self.gpu_devices, 'pilot_path': str(self.pilot_path) if self.pilot_path else None,
            'model_profile': self.runtime.manifest.get('model_provider') or {},
            'observed_samples': self._observed_samples, 'tested_degrees': sorted(self._tested_degrees),
            'wave': self._wave}

    def _decision(self, value):
        if not self._owned:
            self._pending_decisions.append(value)
            return
        with self.ledger.connect() as db:
            db.execute('INSERT INTO forest_study_scheduler_decisions VALUES(?,?,?,?,?)',
                (str(uuid.uuid4()), self.campaign_id, self.scheduler_id, _json(value), time.time()))

    def _read_pilot(self):
        if not self.pilot_path or not self.pilot_path.is_file():
            return
        stat = self.pilot_path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if identity == self._pilot_identity:
            return
        summary = json.loads(self.pilot_path.read_text())
        provider = self.runtime.manifest.get('model_provider') or {}
        if summary.get('model') != provider.get('model') or summary.get('reasoning_effort') != (provider.get('config') or {}).get('reasoning_effort', 'medium'):
            raise ValueError('Concurrency pilot does not match the current saved model profile')
        recommendation = measured_concurrency(summary, self.maximum)
        self.degree = recommendation['degree']
        self._observed_samples = recommendation['samples']
        self._tested_degrees.update(value['degree'] for value in self._observed_samples)
        self._pilot_identity = identity
        self._decision({'type': 'observed_concurrency', 'source': str(self.pilot_path),
            'pilot_id': summary.get('pilot_id'), 'model': summary.get('model'),
            'reasoning_effort': summary.get('reasoning_effort'), **recommendation})

    def acquire(self):
        import psutil
        now = time.time()
        created = psutil.Process().create_time()
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM forest_study_schedulers WHERE campaign_id=?', (self.campaign_id,)).fetchone()
            if previous and previous['session_id'] != self.session_id and previous['state'] == 'running':
                if previous['hostname'] != socket.gethostname():
                    raise CoordinatorBusy('Original coordinator host needs reconciliation before takeover')
                if is_alive(previous['pid'], previous['process_created']):
                    raise CoordinatorBusy('Original campaign coordinator is still alive')
            if previous:
                self.scheduler_id = previous['scheduler_id']
                saved = json.loads(previous['configuration_json'])
                if saved.get('maximum_model_concurrency') == self.maximum and saved.get('model_profile') == self.runtime.manifest.get('model_provider', {}) and saved.get('pilot_path') == (str(self.pilot_path) if self.pilot_path else None):
                    self.degree = min(self.maximum, saved.get('model_concurrency', self.degree))
                    self._observed_samples = saved.get('observed_samples', self._observed_samples)
                    self._tested_degrees.update(saved.get('tested_degrees', []))
                    self._wave = saved.get('wave')
            db.execute('INSERT INTO forest_study_schedulers VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET session_id=excluded.session_id,hostname=excluded.hostname,pid=excluded.pid,process_created=excluded.process_created,state=excluded.state,configuration_json=excluded.configuration_json,updated_at=excluded.updated_at',
                (self.campaign_id, self.scheduler_id, self.session_id, socket.gethostname(), os.getpid(), created,
                 'running', _json(self._configuration()), now, now))
        self._owned = True
        self._recover_dispatches()
        for value in self._pending_decisions:
            self._decision(value)
        self._pending_decisions.clear()
        return self.scheduler_id

    def _recover_dispatches(self):
        # Coordinator PID death proves its threads are gone. Durable external
        # process/model effects are reconciled separately before any re-dispatch.
        observers = []
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            entries = db.execute("SELECT * FROM forest_study_dispatches WHERE campaign_id=? AND session_id<>? AND state IN ('claiming','accepted','running')", (self.campaign_id, self.session_id)).fetchall()
            for entry in entries:
                work = db.execute('SELECT * FROM forest_study_work WHERE campaign_id=? AND lease_owner=?', (self.campaign_id, entry['owner'])).fetchone()
                controller = db.execute('SELECT * FROM forest_study_controller_processes WHERE dispatch_id=?', (entry['dispatch_id'],)).fetchone()
                if controller and (Path(controller['folder']) / 'processes' / entry['dispatch_id'] / 'request.json').is_file():
                    if not work:
                        work = db.execute('SELECT * FROM forest_study_work WHERE campaign_id=? AND work_id=?',
                            (self.campaign_id, controller['work_id'])).fetchone()
                    if work:
                        # A coordinator exit does not stop its detached worker.
                        # Attach to the exact original process, preserving owner.
                        observers.append((entry['dispatch_id'], {**dict(work), 'payload': json.loads(work['payload_json'])}, entry['lane']))
                        db.execute('UPDATE forest_study_dispatches SET session_id=? WHERE dispatch_id=?',
                            (self.session_id, entry['dispatch_id']))
                        continue
                if work and work['state'] == 'claimed':
                    db.execute("UPDATE forest_study_work SET state='queued',lease_owner=NULL,lease_at=NULL,updated_at=? WHERE work_id=? AND lease_owner=? AND state='claimed'", (time.time(), work['work_id'], entry['owner']))
                elif work and work['state'] == 'running':
                    calls = db.execute('SELECT status FROM forest_study_model_calls WHERE campaign_id=? AND work_id=?', (self.campaign_id, work['work_id'])).fetchall()
                    uncertain_call = any(call['status'] == 'requested' for call in calls)
                    completed_without_decision = any(call['status'] == 'completed' for call in calls) and not work['decision_json']
                    # GPU submissions and saved model decisions have their own
                    # durable, original effect identities. An interrupted native
                    # analysis with no receipt cannot be called safe to repeat.
                    known_recoverable = work['kind'] in GPU_KINDS or bool(work['decision_json']) or not calls and work['kind'] in MODEL_KINDS
                    state = 'awaiting_reconciliation' if uncertain_call or completed_without_decision or not known_recoverable else 'retryable'
                    error = 'Original model request must be reconciled; no duplicate invocation' if state == 'awaiting_reconciliation' else None
                    db.execute('UPDATE forest_study_work SET state=?,lease_owner=NULL,lease_at=NULL,error=?,updated_at=? WHERE work_id=? AND lease_owner=?',
                        (state, error, time.time(), work['work_id'], entry['owner']))
                db.execute("UPDATE forest_study_dispatches SET state='reconciled',finished_at=?,work_id=COALESCE(work_id,?) WHERE dispatch_id=?",
                    (time.time(), work['work_id'] if work else None, entry['dispatch_id']))
        for dispatch,item,lane in observers:
            future = self._executors[lane].submit(self._execute, dispatch, item, lane)
            self._futures[future] = {'item': item, 'lane': lane, 'dispatch_id': dispatch}
        # Client construction can fail after the durable claim but before
        # run_claimed changes the work state to running (for example, a
        # temporarily unavailable saved-provider database). Such dispatches
        # are terminal while their work lease remains claimed. Requeue only
        # when the same dispatch has no model-call receipt at all; any recorded
        # request, completed or ambiguous, remains untouched for reconciliation.
        with self.ledger.connect() as db:
            orphaned = db.execute("SELECT w.work_id,w.lease_owner,d.dispatch_id,d.accepted_at FROM forest_study_work w JOIN forest_study_dispatches d ON d.campaign_id=w.campaign_id AND d.owner=w.lease_owner WHERE w.campaign_id=? AND w.state='claimed' AND d.session_id<>? AND d.lane='model' AND d.state='failed' AND d.finished_at IS NOT NULL AND w.kind IN ('episode','replay','calibration_decision')",
                (self.campaign_id, self.session_id)).fetchall()
            recovered_claims = []
            for work in orphaned:
                call = db.execute('SELECT 1 FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? AND started_at>=? LIMIT 1',
                    (self.campaign_id, work['work_id'], work['accepted_at'])).fetchone()
                if call:
                    continue
                changed = db.execute("UPDATE forest_study_work SET state='retryable',lease_owner=NULL,lease_at=NULL,error=NULL,updated_at=? WHERE campaign_id=? AND work_id=? AND state='claimed' AND lease_owner=?",
                    (time.time(), self.campaign_id, work['work_id'], work['lease_owner'])).rowcount
                if changed:
                    recovered_claims.append((work['work_id'], work['dispatch_id']))
        for work_id, dispatch_id in recovered_claims:
            self.ledger.event(self.campaign_id, 'unstarted_claim_recovered',
                {'dispatch_id': dispatch_id, 'reason': 'failed before any model request was recorded'}, work_id)

    def _interval(self, kind, start, end, *, work_id=None, reference=None):
        if end <= start:
            return
        with self.ledger.connect() as db:
            db.execute('INSERT INTO forest_study_scheduler_intervals VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), self.campaign_id, self.scheduler_id, work_id, kind, start, end, _json(reference or {})))

    def _source(self, item, *, provider=False):
        payload = item['payload']
        profile = payload.get('model_provider') or self.runtime.manifest.get('model_provider') or {}
        return _json({'provider': profile, 'failure_scope': 'provider'}) if provider and item['kind'] in MODEL_KINDS else _json({'work_id': item['work_id']})

    def _source_blocked(self, item):
        with self.ledger.connect() as db:
            sources = [self._source(item)]
            if item['kind'] in MODEL_KINDS:
                sources.append(self._source(item, provider=True))
            rows = db.execute('SELECT state FROM forest_study_scheduler_circuits WHERE campaign_id=? AND source_id IN (' + ','.join('?' for _ in sources) + ')', [self.campaign_id, *sources]).fetchall()
        return any(row['state'] == 'blocked' for row in rows)

    def _unsettled_model_owners(self):
        """A dead coordinator cannot release an unconfirmed external model slot."""
        active = {entry['item']['work_id'] for entry in self._futures.values() if entry['lane'] == 'model'}
        with self.ledger.connect() as db:
            owners = {row[0] for row in db.execute(
                "SELECT DISTINCT work_id FROM forest_study_model_calls WHERE campaign_id=? AND status='requested'",
                (self.campaign_id,))}
        return sorted(owners - active)

    @staticmethod
    def _provider_failure(failure):
        from research.agents.provider import ProviderError
        if not isinstance(failure, ProviderError):
            return False
        if failure.code in {'configuration', 'authorization', 'transport_error', 'invalid_response'}:
            return True
        return failure.code == 'http_error' and (failure.http_status in {401, 403, 404, 408, 409, 429} or isinstance(failure.http_status, int) and failure.http_status >= 500)

    def _record_outcome(self, item, error, *, failure=None):
        shared = item['kind'] in MODEL_KINDS and self._provider_failure(failure)
        sources = [self._source(item, provider=shared)]
        if not error and item['kind'] in MODEL_KINDS:
            sources.append(self._source(item, provider=True))
        if error:
            error = _json({'type': type(failure).__name__, 'code': getattr(failure, 'code', None), 'message': error})
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for source in sources:
                previous = db.execute('SELECT * FROM forest_study_scheduler_circuits WHERE campaign_id=? AND source_id=?', (self.campaign_id, source)).fetchone()
                count = previous['consecutive_errors'] + 1 if error and previous and previous['last_error'] == error else 1 if error else 0
                state = 'blocked' if count >= 2 else 'ready'
                db.execute('INSERT INTO forest_study_scheduler_circuits VALUES(?,?,?,?,?) ON CONFLICT(campaign_id,source_id) DO UPDATE SET last_error=excluded.last_error,consecutive_errors=excluded.consecutive_errors,state=excluded.state',
                    (self.campaign_id, source, error, count, state))

    def _accepted_compute(self, work_ids=None):
        from .gpu_supervisor import ComputeSupervisor
        with self.ledger.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_compute_work'").fetchone()
            if not exists:
                return {}
            if work_ids is None:
                rows = db.execute("SELECT work_id,state,receipt_json,json_extract(configuration_json,'$.gpu_devices') AS gpu_devices_json FROM forest_compute_work").fetchall()
            else:
                identifiers = {str(value) for value in work_ids if value}
                # Keep every live physical job visible, even if its owner is
                # outside this campaign; then hydrate receipts only for those
                # owners and the currently unresolved study work.
                identifiers.update(row[0] for row in db.execute(
                    "SELECT work_id FROM forest_compute_work WHERE state NOT IN ('completed','failed','cancelled','budget_exhausted')"))
                ordered = sorted(identifiers)
                rows = []
                for offset in range(0, len(ordered), 500):
                    chunk = ordered[offset:offset + 500]
                    rows.extend(db.execute("SELECT work_id,state,receipt_json,json_extract(configuration_json,'$.gpu_devices') AS gpu_devices_json "
                        "FROM forest_compute_work WHERE work_id IN (" + ','.join('?' for _ in chunk) + ')', chunk).fetchall())
        supervisor = ComputeSupervisor(self.runtime.ledger_path, self.runtime.ledger_id)
        result = {}
        for row in rows:
            if row['state'] in ('completed', 'failed', 'cancelled', 'budget_exhausted'):
                saved = json.loads(row['receipt_json'] or '{}')
                result[row['work_id']] = {**saved, 'status': row['state'], 'configuration': {'gpu_devices': json.loads(row['gpu_devices_json'] or '[]')}}
                continue
            try:
                result[row['work_id']] = supervisor.status(row['work_id'])
            except ValueError:
                # A different logical owner still shares physical GPU usage;
                # unknown original work retains its capacity until reconciled.
                result[row['work_id']] = {'status': row['state'], 'configuration': {'gpu_devices': json.loads(row['gpu_devices_json'] or '[]')}, 'unknown_owner': True}
        return result

    def _hard_allowance(self):
        with self.ledger.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_compute_policies'").fetchone()
            if not exists:
                return None
            policy = db.execute('SELECT configuration_json FROM forest_compute_policies WHERE ledger_id=?', (self.runtime.ledger_id,)).fetchone()
        if not policy:
            return None
        value = json.loads(policy[0])
        if value.get('wall_seconds') is None:
            return None
        return value['wall_start'] + value['wall_seconds'] - time.time()

    @staticmethod
    def _publication_signature(paths):
        values = []
        for path in paths:
            try:
                stat = Path(path).stat()
                identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                identity = None
            values.append((str(path), identity))
        return values

    def _reconcile_completed_gpu(self, compute):
        """Promote only original completed effects; this path cannot submit work."""
        from research.agents.processes import artifact_matches
        from .gpu_supervisor import ComputeSupervisor
        active = {entry['item']['work_id'] for entry in self._futures.values()}
        with self.ledger.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT work_id,kind,supervisor_work_id,payload_json FROM forest_study_work WHERE campaign_id=? AND state='awaiting_reconciliation' AND kind IN ('fit','infer')", (self.campaign_id,))]
            attempts = {row['logical_work_id']: dict(row) for row in db.execute(
                'SELECT logical_work_id,supervisor_work_id,output_directory FROM forest_study_fit_attempts WHERE campaign_id=? ORDER BY attempt_number', (self.campaign_id,))}
            physical_ids = sorted({row['supervisor_work_id'] or row['work_id'] for row in rows})
            accepted = {}
            compute_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_compute_work'").fetchone()
            if compute_table:
                for offset in range(0, len(physical_ids), 500):
                    chunk = physical_ids[offset:offset + 500]
                    accepted.update({row['work_id']: row['ledger_id'] for row in db.execute(
                        'SELECT work_id,ledger_id FROM forest_compute_work WHERE work_id IN (' + ','.join('?' for _ in chunk) + ')', chunk)})
        recovered = []
        for row in rows:
            logical = row['work_id']
            physical = row['supervisor_work_id'] or logical
            if logical in active or accepted.get(physical) != self.runtime.ledger_id:
                continue
            observed = compute.get(physical) or {}
            if observed.get('unknown_owner') or observed.get('status') != 'completed':
                continue
            payload = json.loads(row['payload_json'])
            item = {**row, 'payload': payload}
            attempt = attempts.get(logical)
            if row['kind'] == 'fit':
                if attempt and attempt['supervisor_work_id'] != physical:
                    continue  # An older physical completion is never current.
                output = Path(attempt['output_directory'] if attempt else payload['output_directory'])
                config_path = Path(payload['configuration_path'])
                receipt_path = output / 'execution_receipt.json'
                required = [receipt_path, output / 'effective_config.json', output / 'checkpoint.pt', output / 'training.jsonl']
            else:
                if physical != logical:
                    continue
                spec = payload.get('inference_spec') or {}
                output = Path(spec['output_directory'])
                config_path = Path(spec['config_path'])
                receipt_path = output / 'inference_receipt.json'
                required = [receipt_path, output / 'validation_predictions.npz']
            folder = self.runtime.ledger_path.parent / '.forest-compute' / physical / 'processes' / physical
            previous = self._reconciliation_seen.get(logical) or {}
            paths = list(dict.fromkeys([str(config_path), *(str(path) for path in required),
                *(str(folder / name) for name in ('request.json', 'state.json', 'identity.json', 'completion.json')),
                *previous.get('artifact_paths', [])]))
            signature = (physical, row['payload_json'], observed.get('status'), self._publication_signature(paths))
            if previous.get('signature') == signature:
                continue
            checkpoint = {'signature': signature, 'artifact_paths': previous.get('artifact_paths', [])}
            self._reconciliation_seen[logical] = checkpoint
            try:
                supervisor = ComputeSupervisor(self.runtime.ledger_path, self.runtime.ledger_id)
                status = supervisor.status(physical)
                artifacts = (status.get('observation') or {}).get('artifacts') or []
                checkpoint['artifact_paths'] = [str(value['path']) for value in artifacts]
                if status.get('status') != 'completed':
                    continue
                config = json.loads(config_path.read_text(encoding='utf-8'))
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if (receipt.get('status') != 'SUCCEEDED' or receipt.get('work_id') != logical
                        or receipt.get('effective_config') != config or receipt.get('hidden_test_opened') is not False):
                    raise ValueError('Original scientific receipt/configuration does not reconcile')
                if row['kind'] == 'fit':
                    original = status['configuration'].get('research_configuration', {}).get('effective_config')
                    if config != original or json.loads((output / 'effective_config.json').read_text(encoding='utf-8')) != config:
                        raise ValueError('Fit configuration differs from original accepted conditions')
                    prediction = receipt.get('validation_prediction_file')
                    if not prediction:
                        raise ValueError('Original fit receipt does not identify validation predictions')
                    required.append(output / str(prediction))
                else:
                    if (receipt.get('optimizer_updates') != 0
                            or Path(str(receipt.get('source_checkpoint', ''))).resolve() != Path(spec['checkpoint_path']).resolve()
                            or Path(str(receipt.get('source_fit_config', ''))).resolve() != (Path(spec['checkpoint_path']).parent / 'effective_config.json').resolve()):
                        raise ValueError('Inference receipt differs from original source checkpoint/configuration')
                originals = {str(Path(value['original_path']).resolve()): value for value in artifacts if value.get('original_path')}
                for path in required:
                    artifact = originals.get(str(path.resolve()))
                    if not artifact or not artifact_matches(path, artifact):
                        raise ValueError('Original scientific raw artifact is missing or differs: ' + str(path))
                # Both calls inspect an existing terminal original ID. Neither
                # branch reaches the executors' new-work submission path.
                if row['kind'] == 'fit':
                    result = self.runtime.execute_fit(item, gpu_devices=self.gpu_devices)
                else:
                    result = self.runtime.execute_inference(payload, gpu_devices=self.gpu_devices)
                if result.get('supervisor', {}).get('status') != 'completed' or str(result.get('supervisor_work_id', physical)) != physical:
                    continue
                with self.ledger.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changed = db.execute("UPDATE forest_study_work SET state='completed',result_json=?,error=NULL,lease_owner=NULL,lease_at=NULL,updated_at=? WHERE campaign_id=? AND work_id=? AND state='awaiting_reconciliation' AND payload_json=? AND COALESCE(supervisor_work_id,work_id)=?",
                        (_json(result), time.time(), self.campaign_id, logical, row['payload_json'], physical)).rowcount
                if changed:
                    self.ledger.event(self.campaign_id, 'original_execution_reconciled', {'physical_work_id': physical, 'receipt_path': str(receipt_path)}, logical)
                    recovered.append(logical)
                    self._reconciliation_seen.pop(logical, None)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                self.ledger.event(self.campaign_id, 'original_execution_reconciliation_pending',
                    {'physical_work_id': physical, 'reason': type(exc).__name__ + ': ' + str(exc)}, logical)
            finally:
                # Recheck only metadata next tick, including preserved raw
                # artifacts. Full conditions are loaded once per publication.
                if logical in self._reconciliation_seen:
                    paths = list(dict.fromkeys([*paths, *(str(path) for path in required), *checkpoint['artifact_paths']]))
                    checkpoint['artifact_paths'] = paths
                    checkpoint['signature'] = (physical, row['payload_json'], observed.get('status'), self._publication_signature(paths))
        return recovered

    def _wake_reconciled_parents(self):
        """Resume a durable pending decision only after all its children settle."""
        active = {entry['item']['work_id'] for entry in self._futures.values()}
        with self.ledger.connect() as db:
            rows = db.execute("SELECT work_id,decision_json,updated_at FROM forest_study_work WHERE campaign_id=? AND state='awaiting_reconciliation' AND kind IN ('episode','replay')", (self.campaign_id,)).fetchall()
            session_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_study_aide_sessions'").fetchone()
            sessions = {row['work_id']: row['updated_at'] for row in db.execute(
                'SELECT work_id,updated_at FROM forest_study_aide_sessions WHERE campaign_id=?',
                (self.campaign_id,))} if session_table else {}
            states = {row['work_id']: row['state'] for row in db.execute('SELECT work_id,state FROM forest_study_work WHERE campaign_id=?', (self.campaign_id,))}
            edges = db.execute("SELECT source_id,target_id FROM forest_study_edges WHERE campaign_id=? AND relation='derived_from'", (self.campaign_id,)).fetchall()
            awakened = []
            for row in rows:
                if row['work_id'] in active or db.execute("SELECT 1 FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? AND status='requested'", (self.campaign_id, row['work_id'])).fetchone():
                    continue
                has_decision = bool(row['decision_json'])
                session_updated = sessions.get(row['work_id'])
                signature = (row['updated_at'], session_updated)
                cached = self._parent_reconciliation_cache.get(row['work_id'])
                if cached and cached['signature'] == signature:
                    saved, pending = cached['saved'], cached['pending']
                else:
                    # These reconciliation decisions use only two scalar fields.
                    # Episode result_json may contain the complete durable history
                    # (hundreds of MB in long campaigns); avoid materializing and
                    # decoding that snapshot just to recover its pending action.
                    raw = db.execute("SELECT json_extract(result_json,'$.pending_action') AS pending_action, "
                        "json_extract(result_json,'$.node_id') AS node_id "
                        'FROM forest_study_work WHERE campaign_id=? AND work_id=?',
                        (self.campaign_id, row['work_id'])).fetchone()
                    saved = {'pending_action': raw['pending_action'], 'node_id': raw['node_id']} if raw else {}
                    pending = None
                    if session_table and session_updated is not None:
                        native = db.execute("SELECT json_extract(state_json,'$.pending.node_id') AS node_id, "
                            "json_extract(state_json,'$.pending.node.code') AS code "
                            'FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?',
                            (self.campaign_id, row['work_id'])).fetchone()
                        if native and native['node_id'] is not None:
                            pending = {'node_id': native['node_id'], 'code': native['code']}
                    self._parent_reconciliation_cache[row['work_id']] = {
                        'signature': signature, 'saved': saved, 'pending': pending}
                if not has_decision and saved.get('pending_action') and saved.get('node_id'):
                    program = db.execute('SELECT code,program_json FROM forest_study_aide_programs WHERE campaign_id=? AND work_id=? AND node_id=?', (self.campaign_id, row['work_id'], saved['node_id'])).fetchone()
                    has_decision = bool(program and (json.loads(program['program_json']).get('decision') or {}).get('actions') and pending and pending.get('node_id') == saved['node_id'] and pending.get('code') == program['code'])
                children = {edge['target_id'] for edge in edges if edge['source_id'] == row['work_id']}
                if has_decision and children and all(states.get(child) in TERMINAL for child in children):
                    db.execute("UPDATE forest_study_work SET state='retryable',lease_owner=NULL,lease_at=NULL,error=NULL,updated_at=? WHERE campaign_id=? AND work_id=? AND state='awaiting_reconciliation'", (time.time(), self.campaign_id, row['work_id']))
                    awakened.append((row['work_id'], sorted(children)))
                    self._parent_reconciliation_cache.pop(row['work_id'], None)
        for work_id, children in awakened:
            self.ledger.event(self.campaign_id, 'pending_decision_reconciled', {'settled_child_work_ids': children}, work_id)
        return [work_id for work_id, _ in awakened]

    def _ready_ids(self, kinds, compute):
        with self.ledger.connect() as db:
            # Queue polling is frequent.  Do not materialize each candidate's
            # complete payload and result (which can contain the full durable
            # research history) just to decide whether its identity is ready.
            # Read those JSON subdocuments only after the cheap dependency
            # checks need them, and pass only the provider profile to circuit
            # classification.  The full payload remains untouched for the
            # eventual execution claim.
            rows = db.execute("SELECT work_id,kind,state,supervisor_work_id,decision_json FROM forest_study_work WHERE campaign_id=? AND state IN ('queued','retryable') ORDER BY created_at,work_id", (self.campaign_id,)).fetchall()
            states = {row['work_id']: row['state'] for row in db.execute('SELECT work_id,state FROM forest_study_work WHERE campaign_id=?', (self.campaign_id,))}
            edges = db.execute('SELECT source_id,target_id,relation FROM forest_study_edges WHERE campaign_id=?', (self.campaign_id,)).fetchall()
            active = {entry['item']['work_id'] for entry in self._futures.values()}
            ready = []
            for row in rows:
                if row['kind'] not in kinds or row['work_id'] in active:
                    continue
                if any(edge['target_id'] == row['work_id'] and (
                        edge['relation'] in BLOCKING_RELATIONS and states.get(edge['source_id']) != 'completed' or
                        edge['relation'] in SETTLEMENT_RELATIONS and states.get(edge['source_id']) not in TERMINAL) for edge in edges):
                    continue
                if row['state'] == 'retryable':
                    original = compute.get(row['supervisor_work_id'] or row['work_id'])
                    if original and original['status'] not in ('completed', 'failed', 'cancelled', 'budget_exhausted', 'reconciliation'):
                        continue
                    # Pending action IDs are registered as derived_from edges
                    # before execution. Use this compact relational index for
                    # readiness instead of json_extract(result_json): SQLite
                    # must scan and parse the entire (potentially huge) durable
                    # episode history even when only pending_work_ids is asked
                    # for. This mirrors checkpoint engines that route on small
                    # state metadata and hydrate full snapshots only on resume.
                    children = [edge['target_id'] for edge in edges
                        if edge['source_id'] == row['work_id'] and edge['relation'] == 'derived_from']
                    if any(states.get(child) not in TERMINAL for child in children):
                        continue
                provider_json = db.execute(
                    "SELECT json_extract(payload_json,'$.model_provider') FROM forest_study_work WHERE work_id=?",
                    (row['work_id'],)).fetchone()[0]
                provider = json.loads(provider_json) if provider_json else None
                item = {'work_id': row['work_id'], 'kind': row['kind'], 'payload': {'model_provider': provider}}
                if not self._source_blocked(item):
                    ready.append(row['work_id'])
            return ready

    def _make_client(self, item):
        if self.client_factory:
            client = self.client_factory(deepcopy(item))
        else:
            spec = item['payload'].get('model_provider') or self.runtime.manifest.get('model_provider') or {}
            client = load_saved_codex_client(str(spec['id']), model=str(spec['model']),
                reasoning_effort=str((spec.get('config') or {}).get('reasoning_effort', 'medium')))
        return client

    def _run_controller(self, dispatch, item):
        """Launch once, or observe the original worker after coordinator recovery."""
        with self.ledger.connect() as db:
            original = db.execute('SELECT * FROM forest_study_controller_processes WHERE dispatch_id=?', (dispatch,)).fetchone()
        folder = Path(original['folder']) if original else self.runtime.campaign / '.forest-study-controllers' / dispatch
        folder.mkdir(parents=True, exist_ok=True)
        manager = ManagedProcesses(folder, directory=folder / 'processes',
            code_inputs=[Path(__file__), Path(__file__).with_name('study_runtime.py'),
                         Path(__file__).with_name('aide_runtime.py'), Path(__file__).with_name('end_to_end_runtime.py')])
        if not original:
            request = {'campaign': str(self.runtime.campaign), 'manifest': str(self.runtime.manifest_path),
                'ledger': str(self.runtime.ledger_path), 'ledger_id': self.runtime.ledger_id,
                'work_id': item['work_id'], 'owner': item['lease_owner'], 'dispatch_id': dispatch}
            source_root = Path(__file__).resolve().parents[2]
            paths = [str(source_root)]
            if self.client_factory:
                if self.runtime.manifest.get('nonformal_fixture') is not True or '<locals>' in self.client_factory.__qualname__ or self.client_factory.__name__ == '<lambda>':
                    raise ValueError('OS controller fixtures require an importable factory and explicit nonformal manifest')
                request['nonformal_client_factory'] = self.client_factory.__module__ + ':' + self.client_factory.__qualname__
                module = sys.modules[self.client_factory.__module__]
                paths.append(str(Path(module.__file__).resolve().parent))
            atomic_json(folder / 'controller_request.json', request)
            with self.ledger.connect() as db:
                db.execute('INSERT INTO forest_study_controller_processes(dispatch_id,campaign_id,work_id,folder,state,started_at) VALUES(?,?,?,?,?,?)',
                    (dispatch, self.campaign_id, item['work_id'], str(folder), 'prepared', time.time()))
            commands = self.runtime.manifest.get('commands') or {}
            executable = str(commands.get('controller_python') or commands.get('framework_python') or sys.executable)
            command = [executable, '-m', 'research.experiments.study_scheduler', 'controller-worker',
                '--campaign', str(self.runtime.campaign), '--manifest', str(self.runtime.manifest_path),
                '--ledger', str(self.runtime.ledger_path), '--ledger-id', self.runtime.ledger_id,
                '--controller-request', str(folder / 'controller_request.json')]
            # The controller has only the model/action lane. GPU work is always
            # registered in the same ledger and deferred to the serial GPU lane.
            environment = {'PYTHONPATH': os.pathsep.join(paths), 'CUDA_VISIBLE_DEVICES': ''}
            manager.start(command, process_id=dispatch, env=environment,
                experiment={'kind': 'study_controller', 'campaign_id': self.campaign_id,
                    'work_id': item['work_id'], 'dispatch_id': dispatch, 'controller_restart': _controller_policy(item)})
        while True:
            observed, publication = _original_controller_result(folder, dispatch)
            if publication is not None:
                with self.ledger.connect() as db:
                    db.execute('UPDATE forest_study_controller_processes SET state=?,worker_pid=?,worker_created=?,finished_at=? WHERE dispatch_id=?',
                        (observed['status'], observed.get('pid'), observed.get('process_created'), time.time(), dispatch))
                if publication.get('error'):
                    error = publication['error']
                    if error.get('provider_error'):
                        from research.agents.provider import ProviderError
                        raise ProviderError(error['message'], code=error['code'], http_status=error.get('http_status'),
                            retryable=error.get('retryable', False), ambiguous=error.get('ambiguous', False))
                    raise RuntimeError(error['type'] + ': ' + error['message'])
                if observed['status'] != 'completed':
                    raise RuntimeError('Original controller exited unsuccessfully: ' + observed['status'])
                return publication['result']
            # Missing original publication never permits a new launch/query.
            with self.ledger.connect() as db:
                db.execute('UPDATE forest_study_controller_processes SET state=?,worker_pid=COALESCE(worker_pid,?),worker_created=COALESCE(worker_created,?) WHERE dispatch_id=? AND state<>?',
                    (observed['status'], observed.get('pid'), observed.get('process_created'), dispatch, observed['status']))
            time.sleep(self.poll_seconds)

    def _observe_ready(self, lane, ids):
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            open_spans = db.execute('SELECT span_id,work_id FROM forest_study_queue_spans WHERE campaign_id=? AND lane=? AND claimed_at IS NULL',
                (self.campaign_id, lane)).fetchall()
            for span in open_spans:
                if span['work_id'] not in ids:
                    db.execute('UPDATE forest_study_queue_spans SET claimed_at=? WHERE span_id=?', (time.time(), span['span_id']))
            for work_id in ids:
                previous = db.execute('SELECT 1 FROM forest_study_queue_spans WHERE campaign_id=? AND work_id=? AND lane=? AND claimed_at IS NULL',
                    (self.campaign_id, work_id, lane)).fetchone()
                if not previous:
                    db.execute('INSERT INTO forest_study_queue_spans VALUES(?,?,?,?,?,NULL)',
                        (str(uuid.uuid4()), self.campaign_id, work_id, lane, time.time()))

    def _execute(self, dispatch, item, lane):
        started = time.time()
        with self.ledger.connect() as db:
            db.execute("UPDATE forest_study_dispatches SET state='running',started_at=? WHERE dispatch_id=? AND state='accepted'", (started, dispatch))
        # A distinct runtime snapshot and client are used for every independent
        # episode. Sharing one transcript/model result is never an optimization.
        runtime = copy(self.runtime)
        runtime.manifest = deepcopy(self.runtime.manifest)
        error = None
        failure = None
        result = None
        try:
            if lane == 'model' and _controller_policy(item):
                result = self._run_controller(dispatch, item)
            else:
                client = self._make_client(item) if lane == 'model' else None
                result = runtime.run_claimed(item, gpu_devices=self.gpu_devices,
                    client=client, defer_gpu=lane == 'model')
            return result
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            failure = exc
            raise
        finally:
            finished = time.time()
            if lane == 'cpu':
                self._interval('audit', started, finished, work_id=item['work_id'],
                    reference={'dispatch_id': dispatch, 'operation': item['kind']})
            elif lane == 'model':
                with self.ledger.connect() as db:
                    calls = db.execute('SELECT started_at,finished_at FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? AND started_at>=? AND started_at<=?',
                        (self.campaign_id, item['work_id'], started, finished)).fetchall()
                cursor = started
                for left, right in merge_intervals([(call['started_at'], call['finished_at'] or finished) for call in calls]):
                    self._interval('audit', cursor, left, work_id=item['work_id'],
                        reference={'dispatch_id': dispatch, 'operation': 'prepare_or_verify_decision'})
                    cursor = max(cursor, right)
                self._interval('audit', cursor, finished, work_id=item['work_id'],
                    reference={'dispatch_id': dispatch, 'operation': 'prepare_or_verify_decision'})
            with self.ledger.connect() as db:
                db.execute('UPDATE forest_study_dispatches SET state=?,finished_at=?,error=? WHERE dispatch_id=?',
                    ('failed' if error else 'returned', time.time(), error, dispatch))
                # The complete result is already durably stored by StudyRuntime
                # in forest_study_work (and its event delta). Duplicating it in
                # every dispatch row multiplied episode histories in the ledger;
                # dispatch.work_id is the canonical result reference.
            self._record_outcome(item, error, failure=failure)

    def _evaluate_wave(self):
        if not self._wave:
            return
        with self.ledger.connect() as db:
            dispatches = db.execute('SELECT * FROM forest_study_dispatches WHERE dispatch_id IN (' + ','.join('?' for _ in self._wave['dispatch_ids']) + ')', self._wave['dispatch_ids']).fetchall()
            if any(row['finished_at'] is None for row in dispatches):
                return
            calls = []
            for row in dispatches:
                calls.extend(dict(call) for call in db.execute('SELECT * FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? AND started_at>=? AND started_at<=?',
                    (self.campaign_id, row['work_id'], row['started_at'], row['finished_at'])))
        wave = self._wave
        self._wave = None
        if not calls:
            return  # Resuming saved actions is not a new inference sample.
        failed = [call for call in calls if call['status'] != 'completed' or call['finished_at'] is None]
        if failed:
            previous = self.degree
            self._tested_degrees.add(wave['degree'])
            self._observed_samples = [value for value in self._observed_samples if value['degree'] < wave['degree']]
            self.degree = max(1, min(self.degree, wave['degree'] - 1))
            self._decision({'type': 'live_concurrency', 'degree': self.degree, 'previous_degree': previous,
                'reason': 'Observed model request failure; reduce simultaneous calls and diagnose the source',
                'request_ids': [call['request_id'] for call in calls]})
            return
        start = min(call['started_at'] for call in calls)
        end = max(call['finished_at'] for call in calls)
        if end <= start:
            return
        latency = sorted(call['elapsed_seconds'] for call in calls if call['elapsed_seconds'] is not None)
        if not latency:
            return
        # Underfilled waves cannot establish the performance of a higher degree.
        overlap = sorted([(call['started_at'], 1) for call in calls] +
                         [(call['finished_at'], -1) for call in calls])
        active, observed_degree = 0, 0
        for _, delta in overlap:
            active += delta
            observed_degree = max(observed_degree, active)
        sample = {'degree': observed_degree, 'completed_calls': len(calls), 'wall_seconds': end-start,
            'calls_per_second': len(calls)/(end-start), 'p95_seconds': latency[math.ceil(.95*len(latency))-1],
            'request_ids': [call['request_id'] for call in calls],
            'model_profiles': [json.loads(call['request_json']).get('model') for call in calls]}
        self._observed_samples = [value for value in self._observed_samples if value['degree'] != observed_degree] + [sample]
        self._tested_degrees.add(observed_degree)
        chosen = max(self._observed_samples, key=lambda value: (value['calls_per_second'], -value['p95_seconds'], -value['degree']))
        probe = observed_degree + 1
        probing = probe <= self.maximum and probe not in self._tested_degrees and len(calls) >= wave['degree']
        self.degree = probe if probing else chosen['degree']
        self._decision({'type': 'live_concurrency', 'degree': self.degree,
            'reason': 'Bounded next-degree measurement' if probing else 'Best actually observed completed calls per wall second; p95 breaks throughput ties',
            'sample': sample, 'chosen': chosen, 'probe': probing})

    def _dispatch(self, lane, ids):
        dispatch,owner = str(uuid.uuid4()), self.scheduler_id + ':' + str(uuid.uuid4())
        accepted = time.time()
        # Ownership is durable before claim, before queueing, and before model
        # or deterministic executor effects. Restart can find either crash gap.
        with self.ledger.connect() as db:
            db.execute('INSERT INTO forest_study_dispatches(dispatch_id,campaign_id,scheduler_id,session_id,owner,lane,state,accepted_at) VALUES(?,?,?,?,?,?,?,?)',
                (dispatch, self.campaign_id, self.scheduler_id, self.session_id, owner, lane, 'claiming', accepted))
        audit_started = time.time()
        try:
            item = self.runtime.claim_work(owner, work_ids=ids)
        except Exception:
            with self.ledger.connect() as db:
                db.execute("UPDATE forest_study_dispatches SET state='claim_failed',finished_at=? WHERE dispatch_id=?", (time.time(), dispatch))
            raise
        self._interval('audit', audit_started, time.time(), work_id=item['work_id'] if item else None,
            reference={'dispatch_id': dispatch, 'operation': 'reload_and_claim'})
        if item is None:
            with self.ledger.connect() as db:
                db.execute("UPDATE forest_study_dispatches SET state='idle',finished_at=? WHERE dispatch_id=?", (time.time(), dispatch))
            return False
        with self.ledger.connect() as db:
            db.execute("UPDATE forest_study_dispatches SET work_id=?,state='accepted' WHERE dispatch_id=?", (item['work_id'], dispatch))
            db.execute('UPDATE forest_study_queue_spans SET claimed_at=? WHERE campaign_id=? AND work_id=? AND lane=? AND claimed_at IS NULL',
                (time.time(), self.campaign_id, item['work_id'], lane))
        future = self._executors[lane].submit(self._execute, dispatch, item, lane)
        self._futures[future] = {'item': item, 'lane': lane, 'dispatch_id': dispatch}
        return dispatch

    def tick(self):
        if self._closed:
            raise RuntimeError('Scheduler is closed')
        if not self._owned:
            self.acquire()
        started = time.time()
        outcomes = []
        for future,entry in list(self._futures.items()):
            if future.done():
                try:
                    outcomes.append({'work_id': entry['item']['work_id'], 'result': future.result()})
                except Exception as exc:
                    outcomes.append({'work_id': entry['item']['work_id'], 'error': type(exc).__name__ + ': ' + str(exc)})
                del self._futures[future]
            else:
                try:
                    self.ledger.heartbeat(self.campaign_id, entry['item']['work_id'], entry['item']['lease_owner'])
                except ValueError:
                    pass  # Runtime may have just committed its terminal state.
        self.runtime.reload_manifest()
        self._read_pilot()
        self._evaluate_wave()
        with self.ledger.connect() as db:
            candidates = db.execute("SELECT work_id,supervisor_work_id FROM forest_study_work "
                "WHERE campaign_id=? AND state NOT IN ('completed','failed','cancelled','not_run','budget_exhausted')",
                (self.campaign_id,)).fetchall()
        compute_ids = {value for row in candidates for value in (row['work_id'], row['supervisor_work_id']) if value}
        compute = self._accepted_compute(compute_ids)
        self._reconcile_completed_gpu(compute)
        self._wake_reconciled_parents()
        gpu_busy = any(value['configuration'].get('gpu_devices') and
            value.get('confirmed_execution_status') is None and value['status'] not in ('completed', 'failed', 'cancelled', 'budget_exhausted') for value in compute.values())
        occupancy = {lane: sum(entry['lane'] == lane for entry in self._futures.values()) for lane in self._executors}
        # Unknown legacy/provider subprocesses may survive coordinator death.
        # Reattached OS controllers already own a future and are excluded.
        occupancy['model'] += len(self._unsettled_model_owners())
        ready = {'gpu': self._ready_ids(GPU_KINDS, compute), 'cpu': self._ready_ids(CPU_KINDS, compute), 'model': self._ready_ids(MODEL_KINDS, compute)}
        for lane,ids in ready.items():
            self._observe_ready(lane, ids)
        # Physical work gets the first opportunity, independently of slow model
        # calls. Accepted unknown original jobs retain this exclusive capacity.
        if not gpu_busy and occupancy['gpu'] < 1:
            ids = ready['gpu']
            if ids:
                self._dispatch('gpu', ids)
        for lane,kinds,capacity in [('cpu', CPU_KINDS, self.cpu_degree), ('model', MODEL_KINDS, self.degree)]:
            available = capacity - occupancy[lane]
            wave_dispatches = []
            for _ in range(available):
                ids = self._ready_ids(kinds, compute)
                dispatch = self._dispatch(lane, ids) if ids else None
                if not dispatch:
                    break
                wave_dispatches.append(dispatch)
            if lane == 'model' and wave_dispatches and not self._wave:
                self._wave = {'degree': self.degree, 'dispatch_ids': [
                    entry['dispatch_id'] for entry in self._futures.values()
                    if entry['lane'] == 'model']}
        with self.ledger.connect() as db:
            db.execute('UPDATE forest_study_schedulers SET updated_at=?,configuration_json=? WHERE campaign_id=? AND session_id=?',
                (time.time(), _json(self._configuration()), self.campaign_id, self.session_id))
        self._interval('scheduling', started, time.time(), reference={'active_dispatches': len(self._futures)})
        pending_original = any(value.get('confirmed_execution_status') is None and value['status'] not in ('completed','failed','cancelled','budget_exhausted','reconciliation') for value in compute.values())
        with self.ledger.connect() as db:
            unresolved = db.execute("SELECT count(*) FROM forest_study_work WHERE campaign_id=? AND state NOT IN ('completed','failed','cancelled','not_run','budget_exhausted')", (self.campaign_id,)).fetchone()[0]
        state = 'running' if self._futures or pending_original else 'blocked' if unresolved else 'idle'
        return {'state': state, 'outcomes': outcomes, 'unresolved_work_items': unresolved,
            'active': len(self._futures), 'model_concurrency': self.degree}

    def metrics(self):
        now = time.time()
        categories = {}
        def add(kind, start, end):
            if start is not None and end is not None and end > start:
                categories.setdefault(kind, []).append((start,end))
        with self.ledger.connect() as db:
            intervals = db.execute('SELECT * FROM forest_study_scheduler_intervals WHERE campaign_id=?', (self.campaign_id,)).fetchall()
            for entry in intervals:
                add(entry['kind'], entry['started_at'], entry['finished_at'])
            calls = db.execute('SELECT work_id,started_at,finished_at FROM forest_study_model_calls WHERE campaign_id=?', (self.campaign_id,)).fetchall()
            for call in calls:
                if call['finished_at'] is not None:
                    add('model_wait', call['started_at'], call['finished_at'])
                elif any(entry['item']['work_id'] == call['work_id'] for entry in self._futures.values()):
                    add('model_wait', call['started_at'], now)
                else:
                    add('unreconciled_model_interval', call['started_at'], now)
            compute_exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_compute_work'").fetchone()
            compute = db.execute("SELECT work_id,started_at,accepted_at,receipt_json,json_extract(configuration_json,'$.gpu_devices') AS gpu_devices_json FROM forest_compute_work WHERE ledger_id=?", (self.runtime.ledger_id,)).fetchall() if compute_exists else []
            attempts_exist = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_study_fit_attempts'").fetchone()
            fit_attempts = db.execute('SELECT logical_work_id,supervisor_work_id FROM forest_study_fit_attempts WHERE campaign_id=?',
                                     (self.campaign_id,)).fetchall() if attempts_exist else []
            queued = db.execute('SELECT * FROM forest_study_queue_spans WHERE campaign_id=?', (self.campaign_id,)).fetchall()
            work = db.execute('SELECT work_id,stage,kind,state,created_at,updated_at FROM forest_study_work WHERE campaign_id=?', (self.campaign_id,)).fetchall()
            stage_starts = {(row['stage'], row['kind']): row['started_at'] for row in db.execute(
                "SELECT w.stage,w.kind,MIN(e.recorded_at) started_at FROM forest_study_events e JOIN forest_study_work w ON w.work_id=e.work_id AND w.campaign_id=e.campaign_id WHERE e.campaign_id=? AND e.event_type='work_claimed' GROUP BY w.stage,w.kind", (self.campaign_id,))}
        gpu = []
        waits = []
        for entry in compute:
            if not json.loads(entry['gpu_devices_json'] or '[]'):
                continue
            observation = json.loads(entry['receipt_json'])
            end = observation.get('finished_at') or now
            if entry['started_at'] is not None:
                gpu.append((entry['work_id'], entry['started_at'], end))
                add('gpu_work', entry['started_at'], end)
            waits.append((entry['work_id'], entry['accepted_at'], entry['started_at'] or now))
            add('gpu_dispatch_wait', entry['accepted_at'], entry['started_at'] or now)
        for work_id,start,end in waits:
            for left,right in intersect_intervals([(start,end)], [(a,b) for other,a,b in gpu if other != work_id]):
                add('gpu_queue_wait', left,right)
        for entry in queued:
            end = entry['claimed_at'] or now
            add(entry['lane'] + '_ready_wait', entry['ready_at'], end)
            if entry['lane'] == 'gpu':
                for left,right in intersect_intervals([(entry['ready_at'], end)], [(a,b) for _,a,b in gpu]):
                    add('gpu_queue_wait', left,right)
        states = {}
        for entry in work:
            states[entry['state']] = states.get(entry['state'], 0) + 1
        compute_status = self._accepted_compute()
        ready = set(self._ready_ids(MODEL_KINDS + GPU_KINDS + CPU_KINDS, compute_status))
        pending = [entry for entry in work if entry['state'] not in TERMINAL]
        completed = [entry for entry in work if entry['state'] == 'completed']
        # Historical attempts and omitted planned cells retain their records.
        # A current formal comparison is defined by the manifest's cell IDs,
        # never by every row that happens to share its stage in the ledger.
        cells = self.runtime.manifest.get('cells') or []
        formal_ids = {str(cell['episode_id']) for cell in cells if cell.get('episode_id')}
        current_formal = [entry for entry in work if entry['work_id'] in formal_ids]
        formal_states = {}
        for entry in current_formal:
            formal_states[entry['state']] = formal_states.get(entry['state'], 0) + 1
        formal_progress = {
            'protocol_version': (self.runtime.manifest.get('formal_protocol_version')
                                 or self.runtime.manifest.get('agent_execution_protocol_version')),
            'selection_revision': (self.runtime.manifest.get('selection_revision') or {}).get('revision_id'),
            'formal_batch_id': self.runtime.manifest.get('formal_batch_id'),
            'total': len(formal_ids), 'registered': len(current_formal),
            'completed': formal_states.get('completed', 0), 'states': formal_states,
            'paused_no_progress': formal_states.get('paused_no_progress', 0),
        }
        formal_progress['percent'] = (100.0 * formal_progress['completed'] / len(formal_ids)
                                      if formal_ids else None)
        rate_work = current_formal if formal_ids else work
        rate_completed = [entry for entry in rate_work if entry['state'] == 'completed']
        if formal_ids:
            with self.ledger.connect() as db:
                first = db.execute(
                    "SELECT MIN(recorded_at) FROM forest_study_events WHERE campaign_id=? "
                    "AND event_type='work_claimed' AND work_id IN (" + ','.join('?' for _ in formal_ids) + ')',
                    (self.campaign_id, *sorted(formal_ids))).fetchone()[0]
        else:
            first = min((entry['created_at'] for entry in work), default=None)
        last = max((entry['updated_at'] for entry in rate_completed), default=None)
        observed_window = last-first if first is not None and last is not None and last > first else None
        rate = len(rate_completed)/observed_window if observed_window else None
        measured = {kind: interval_measure(value) for kind,value in categories.items()}
        stages = []
        logical_jobs = {}
        for attempt in fit_attempts:
            logical_jobs.setdefault(attempt['logical_work_id'], set()).add(attempt['supervisor_work_id'])
        for stage, kind in sorted({(entry['stage'], entry['kind']) for entry in work}):
            group = [entry for entry in work if entry['stage'] == stage and entry['kind'] == kind]
            if formal_ids and any(entry['work_id'] in formal_ids for entry in group):
                group = [entry for entry in group if entry['work_id'] in formal_ids]
            done = [entry for entry in group if entry['state'] == 'completed']
            # A prior failed batch must not start the observation window for
            # its successor. Use only claims belonging to this current group.
            if formal_ids and any(entry['work_id'] in formal_ids for entry in group):
                with self.ledger.connect() as db:
                    ids = [entry['work_id'] for entry in group]
                    left = db.execute(
                        "SELECT MIN(recorded_at) FROM forest_study_events WHERE campaign_id=? "
                        "AND event_type='work_claimed' AND work_id IN (" + ','.join('?' for _ in ids) + ')',
                        (self.campaign_id, *ids)).fetchone()[0]
            else:
                left = stage_starts.get((stage, kind))
            if kind in GPU_KINDS:
                group_ids = {entry['work_id'] for entry in group}
                group_ids.update(job for logical in tuple(group_ids) for job in logical_jobs.get(logical, ()))
                starts = [entry['started_at'] for entry in compute
                          if entry['work_id'] in group_ids and entry['started_at'] is not None]
                # Imported/reconciled original fits can predate their study
                # claim. Receipt publication must not masquerade as fit speed.
                if starts:
                    left = min(starts + ([left] if left is not None else []))
            right = max((entry['updated_at'] for entry in done), default=None)
            elapsed = right-left if left is not None and right is not None and right > left else None
            stage_rate = len(done)/elapsed if elapsed else None
            remaining = sum(entry['state'] not in TERMINAL for entry in group)
            stages.append({'stage': stage, 'kind': kind, 'registered': len(group),
                'completed': len(done), 'remaining': remaining,
                'observed_wall_seconds': elapsed, 'completed_per_second': stage_rate,
                'eta_remaining_seconds': remaining/stage_rate if stage_rate else None})
        return {'campaign_id': self.campaign_id, 'scheduler_id': self.scheduler_id,
            'model_concurrency': self.degree, 'capacity': self._configuration(),
            'unsettled_model_work_ids': self._unsettled_model_owners(),
            'categories': measured, 'categories_can_overlap': True,
            'observed_wall_union_seconds': interval_measure([interval for kind,value in categories.items() if kind != 'unreconciled_model_interval' for interval in value])['wall_union_seconds'],
            'work_states': states, 'remaining_ready': len(ready),
            'formal_progress': formal_progress,
            'paused_no_progress_work_ids': [entry['work_id'] for entry in pending
                                            if entry['state'] == 'paused_no_progress'],
            'remaining_blocked_or_waiting': len(pending)-len(ready), 'not_run': states.get('not_run', 0),
            'completed_work_items': len(completed), 'observed_completed_items_per_second': rate,
            'stage_throughput': stages,
            'eta_ready_seconds': len(ready)/rate if rate else None,
            'eta_blocked_seconds': None, 'eta_basis': 'Observed completed work throughput; heterogeneous tasks and dependency waits can change the rate'}

    def run(self, *, daemon=False, wait_timeout=None):
        deadline = time.monotonic() + wait_timeout if wait_timeout is not None else None
        while deadline is None or time.monotonic() < deadline:
            value = self.tick()
            if value['state'] == 'budget_exhausted' or value['state'] in ('idle', 'blocked') and not daemon:
                return {**value, 'metrics': self.metrics()}
            time.sleep(self.poll_seconds)
        return {'state': 'caller_wait_ended', 'metrics': self.metrics()}

    def close(self, *, wait=True):
        if self._closed:
            return
        for executor in self._executors.values():
            executor.shutdown(wait=wait, cancel_futures=not wait)
        if wait and self._owned:
            with self.ledger.connect() as db:
                db.execute("UPDATE forest_study_schedulers SET state='stopped',pid=NULL,process_created=NULL,updated_at=? WHERE campaign_id=? AND session_id=?", (time.time(), self.campaign_id, self.session_id))
        self._closed = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('run', 'status', 'controller-worker'))
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--ledger-id', required=True)
    parser.add_argument('--scheduler-id')
    parser.add_argument('--maximum-model-concurrency', type=int, default=2)
    parser.add_argument('--model-concurrency', type=int, default=1)
    parser.add_argument('--cpu-concurrency', type=int, default=1)
    parser.add_argument('--gpu-device', action='append', default=[])
    parser.add_argument('--pilot')
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--wait-timeout', type=float)
    parser.add_argument('--controller-request')
    args = parser.parse_args(argv)
    if args.operation == 'controller-worker':
        if not args.controller_request:
            raise ValueError('Controller worker requires its durable original request')
        print(_json(controller_worker(args.controller_request)))
        return
    if args.operation == 'status':
        ledger_path = Path(args.ledger).resolve()
        if not ledger_path.is_file():
            raise ValueError('Status requires the original existing study ledger')
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        runtime = SimpleNamespace(campaign=Path(args.campaign).resolve(), ledger_path=ledger_path,
            ledger_id=args.ledger_id, manifest=manifest, campaign_id=manifest['campaign_id'],
            ledger=StudyLedger(ledger_path, args.ledger_id))
        with runtime.ledger.connect() as db:
            original = db.execute('SELECT ledger_id FROM forest_study_campaigns WHERE campaign_id=?', (runtime.campaign_id,)).fetchone()
            if original is None or original['ledger_id'] != args.ledger_id:
                raise ValueError('Status campaign must match its registered original ledger ID')
    else:
        runtime = StudyRuntime(campaign=Path(args.campaign), ledger=Path(args.ledger),
            ledger_id=args.ledger_id, manifest=Path(args.manifest))
    scheduler = StudyScheduler(runtime, scheduler_id=args.scheduler_id,
        maximum_model_concurrency=args.maximum_model_concurrency, model_concurrency=args.model_concurrency,
        cpu_concurrency=args.cpu_concurrency, gpu_devices=args.gpu_device, pilot_path=args.pilot)
    try:
        value = scheduler.run(daemon=args.daemon, wait_timeout=args.wait_timeout) if args.operation == 'run' else scheduler.metrics()
        print(_json(value))
    finally:
        scheduler.close()


if __name__ == '__main__':
    main()
