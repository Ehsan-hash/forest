"""Detached commands with bound launch identity and preserved original receipts.

Request/identity/receipt hashes establish which configuration actually executed.
Editable working files remain separate from saved observations. Unknown launches
require reconciliation, and replay of an existing job never starts another fit.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
import uuid

import psutil

TERMINAL = {'completed', 'failed', 'cancelled', 'lost'}
RECONCILIATION = 'reconciliation'
_ARTIFACT_VALIDATION_CACHE={}
_SOURCE_HASH_CACHE={}
_SOURCE_SUFFIXES={'.py','.pyi','.js','.jsx','.mjs','.cjs','.ts','.tsx','.r','.R',
    '.jl','.c','.h','.cpp','.cc','.cu','.f','.f90','.sh'}
_CHECKPOINT_FILES={'agent_session.json','agent_transcript.json','task_progress.json',
    'context_packet.json','agent_result.json','agent_usage.json','research_memory.json'}


def file_hash(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def workspace_inventory(workspace):
    """File metadata locates changed artifacts without re-reading unchanged data."""
    workspace=Path(workspace)
    inventory={}
    for path in workspace.rglob('*'):
        bookkeeping=(path.name in _CHECKPOINT_FILES or
            any(path.name.startswith(name+'.') and path.name.endswith('.tmp')
                for name in _CHECKPOINT_FILES))
        if (not path.is_file() or path.is_symlink() or bookkeeping or
                any(part.startswith('.forest-') or part in ('__pycache__','.git')
                    for part in path.relative_to(workspace).parts)):
            continue
        try: stat=path.stat()
        except FileNotFoundError:
            continue  # An independent atomic publisher may have renamed it.
        inventory[path.relative_to(workspace).as_posix()]={'size':stat.st_size,
            'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns}
    return inventory


def artifact_is_valid(artifact):
    """Check the saved original; edits to an editable working file remain legal."""
    path=Path(artifact['path'])
    if not path.is_file(): return False
    stat=path.stat()
    identity=(stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,artifact.get('sha256'))
    cached=_ARTIFACT_VALIDATION_CACHE.get(str(path))
    if cached is None or cached[0]!=identity:
        cached=(identity,file_hash(path)==artifact.get('sha256'))
        _ARTIFACT_VALIDATION_CACHE[str(path)]=cached
    return cached[1]


def configuration_hash(request):
    return hashlib.sha256(json.dumps({key:request.get(key) for key in (
        'command','cwd','env','timeout','source_hashes')},sort_keys=True,
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def request_sources(command,workspace,cwd,code_inputs=None):
    """Bind argv inputs and the authorized workspace method source tree.

    Config/data inputs beyond argv may be named explicitly by ``code_inputs``.
    Imports through PYTHONPATH/sys.path may reach outside the command cwd, so
    every editable source module in the workspace participates in job identity.
    Unchanged verified source is served from file metadata keyed hash cache.
    """
    workspace=Path(workspace).resolve()
    sources={}
    candidates=set()
    roots={workspace}
    arguments=list(command)
    # Shell command strings reach the manager as /bin/sh -c. The executable
    # script still establishes its own import directory outside a nested cwd.
    if (len(command)>=3 and Path(command[0]).name in {'sh','bash','dash','zsh'}
            and command[1]=='-c'):
        try: arguments.extend(shlex.split(command[2]))
        except ValueError: pass
    for value in [*arguments,*(code_inputs or [])]:
        try:
            path=(Path(cwd)/value).resolve()
            if path.is_relative_to(workspace) and not path.is_symlink():
                if path.is_file():
                    candidates.add(path)
                    if path.suffix in _SOURCE_SUFFIXES:
                        roots.add(path.parent)
                elif value in (code_inputs or []) and path.is_dir():
                    roots.add(path)
        except OSError:
            # Inline source, flags, and other argv values are not file paths.
            continue
    for root in roots:
        if not root.is_relative_to(workspace):
            continue
        for path in root.rglob('*'):
            relative=path.relative_to(workspace)
            if (path.suffix in _SOURCE_SUFFIXES and not path.is_symlink() and path.is_file()
                    and not any(part.startswith('.') or part in ('__pycache__','node_modules','build','dist','venv') for part in relative.parts)):
                candidates.add(path)
    for path in sorted(candidates):
        stat=path.stat()
        identity=(stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        cached=_SOURCE_HASH_CACHE.get(str(path))
        if cached is None or cached[0]!=identity:
            cached=(identity,file_hash(path))
            _SOURCE_HASH_CACHE[str(path)]=cached
        sources[path.relative_to(workspace).as_posix()]=cached[1]
    return sources


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def is_alive(pid, created):
    if not pid or created is None:
        return False
    try:
        process = psutil.Process(int(pid))
        return abs(process.create_time() - float(created)) < .1 and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def signal_process_group(process_group_id, signum):
    """Signal an owned command group, even after its original command exits."""
    if not process_group_id:
        return False
    try:
        os.killpg(int(process_group_id), signum)
        return True
    except ProcessLookupError:
        return False


class ManagedProcesses:
    def __init__(self, workspace, *, directory=None, legacy_directory=None, code_inputs=None):
        self.workspace = Path(workspace).resolve()
        self.directory = Path(directory) if directory else self.workspace / '.forest-processes'
        self.legacy_directory=(Path(legacy_directory) if legacy_directory else
            self.workspace.parent/'.forest-processes'/self.workspace.name if directory is None else None)
        self.code_inputs=code_inputs
        self.directory.mkdir(parents=True, exist_ok=True)

    def folder(self, process_id):
        if not process_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in str(process_id)):
            raise ValueError('Invalid process ID')
        legacy=self.legacy_directory/str(process_id) if self.legacy_directory else None
        return legacy if legacy and (legacy/'request.json').is_file() else self.directory/str(process_id)

    def start(self, command, *, process_id=None, cwd='.', env=None, timeout=None):
        process_id = process_id or str(uuid.uuid4())
        folder = self.folder(process_id)
        folder.mkdir(parents=True, exist_ok=True)
        requested_cwd = (self.workspace / cwd).resolve()
        if not requested_cwd.is_relative_to(self.workspace) or not requested_cwd.is_dir():
            raise ValueError('Process working directory must exist inside the workspace')
        if isinstance(command, str):
            command = ['/bin/sh', '-c', command]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
            raise ValueError('command must be a nonempty argv list or shell command string')
        if timeout is not None and float(timeout) <= 0:
            raise ValueError('timeout must be positive or omitted')
        request = {'process_id': process_id, 'command': command, 'cwd': str(requested_cwd), 'env': env or {}, 'timeout': timeout, 'created_at': time.time(),
            'workspace':str(self.workspace),'source_hashes':request_sources(command,self.workspace,requested_cwd,self.code_inputs)}
        request['configuration_hash']=configuration_hash(request)
        request_path = folder / 'request.json'
        import fcntl
        # All launchers serialize request ownership before a supervisor can
        # read it. Different commands cannot overwrite the same job intent.
        with (folder/'request.lease').open('a') as lease:
            fcntl.flock(lease.fileno(),fcntl.LOCK_EX)
            if request_path.exists():
                try: previous = read_json(request_path)
                except (OSError,ValueError,UnicodeError): return self.inspect(process_id)
                if any(previous.get(key) != request.get(key) for key in ('command', 'cwd', 'env', 'timeout')):
                    raise ValueError('This process ID already belongs to a different command; choose a new ID')
                state = self.inspect(process_id)
                if state.get('pid') or state['status'] in (*TERMINAL,RECONCILIATION) or is_alive(state.get('host_pid'), state.get('host_created')) or state.get('launch_committed'):
                    return state
            else:
                # Persist launch intent before creating any process.
                request['workspace_inventory']=workspace_inventory(self.workspace)
                atomic_json(request_path, request)
        # The supervisor holds a coordination lease for its entire lifetime; a
        # duplicate launcher cannot execute the command twice. This lease never
        # prevents the user editing source code, task state, or output files.
        host = Path(__file__).with_name('process_host.py')
        with (folder / 'supervisor.log').open('ab') as log:
            subprocess.Popen([sys.executable, str(host), str(folder)], cwd=self.workspace,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            state = self.inspect(process_id)
            if state.get('pid') or state['status'] in (*TERMINAL,RECONCILIATION):
                return state
            time.sleep(.02)
        return state

    def inspect(self, process_id):
        folder = self.folder(process_id)
        try:
            request = read_json(folder / 'request.json')
            state = read_json(folder / 'state.json', {'status': 'starting'})
            identity = read_json(folder / 'identity.json')
            receipt = read_json(folder / 'completion.json')
        except (OSError,ValueError,UnicodeError) as exc:
            return {'process_id':process_id,'status':RECONCILIATION,
                'error':'Executor record is unreadable; reconcile original launch records: '+str(exc)}
        if not request:
            raise ValueError('Unknown process ID: ' + str(process_id))
        expected=request.get('configuration_hash')
        if not expected or expected!=configuration_hash(request):
            return {**request,**state,'status':RECONCILIATION,'error':'Launch configuration is unbound or has changed; reconcile the original request and artifacts before continuing'}
        if any(record and record.get('configuration_hash')!=expected for record in (state if (folder/'state.json').exists() else {},identity,receipt)):
            return {**request,**state,'status':RECONCILIATION,'error':'Executor identity/state/receipt configuration hashes disagree'}
        if identity and state.get('launch_id')!=identity.get('launch_id'):
            return {**request,**state,'status':RECONCILIATION,'error':'Executor identity does not belong to the committed launch'}
        if identity:
            state.update(identity)
        value = {'process_id': process_id, 'command': request['command'], 'created_at': request['created_at'],
            'configuration_hash':expected, **state}
        if value['status'] in ('completed','failed','cancelled'):
            if (not receipt or receipt.get('launch_id')!=value.get('launch_id') or
                    receipt.get('status')!=value['status'] or receipt.get('exit_code')!=value.get('exit_code')):
                value.update(status=RECONCILIATION,error='Terminal executor state has no consistent completion receipt')
                return value
            if receipt.get('artifact_errors'):
                value.update(status=RECONCILIATION,error='Original completion artifacts could not be preserved',
                    artifact_errors=receipt['artifact_errors'])
                return value
            for artifact in receipt.get('artifacts',[]):
                if not artifact_is_valid(artifact):
                    value.update(status=RECONCILIATION,error='Original completion artifact is missing or has changed',artifact=artifact)
                    return value
            value.update(completion_receipt=str(folder/'completion.json'),artifacts=receipt.get('artifacts',[]))
            return value
        if value['status'] not in TERMINAL and is_alive(value.get('pid'), value.get('process_created')):
            try:
                proc = psutil.Process(value['pid'])
                value.update(status='paused' if proc.status() == psutil.STATUS_STOPPED else 'running', rss_bytes=proc.memory_info().rss)
            except psutil.NoSuchProcess:
                pass
        elif value['status'] not in TERMINAL and not is_alive(value.get('host_pid'), value.get('host_created')) and time.time() - request['created_at'] > 5:
            value.update(status=RECONCILIATION, error='Process exited without a bound completion receipt. Reconcile executor state and original artifacts; execution is unconfirmed.')
        return value

    def read_output(self, process_id, offset=0, limit=24000, stream='stdout'):
        if stream not in ('stdout', 'stderr'):
            raise ValueError('stream must be stdout or stderr')
        self.inspect(process_id)
        path = self.folder(process_id) / (stream + '.log')
        if offset < 0 or not 0 < limit <= 1_000_000:
            raise ValueError('offset must be nonnegative and limit between 1 and 1000000 bytes')
        if not path.exists():
            return {'process_id': process_id, 'stream': stream, 'content': '', 'next_offset': offset}
        with path.open('rb') as handle:
            handle.seek(offset)
            content = handle.read(limit)
            position = handle.tell()
        return {'process_id': process_id, 'stream': stream, 'content': content.decode('utf-8', errors='replace'), 'next_offset': position}

    def cancel(self, process_id):
        state = self.inspect(process_id)
        atomic_json(self.folder(process_id) / 'cancel.json', {'requested_at': time.time()})
        if state['status'] not in (*TERMINAL,RECONCILIATION):
            group_id = state.get('process_group_id') or state.get('pid')
            signal_process_group(group_id, signal.SIGCONT)
            signal_process_group(group_id, signal.SIGTERM)
        return self.inspect(process_id)

    def all(self):
        directories=[self.directory,*([self.legacy_directory] if self.legacy_directory and self.legacy_directory.exists() else [])]
        identifiers={path.name for directory in directories for path in directory.iterdir()
            if path.is_dir() and (path/'request.json').exists()}
        return [self.inspect(ident) for ident in sorted(identifiers)]

    def cancel_all(self):
        return [self.cancel(p['process_id']) for p in self.all() if p['status'] not in TERMINAL]

    def signal_all(self, signum):
        for state in self.all():
            if state['status'] not in (*TERMINAL,RECONCILIATION):
                group_id = state.get('process_group_id') or state.get('pid')
                signal_process_group(group_id, signum)
