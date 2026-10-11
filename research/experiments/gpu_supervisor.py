"""Durable compute ownership and direct receipts in an existing SQLite ledger.

The legacy ledger is read without alteration. New versioned records use explicit
work IDs and complete conditions, never content-addressed identities. Physical
GPU allocation time and CPU execution time are separate measured resources.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from research.agents.processes import (ManagedProcesses, atomic_json, binding_matches, configuration_record,
    is_alive, read_json, signal_process_group)
from research.agents.artifacts import equal_files, file_identity, record_file


TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'budget_exhausted'})
# The current independent ManagedProcesses host has a 3-second TERM grace,
# .1-second polling, plus this meter's .05-second polling and dispatch margin.
# This is process-stop latency, never a scientific lifetime/phase quota.
MINIMUM_TERMINATION_MARGIN_SECONDS = 3.25
SCHEMA = '''
CREATE TABLE IF NOT EXISTS forest_compute_policies (
 ledger_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, configuration_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forest_compute_policy_history (
 ledger_id TEXT NOT NULL, revision INTEGER NOT NULL, configuration_json TEXT NOT NULL,
 recorded_at REAL NOT NULL, PRIMARY KEY(ledger_id,revision));
CREATE TABLE IF NOT EXISTS forest_compute_work (
 work_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL, configuration_json TEXT NOT NULL,
 state TEXT NOT NULL, accepted_at REAL NOT NULL, launch_requested INTEGER NOT NULL DEFAULT 0,
 started_at REAL, effective_timeout REAL, accounted_ms INTEGER NOT NULL DEFAULT 0,
 supervisor_pid INTEGER, supervisor_created REAL, receipt_json TEXT NOT NULL DEFAULT '{}',
 error TEXT);
CREATE TABLE IF NOT EXISTS forest_compute_usage (
 entry_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL, work_id TEXT NOT NULL,
 resource_type TEXT NOT NULL, quantity INTEGER NOT NULL, interval_key INTEGER NOT NULL,
 receipt_json TEXT NOT NULL, recorded_at REAL NOT NULL,
 UNIQUE(work_id,resource_type,interval_key));
'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be a finite positive number')
    return float(value)


def resolve_gpu_devices(requested, inventory):
    """Resolve physical aliases; index and UUID must share one allocation."""
    records = []
    aliases = {}
    for line in inventory.splitlines():
        if not line.strip():
            continue
        fields = [value.strip() for value in line.split(',')]
        if len(fields) != 2 or not all(fields):
            raise ValueError('Physical GPU inventory cannot be reliably parsed')
        index, identifier = fields
        records.append({'index': index, 'uuid': identifier})
        aliases[index] = aliases[identifier] = identifier
    if any(device not in aliases for device in requested):
        raise ValueError('The requested physical GPU devices are unavailable')
    resolved = [aliases[device] for device in requested]
    if len(set(resolved)) != len(resolved):
        raise ValueError('Different aliases cannot allocate the same physical GPU twice')
    return resolved, records


def _confirmed_stop(observation):
    status = observation.get('confirmed_execution_status') or observation.get('status')
    return status if status in ('completed', 'failed', 'cancelled') and observation.get('finished_at') is not None else None


def _meter_observation(observation):
    """Keep timing proof plus original references, without copying code per tick."""
    fields = ('status', 'process_id', 'work_id', 'launch_id', 'pid', 'process_created',
        'host_pid', 'host_created', 'process_group_id', 'created_at', 'started_at',
        'finished_at', 'exit_code', 'elapsed_seconds', 'error',
        'confirmed_execution_status', 'completion_receipt', 'supervisor_stop_receipt')
    return {key: deepcopy(observation[key]) for key in fields if key in observation}


def _group_live(group):
    """Observe live members; zombies cannot continue consuming the device."""
    import psutil
    for process in psutil.process_iter():
        try:
            if os.getpgid(process.pid) == group and process.status() != psutil.STATUS_ZOMBIE:
                return True
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
        except (PermissionError, psutil.AccessDenied):
            return True
    return False


class ComputeSupervisor:
    def __init__(self, ledger, ledger_id):
        self.ledger = Path(ledger).resolve()
        self.ledger_id = str(ledger_id)
        if not self.ledger_id:
            raise ValueError('ledger_id is required')
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.directory = self.ledger.parent / '.forest-compute'
        self.directory.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.ledger, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        return db

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def configure(self, *, gpu_budget_seconds, wall_start, wall_seconds, campaign_id=None,
                  termination_margin_seconds=3.25):
        configuration = {'version': 1, 'gpu_budget_ms': int(_positive(gpu_budget_seconds, 'gpu_budget_seconds') * 1000),
            'wall_start': float(wall_start), 'wall_seconds': _positive(wall_seconds, 'wall_seconds'),
            'campaign_id': campaign_id,
            'termination_margin_seconds': _positive(termination_margin_seconds, 'termination_margin_seconds')}
        if not math.isfinite(configuration['wall_start']):
            raise ValueError('wall_start must be an explicit finite epoch time')
        if configuration['termination_margin_seconds'] < MINIMUM_TERMINATION_MARGIN_SECONDS:
            raise ValueError('termination_margin_seconds must cover the independent process host stop latency (at least 3.25 seconds)')
        with self.transaction() as db:
            if campaign_id and self._table(db, 'campaigns'):
                legacy = db.execute('SELECT gpu_budget_ms FROM campaigns WHERE campaign_id=?', (campaign_id,)).fetchone()
                if legacy and configuration['gpu_budget_ms'] > legacy['gpu_budget_ms']:
                    raise ValueError('The original campaign GPU hard allowance cannot be exceeded')
            previous = db.execute('SELECT * FROM forest_compute_policies WHERE ledger_id=?', (self.ledger_id,)).fetchone()
            if previous and json.loads(previous['configuration_json']) == configuration:
                return configuration
            revision = previous['revision'] + 1 if previous else 1
            db.execute('INSERT INTO forest_compute_policies VALUES(?,?,?) ON CONFLICT(ledger_id) DO UPDATE SET revision=excluded.revision,configuration_json=excluded.configuration_json',
                (self.ledger_id, revision, _json(configuration)))
            db.execute('INSERT INTO forest_compute_policy_history VALUES(?,?,?,?)',
                (self.ledger_id, revision, _json(configuration), time.time()))
        return configuration

    @staticmethod
    def _table(db, name):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _policy(self, db):
        row = db.execute('SELECT * FROM forest_compute_policies WHERE ledger_id=?', (self.ledger_id,)).fetchone()
        if row is None:
            raise ValueError('Configure the actual GPU and wall allowances before submission')
        return json.loads(row['configuration_json'])

    def _totals(self, db):
        # Legacy rows remain original, including corrections and their IDs.
        legacy = float(db.execute("SELECT COALESCE(SUM(quantity),0) FROM resource_ledger WHERE resource_type='gpu_allocated_ms'").fetchone()[0]) if self._table(db, 'resource_ledger') else 0.0
        # The physical ledger is shared across work/phase/owner revisions. A
        # different logical ledger ID cannot reset already consumed hardware.
        measured = float(db.execute("SELECT COALESCE(SUM(quantity),0) FROM forest_compute_usage WHERE resource_type='gpu_allocated_ms'").fetchone()[0])
        return {'legacy_gpu_ms': legacy, 'new_gpu_ms': measured, 'gpu_ms': legacy + measured}

    def _row(self, db, work_id):
        row = db.execute('SELECT * FROM forest_compute_work WHERE work_id=? AND ledger_id=?', (work_id, self.ledger_id)).fetchone()
        if row is None:
            raise ValueError('Unknown work ID in this ledger')
        return dict(row)

    def _manager(self, row):
        configuration = json.loads(row['configuration_json'])
        return ManagedProcesses(configuration['workspace'], directory=self.directory / row['work_id'] / 'processes',
                                code_inputs=configuration.get('code_inputs'))

    def submit(self, command, workspace, *, work_id=None, configuration=None, timeout_seconds,
               gpu_devices=(), cwd='.', env=None, phase=None):
        work_id = work_id or str(uuid.uuid4())
        # Use the same ID syntax as the durable process registry.
        if not work_id or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for character in work_id):
            raise ValueError('work_id must be an explicit process-compatible stable ID')
        if not isinstance(command, list) or not command or any(not isinstance(value, str) for value in command):
            raise ValueError('command must be a nonempty argv list')
        workspace = Path(workspace).resolve()
        actual_cwd = (workspace / cwd).resolve()
        if not workspace.is_dir() or not actual_cwd.is_dir() or not actual_cwd.is_relative_to(workspace):
            raise ValueError('The authorized workspace and command cwd must exist within the workspace')
        devices = list(gpu_devices)
        if any(not isinstance(value, str) or not value for value in devices) or len(set(devices)) != len(devices):
            raise ValueError('GPU allocation must name distinct actual device identifiers')
        actual_env = deepcopy(env or {})
        if not isinstance(actual_env, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key,value in actual_env.items()):
            raise ValueError('env must map explicit names to string values')
        # Declared CPU work cannot inherit otherwise visible GPUs, including a
        # CUDA selection supplied through the generic environment argument.
        actual_env['CUDA_VISIBLE_DEVICES'] = ','.join(devices)
        conditions = {'version': 1, 'command': deepcopy(command), 'workspace': str(workspace), 'cwd': cwd,
            'env': actual_env, 'timeout_seconds': _positive(timeout_seconds, 'timeout_seconds'),
            'gpu_devices': devices, 'requested_gpu_devices': devices,
            'research_configuration': deepcopy(configuration or {}), 'phase': phase,
            'code_inputs': deepcopy((configuration or {}).get('code_inputs'))}
        with self.transaction() as db:
            self._policy(db)
            previous = db.execute('SELECT * FROM forest_compute_work WHERE work_id=?', (work_id,)).fetchone()
            if previous:
                prior_conditions = json.loads(previous['configuration_json'])
                # Accepted physical aliases remain attributable even when a
                # finished job's old hardware is no longer present at replay.
                conditions['gpu_devices'] = prior_conditions['gpu_devices']
                conditions['env']['CUDA_VISIBLE_DEVICES'] = ','.join(prior_conditions['gpu_devices'])
                if previous['ledger_id'] != self.ledger_id or {key:value for key,value in prior_conditions.items() if key not in ('source_originals', 'gpu_inventory')} != conditions:
                    raise ValueError('Accepted work ID belongs to different original execution conditions; submit a new work revision')
            else:
                if devices:
                    detected = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader'],
                        capture_output=True, text=True, check=True, timeout=10)
                    resolved, inventory = resolve_gpu_devices(devices, detected.stdout)
                    conditions.update(gpu_devices=resolved, gpu_inventory=inventory)
                    conditions['env']['CUDA_VISIBLE_DEVICES'] = ','.join(resolved)
                # Installed executables or modules may be outside the writable
                # output workspace. Explicit owner-supplied source references
                # retain the actual accepted code without content addressing.
                sources = (configuration or {}).get('source_files') or []
                if not isinstance(sources, list) or any(not isinstance(path, str) for path in sources):
                    raise ValueError('source_files must be explicit original file paths')
                originals = [record_file(path, self.directory / 'source-originals') for path in sources]
                conditions['source_originals'] = [{**original, 'snapshot_metadata': file_identity(original['snapshot_path'])} for original in originals]
                db.execute('INSERT INTO forest_compute_work(work_id,ledger_id,configuration_json,state,accepted_at) VALUES(?,?,?,?,?)',
                    (work_id, self.ledger_id, _json(conditions), 'accepted', time.time()))
        self._ensure_supervisor(work_id)
        return self.status(work_id)

    def _ensure_supervisor(self, work_id):
        with self.connect() as db:
            row = self._row(db, work_id)
        if row['state'] in TERMINAL or is_alive(row['supervisor_pid'], row['supervisor_created']):
            return
        if row['state'] == 'reconciliation':
            original = self._inspect(row)
            if original.get('started_at') is None or _confirmed_stop(original) or row['error'] and row['error'].startswith('Original job stop requires reconciliation:'):
                return
        folder = self.directory / work_id
        folder.mkdir(exist_ok=True)
        # Duplicate helper processes serialize on the work lease before any
        # accepted child launch. They do not create new scientific work IDs.
        with (folder / 'supervisor.log').open('ab') as log:
            subprocess.Popen([sys.executable, '-m', 'research.experiments.gpu_supervisor', 'run',
                '--ledger', str(self.ledger), '--ledger-id', self.ledger_id, '--work-id', work_id],
                cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True, close_fds=True)

    def _inspect(self, row):
        manager = self._manager(row)
        if not row['launch_requested']:
            return {'status': row['state']}
        if (row['state'] in ('launching', 'starting')
                and not (manager.folder(row['work_id']) / 'request.json').exists()
                and is_alive(row['supervisor_pid'], row['supervisor_created'])):
            # The proven meter owns the first launch between its committed
            # allocation and original request publication. Observe that owner;
            # a dead owner with the same missing request requires reconciliation.
            return {'status': 'starting', 'pending_original_launch': True}
        conditions = json.loads(row['configuration_json'])
        source_error = None
        try:
            if any(original['snapshot_metadata'] != file_identity(original['snapshot_path']) for original in conditions.get('source_originals', [])):
                raise ValueError('Retained source original was changed')
        except (OSError, KeyError, ValueError) as exc:
            source_error = 'Original accepted source requires reconciliation: ' + str(exc)
        try:
            observation = manager.inspect(row['work_id'])
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            observation = {'status': 'reconciliation', 'error': 'Original launch record cannot be reconciled: ' + str(exc)}
        if source_error:
            confirmed_stop = _confirmed_stop(observation)
            observation.update(status='reconciliation', error=source_error)
            if confirmed_stop:
                observation['confirmed_execution_status'] = confirmed_stop
        if observation.get('status') == 'reconciliation':
            # An unreadable completion file does not erase a separately valid
            # original launch clock. This only supplies metering, never a
            # completion assertion or permission to relaunch unknown work.
            for key in ('started_at', 'finished_at', 'confirmed_execution_status'):
                observation.pop(key, None)
            try:
                folder = manager.folder(row['work_id'])
                request = read_json(folder / 'request.json')
                state = read_json(folder / 'state.json')
                conditions = json.loads(row['configuration_json'])
                valid_request = (request.get('record_version') == 2
                        and request.get('work_id') == row['work_id']
                        and request.get('configuration') == configuration_record(request)
                        and request.get('workspace') == conditions['workspace']
                        and request.get('cwd') == str((Path(conditions['workspace']) / conditions['cwd']).resolve())
                        and request.get('command') == conditions['command']
                        and request.get('env') == conditions['env']
                        and request.get('timeout') == row['effective_timeout']
                        and request.get('experiment') == conditions['research_configuration'])
                if valid_request and state.get('launch_committed') and binding_matches(request, state):
                    observation['started_at'] = state['started_at']
                    # Original completion proves the owned group stopped even
                    # if its scientific artifact capture later needs repair.
                    identity = read_json(folder / 'identity.json')
                    receipt = read_json(folder / 'completion.json')
                    if (state.get('status') in ('completed', 'failed', 'cancelled')
                            and identity and receipt
                            and all(binding_matches(request, record) for record in (identity, receipt))
                            and state.get('launch_id') == identity.get('launch_id') == receipt.get('launch_id')
                            and state.get('status') == receipt.get('status')
                            and state.get('exit_code') == receipt.get('exit_code')
                            and state.get('finished_at') == receipt.get('finished_at')):
                        observation.update(finished_at=receipt['finished_at'], confirmed_execution_status=receipt['status'])
            except (OSError, ValueError, KeyError, RuntimeError):
                pass  # Retain any previously metered clock and original hold.
            # A separate supervisor may have proved and recorded physical stop
            # after the original receipt host died. This is a resource receipt,
            # never a fabricated successful fit or reconstructed model output.
            try:
                stopped = read_json(self.directory / row['work_id'] / 'supervisor-stop.json')
                if (stopped and stopped.get('work_id') == row['work_id']
                        and stopped.get('configuration') == conditions
                        and stopped.get('process_group_live') is False):
                    observation.update(started_at=stopped['started_at'], finished_at=stopped['finished_at'],
                        confirmed_execution_status='cancelled',
                        supervisor_stop_receipt=str(self.directory / row['work_id'] / 'supervisor-stop.json'))
            except (OSError, ValueError, KeyError):
                pass
        return observation

    def _meter(self, db, row, observation):
        # A proven stopped job no longer owns compute. Later loss or editing of
        # its historical evidence must not turn artifact repair into new usage.
        # Its immutable original observed endpoint remains in receipt_json.
        if row['state'] in TERMINAL or _confirmed_stop(json.loads(row['receipt_json'])):
            return row
        conditions = json.loads(row['configuration_json'])
        started = row['started_at'] or observation.get('started_at')
        if started is None:
            return row
        finished = observation.get('finished_at') if _confirmed_stop(observation) else None
        endpoint = finished if finished is not None else time.time()
        target = max(row['accounted_ms'], max(0, int((endpoint - float(started)) * 1000)))
        delta = target - row['accounted_ms']
        timing = _meter_observation(observation)
        if delta:
            resource = 'gpu_allocated_ms' if conditions['gpu_devices'] else 'cpu_ms'
            quantity = delta * len(conditions['gpu_devices']) if conditions['gpu_devices'] else delta
            record = {'version': 1, 'work_id': row['work_id'], 'ledger_id': row['ledger_id'],
                'from_ms': row['accounted_ms'], 'through_ms': target, 'started_at': started,
                'phase': conditions['phase'],
                'configuration_reference': {'ledger': str(self.ledger), 'table': 'forest_compute_work',
                    'work_id': row['work_id'], 'configuration_version': conditions['version']},
                'original_process_directory': str(self._manager(row).folder(row['work_id'])),
                'observation': timing}
            db.execute('INSERT INTO forest_compute_usage VALUES(?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), row['ledger_id'], row['work_id'], resource, quantity, target, _json(record), time.time()))
        db.execute('UPDATE forest_compute_work SET started_at=?,accounted_ms=?,receipt_json=? WHERE work_id=?',
            (started, target, _json(timing), row['work_id']))
        return {**row, 'started_at': started, 'accounted_ms': target, 'receipt_json': _json(timing)}

    def status(self, work_id=None):
        with self.transaction() as db:
            policy = self._policy(db)
            if work_id is None:
                for value in db.execute('SELECT * FROM forest_compute_work WHERE ledger_id=?', (self.ledger_id,)).fetchall():
                    self._meter(db, dict(value), self._inspect(dict(value)))
                return {'ledger_id': self.ledger_id, 'policy': policy, **self._totals(db)}
            row = self._row(db, work_id)
            observation = self._inspect(row)
            row = self._meter(db, row, observation)
            state = observation.get('status', row['state']) if row['state'] not in TERMINAL else row['state']
            confirmed_stop = _confirmed_stop(observation) or _confirmed_stop(json.loads(row['receipt_json']))
            historical_integrity_error = (row['state'] in TERMINAL or confirmed_stop) and observation.get('status') == 'reconciliation'
            if historical_integrity_error:
                state = 'reconciliation'
            if state in ('completed', 'failed', 'cancelled') and (
                    row['error'] == 'Actual execution allowance exhausted' or
                    (row['effective_timeout'] is not None and row['accounted_ms'] >= row['effective_timeout'] * 1000)):
                state = 'budget_exhausted'
            if not historical_integrity_error and state in (*TERMINAL, 'reconciliation'):
                db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                    (state, row['error'] or observation.get('error'), work_id))
            elif historical_integrity_error and confirmed_stop and row['state'] not in TERMINAL:
                db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                    (confirmed_stop, observation.get('error'), work_id))
            return {'ledger_id': self.ledger_id, 'work_id': work_id, 'status': state,
                'configuration': json.loads(row['configuration_json']), 'observation': observation,
                'elapsed_ms': row['accounted_ms'], 'effective_timeout_seconds': row['effective_timeout'],
                'confirmed_execution_status': confirmed_stop or row['state'] if historical_integrity_error else None,
                'error': row['error'] or observation.get('error'), 'policy': policy, **self._totals(db)}

    def wait(self, work_id, *, timeout=None):
        deadline = time.monotonic() + _positive(timeout, 'wait timeout') if timeout is not None else None
        while True:
            self._ensure_supervisor(work_id)
            value = self.status(work_id)
            if value['status'] in TERMINAL or value['status'] == 'reconciliation':
                return value
            if deadline is not None and time.monotonic() >= deadline:
                return value  # A caller timeout is not a scientific failure.
            time.sleep(.1)

    def _allocation(self, db, row, policy):
        conditions = json.loads(row['configuration_json'])
        # Reserve only process termination latency, never a scientific phase or
        # decision quota. The original ManagedProcesses host also stops work if
        # this metering process crashes; its three-second TERM grace is covered.
        margin = policy['termination_margin_seconds']
        remaining_wall = policy['wall_start'] + policy['wall_seconds'] - time.time() - margin
        if remaining_wall <= 0:
            return None, 'Actual wall allowance exhausted'
        reserved = 0
        requested = set(conditions['gpu_devices'])
        for prior in db.execute('SELECT * FROM forest_compute_work WHERE work_id<>?', (row['work_id'],)).fetchall():
            previous = dict(prior)
            observation = self._inspect(previous)
            previous = self._meter(db, previous, observation)
            stopped_status = _confirmed_stop(observation) or _confirmed_stop(json.loads(previous['receipt_json']))
            if stopped_status:
                db.execute('UPDATE forest_compute_work SET state=? WHERE work_id=?', (stopped_status, previous['work_id']))
                previous['state'] = stopped_status
            if not previous['launch_requested'] or previous['state'] in TERMINAL:
                continue
            prior_conditions = json.loads(previous['configuration_json'])
            if requested.intersection(prior_conditions['gpu_devices']):
                return 0, 'Actual GPU devices are still allocated to original work'
            reserved += max(0, int(((previous['effective_timeout'] or 0) + margin) * 1000) - previous['accounted_ms']) * len(prior_conditions['gpu_devices'])
        effective = min(conditions['timeout_seconds'], remaining_wall)
        if requested:
            totals = self._totals(db)
            available = policy['gpu_budget_ms'] - totals['gpu_ms'] - reserved
            if available <= 0:
                return (0 if reserved else None), 'Actual shared GPU allowance exhausted or held by original work'
            effective = min(effective, available / 1000 / len(requested) - margin)
            if effective <= 0:
                return None, 'Remaining GPU allowance cannot include owned-process termination'
        return effective, None

    def _stop(self, row):
        """Stop only the original owned process group, retaining its real receipt."""
        manager = self._manager(row)
        manager.cancel(row['work_id'])
        # Hard resource boundaries must not depend on a scientific program
        # voluntarily accepting SIGTERM. Validate original request/identity
        # directly before immediately terminating its owned live process group.
        folder = manager.folder(row['work_id'])
        request = read_json(folder / 'request.json')
        identity = read_json(folder / 'identity.json')
        state = read_json(folder / 'state.json')
        conditions = json.loads(row['configuration_json'])
        if not (request and identity and request.get('work_id') == row['work_id']
                and request.get('configuration') == configuration_record(request)
                and request.get('command') == conditions['command'] and request.get('env') == conditions['env']
                and request.get('experiment') == conditions['research_configuration']
                and request.get('timeout') == row['effective_timeout']
                and request.get('workspace') == conditions['workspace']
                and request.get('cwd') == str((Path(conditions['workspace']) / conditions['cwd']).resolve())
                and binding_matches(request, identity) and binding_matches(request, state)
                and identity.get('launch_id') == state.get('launch_id')):
            raise RuntimeError('Original process ownership cannot be proven for cancellation')
        import psutil
        group = state.get('process_group_id') or identity['pid']
        if not is_alive(identity.get('pid'), identity.get('process_created')):
            try:
                if psutil.Process(identity['pid']).create_time() != identity['process_created']:
                    raise RuntimeError('Original process identity was reused; reconcile before signalling')
            except psutil.NoSuchProcess:
                pass
        signal_process_group(group, signal.SIGKILL)
        deadline = time.monotonic() + MINIMUM_TERMINATION_MARGIN_SECONDS
        while _group_live(group) and time.monotonic() < deadline:
            time.sleep(.02)
        if _group_live(group):
            raise RuntimeError('Original owned process group did not acknowledge stop')
        # Keep the real originals and direct OS stop observation. The original
        # fit receipt can remain unavailable without leaving compute unbounded.
        atomic_json(self.directory / row['work_id'] / 'supervisor-stop.json', {
            'version': 1, 'work_id': row['work_id'], 'ledger_id': row['ledger_id'],
            'configuration': json.loads(row['configuration_json']), 'original_request': request,
            'original_state': state, 'original_identity': identity, 'started_at': state['started_at'],
            'finished_at': time.time(), 'process_group_id': group, 'process_group_live': False,
            'signal': 'SIGKILL', 'reason': 'Actual execution allowance exhausted'})

    def run(self, work_id):
        folder = self.directory / work_id
        folder.mkdir(exist_ok=True)
        with (folder / 'meter.lease').open('a') as lease:
            try:
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.transaction() as db:
                row = self._row(db, work_id)
                if row['state'] in TERMINAL:
                    return
                import psutil
                db.execute('UPDATE forest_compute_work SET supervisor_pid=?,supervisor_created=? WHERE work_id=?',
                    (os.getpid(), psutil.Process().create_time(), work_id))
            first_launch = False
            while True:
                with self.transaction() as db:
                    row = self._row(db, work_id)
                    policy = self._policy(db)
                    if row['state'] in TERMINAL:
                        return
                    if not row['launch_requested']:
                        effective, reason = self._allocation(db, row, policy)
                        if effective is None:
                            db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                                ('budget_exhausted', reason, work_id))
                            return
                        if effective:
                            # Acceptance precedes the original process effect.
                            db.execute('UPDATE forest_compute_work SET launch_requested=1,state=?,effective_timeout=? WHERE work_id=?',
                                ('launching', effective, work_id))
                            row.update(launch_requested=1, effective_timeout=effective)
                            first_launch = True
                if not row['launch_requested']:
                    time.sleep(.1)
                    continue
                if first_launch:
                    conditions = json.loads(row['configuration_json'])
                    if any(not equal_files(original['path'], original['snapshot_path']) for original in conditions.get('source_originals', [])):
                        with self.transaction() as db:
                            db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                                ('reconciliation', 'Accepted source changed before original launch; inspect original source and submit a new work revision', work_id))
                        return
                    self._manager(row).start(conditions['command'], process_id=work_id,
                        cwd=conditions['cwd'], env=conditions['env'], timeout=row['effective_timeout'],
                        experiment=conditions['research_configuration'])
                    first_launch = False
                observation = self._inspect(row)
                with self.transaction() as db:
                    row = self._meter(db, self._row(db, work_id), observation)
                    policy = self._policy(db)
                    stopped_status = _confirmed_stop(observation)
                    terminal = bool(stopped_status)
                    conditions = json.loads(row['configuration_json'])
                    exhausted = (time.time() >= policy['wall_start'] + policy['wall_seconds'] - policy['termination_margin_seconds'] or
                        (row['started_at'] is not None and row['accounted_ms'] >= row['effective_timeout'] * 1000) or
                        (conditions['gpu_devices'] and self._totals(db)['gpu_ms'] >= policy['gpu_budget_ms']))
                    if terminal:
                        state = 'budget_exhausted' if exhausted or row['error'] == 'Actual execution allowance exhausted' else stopped_status
                        db.execute('UPDATE forest_compute_work SET state=? WHERE work_id=?', (state, work_id))
                        return
                    db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                        ('reconciliation' if observation.get('status') == 'reconciliation' else observation.get('status', 'running'),
                         'Actual execution allowance exhausted' if exhausted else observation.get('error'), work_id))
                if exhausted:
                    try:
                        self._stop(row)
                    except (OSError, ValueError, KeyError, RuntimeError) as exc:
                        with self.transaction() as db:
                            db.execute('UPDATE forest_compute_work SET state=?,error=? WHERE work_id=?',
                                ('reconciliation', 'Original job stop requires reconciliation: ' + str(exc), work_id))
                        return
                if observation.get('status') == 'reconciliation' and observation.get('started_at') is None:
                    # No validated original launch clock permits a watchdog
                    # to attribute compute or stop an unknown owned process.
                    # Keep reconciliation without ever recreating the command.
                    return
                time.sleep(.05)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('configure', 'submit', 'status', 'wait', 'run'))
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--ledger-id', required=True)
    parser.add_argument('--work-id')
    parser.add_argument('--campaign-id')
    parser.add_argument('--gpu-budget-seconds', type=float)
    parser.add_argument('--wall-start', type=float)
    parser.add_argument('--wall-seconds', type=float)
    parser.add_argument('--workspace')
    parser.add_argument('--command-json')
    parser.add_argument('--env-json')
    parser.add_argument('--configuration')
    parser.add_argument('--timeout-seconds', type=float)
    parser.add_argument('--gpu-device', action='append', default=[])
    parser.add_argument('--phase')
    parser.add_argument('--wait-timeout', type=float)
    parser.add_argument('--termination-margin-seconds', type=float, default=3.25)
    args = parser.parse_args(argv)
    supervisor = ComputeSupervisor(args.ledger, args.ledger_id)
    if args.operation == 'configure':
        result = supervisor.configure(gpu_budget_seconds=args.gpu_budget_seconds, wall_start=args.wall_start,
            wall_seconds=args.wall_seconds, campaign_id=args.campaign_id,
            termination_margin_seconds=args.termination_margin_seconds)
    elif args.operation == 'submit':
        result = supervisor.submit(json.loads(args.command_json), args.workspace, work_id=args.work_id,
            configuration=read_json(args.configuration) if args.configuration else {},
            timeout_seconds=args.timeout_seconds, gpu_devices=args.gpu_device, phase=args.phase,
            env=json.loads(args.env_json) if args.env_json else {})
    elif args.operation == 'status':
        result = supervisor.status(args.work_id)
    elif args.operation == 'wait':
        result = supervisor.wait(args.work_id, timeout=args.wait_timeout)
    else:
        supervisor.run(args.work_id)
        return
    print(_json(result))


if __name__ == '__main__':
    main()
