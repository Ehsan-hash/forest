"""Detached commands with bound launch identity and preserved original receipts.

Versioned request/identity/receipt records establish what actually executed.
Editable working files remain separate from saved observations. Unknown launches
require reconciliation, and replay of an existing job never starts another fit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
import uuid
import shutil
from copy import deepcopy

import psutil

TERMINAL = {'completed', 'failed', 'cancelled', 'lost'}
RECONCILIATION = 'reconciliation'
_FILE_COMPARISON_CACHE={}
_SOURCE_SUFFIXES={'.py','.pyi','.js','.jsx','.mjs','.cjs','.ts','.tsx','.r','.R',
    '.jl','.c','.h','.cpp','.cc','.cu','.f','.f90','.sh'}
_CHECKPOINT_FILES={'agent_session.json','agent_transcript.json','task_progress.json',
    'context_packet.json','agent_result.json','agent_usage.json','research_memory.json'}


def file_metadata(path):
    stat=Path(path).stat()
    return {'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns}


def file_contents_equal(first, second):
    """Compare original bytes directly, caching only while metadata is unchanged."""
    first,second=Path(first),Path(second)
    try:
        metadata=(file_metadata(first),file_metadata(second))
        key=(str(first.resolve()),str(second.resolve()))
        previous=_FILE_COMPARISON_CACHE.get(key)
        if previous and previous[0]==metadata: return previous[1]
        equal=metadata[0]['size']==metadata[1]['size']
        if equal:
            with first.open('rb') as left,second.open('rb') as right:
                while True:
                    block=left.read(1024*1024)
                    if block!=right.read(1024*1024): equal=False; break
                    if not block: break
        if len(_FILE_COMPARISON_CACHE)>512: _FILE_COMPARISON_CACHE.clear()
        _FILE_COMPARISON_CACHE[key]=(metadata,equal)
        return equal
    except OSError:
        return False


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
    return bool(artifact.get('artifact_id') and artifact.get('metadata')==file_metadata(path))


def artifact_matches(path, artifact):
    return artifact_is_valid(artifact) and file_contents_equal(path,artifact['path'])


def configuration_record(request):
    """The complete human-readable conditions belonging to one work revision."""
    return deepcopy({key:request.get(key) for key in
        ('command','cwd','env','timeout','source_references','experiment')})


def record_binding(request):
    return {'record_version':2,'work_id':request['work_id'],
        'configuration_version':request['configuration_version'],
        'configuration':configuration_record(request)}


def binding_matches(request, record):
    return all(record.get(key)==value for key,value in record_binding(request).items())


def request_sources(command,workspace,cwd,code_inputs=None):
    """Bind argv inputs and the authorized workspace method source tree.

    Config/data inputs beyond argv may be named explicitly by ``code_inputs``.
    Imports through PYTHONPATH/sys.path may reach outside the command cwd, so
    every editable source module in the workspace participates in job identity.
    File metadata supports caching. Original bytes are saved with the request
    and compared directly only when deciding whether an existing work applies.
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
        sources[path.relative_to(workspace).as_posix()]={'path':str(path),**file_metadata(path)}
    return sources


def save_request_sources(folder, request):
    """Retain the actual method input under its stable work record."""
    references={}
    for relative,metadata in request['source_references'].items():
        original=Path(metadata['path'])
        snapshot=folder/'sources'/relative
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original,snapshot)
        with snapshot.open('rb') as stream: os.fsync(stream.fileno())
        if not file_contents_equal(original,snapshot):
            raise RuntimeError('Source changed while capturing its work revision: '+relative)
        references[relative]={**metadata,**file_metadata(original),'source_record_id':str(uuid.uuid4()),
            'snapshot_path':str(snapshot),'snapshot_metadata':file_metadata(snapshot)}
    request['source_references']=references


def sources_match(request, references):
    stored=request.get('source_references') or {}
    return stored.keys()==references.keys() and all(
        file_contents_equal(record['snapshot_path'],references[relative]['path'])
        for relative,record in stored.items())


def matching_work(manager, command, *, cwd='.', env=None, timeout=None, experiment=None):
    argv=['/bin/sh','-c',command] if isinstance(command,str) else command
    requested_cwd=(manager.workspace/cwd).resolve()
    candidate={'command':argv,'cwd':str(requested_cwd),'env':env or {},
        'timeout':timeout,'experiment':deepcopy(experiment)}
    references=request_sources(argv,manager.workspace,requested_cwd,getattr(manager,'code_inputs',None))
    directories=[manager.directory,*([manager.legacy_directory] if getattr(manager,'legacy_directory',None) else [])]
    for folder in sorted({path for directory in directories if directory.exists()
            for path in directory.iterdir() if path.is_dir() and (path/'request.json').exists()}):
        try:
            request=read_json(folder/'request.json')
            if any(request.get(key)!=value for key,value in candidate.items()): continue
            if request.get('record_version')!=2: return manager.inspect(folder.name)
            if any(not reference.get('snapshot_path') or not Path(reference['snapshot_path']).is_file()
                    for reference in (request.get('source_references') or {}).values()):
                return manager.inspect(folder.name)
            if sources_match(request,references): return manager.inspect(folder.name)
        except (OSError,ValueError,KeyError,UnicodeError):
            # A matching or unreadable launch record is not evidence that no
            # work ran. Preserve its stable identity for actual reconciliation.
            return manager.inspect(folder.name)
    return None


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


def _record_versions(folder):
    versions = {}
    for name in ('request', 'state', 'identity', 'completion'):
        try:
            stat = (folder / (name + '.json')).stat()
            versions[name] = (stat.st_dev, stat.st_ino, stat.st_size,
                              stat.st_mtime_ns, stat.st_ctime_ns)
        except FileNotFoundError:
            versions[name] = None
    return versions


def _read_record_snapshot(folder):
    """Observe one stable publication without relaxing original bindings.

    The host atomically replaces individual files, so sequential JSON reads
    can straddle its launch or completion publication. File versions bracket
    all reads, including absent files; only a changed observation is retried.
    """
    for attempt in range(3):
        before = _record_versions(folder)
        error = None
        try:
            records = (read_json(folder / 'request.json'),
                       read_json(folder / 'state.json', {'status': 'starting'}),
                       read_json(folder / 'identity.json'),
                       read_json(folder / 'completion.json'))
        except (OSError, ValueError, UnicodeError) as exc:
            error = exc
        after = _record_versions(folder)
        if before == after:
            if error is not None:
                raise error
            return records, before['state'] is not None
        if attempt < 2:
            time.sleep(.005)
    raise ValueError('Concurrent executor record publication did not settle after three observations')


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

    def find_work(self,command,*,cwd='.',env=None,timeout=None,experiment=None):
        return matching_work(self,command,cwd=cwd,env=env,timeout=timeout,experiment=experiment)

    def start(self, command, *, process_id=None, cwd='.', env=None, timeout=None, experiment=None):
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
        request = {'process_id': process_id,'record_version':2,'work_id':process_id,'configuration_version':1,
            'command': command, 'cwd': str(requested_cwd), 'env': env or {}, 'timeout': timeout, 'created_at': time.time(),
            'workspace':str(self.workspace),'experiment':deepcopy(experiment),
            'source_references':request_sources(command,self.workspace,requested_cwd,self.code_inputs)}
        request_path = folder / 'request.json'
        import fcntl
        # All launchers serialize request ownership before a supervisor can
        # read it. Different commands cannot overwrite the same job intent.
        with (folder/'request.lease').open('a') as lease:
            fcntl.flock(lease.fileno(),fcntl.LOCK_EX)
            if request_path.exists():
                try: previous = read_json(request_path)
                except (OSError,ValueError,UnicodeError): return self.inspect(process_id)
                if any(previous.get(key) != request.get(key) for key in ('command', 'cwd', 'env', 'timeout','experiment')):
                    raise ValueError('This process ID already belongs to a different command; choose a new ID')
                state = self.inspect(process_id)
                if state.get('pid') or state['status'] in (*TERMINAL,RECONCILIATION) or is_alive(state.get('host_pid'), state.get('host_created')) or state.get('launch_committed'):
                    return state
            else:
                # Persist launch intent before creating any process.
                save_request_sources(folder,request)
                request['configuration']=configuration_record(request)
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
            (request, state, identity, receipt), state_present = _read_record_snapshot(folder)
        except (OSError,ValueError,UnicodeError) as exc:
            return {'process_id':process_id,'status':RECONCILIATION,
                'error':'Executor record is unreadable; reconcile original launch records: '+str(exc)}
        if not request:
            raise ValueError('Unknown process ID: ' + str(process_id))
        if (request.get('record_version')!=2 or request.get('work_id')!=str(process_id) or
                not request.get('configuration_version') or request.get('configuration')!=configuration_record(request)):
            return {**request,**state,'status':RECONCILIATION,
                'error':'Legacy or changed executor record requires reconciliation of its original configuration and observations'}
        if any(record and not binding_matches(request,record) for record in
                (state if state_present else {},identity,receipt)):
            return {**request,**state,'status':RECONCILIATION,'error':'Executor identity/state/receipt work records disagree'}
        for reference in (request.get('source_references') or {}).values():
            try: valid=reference.get('snapshot_metadata')==file_metadata(reference['snapshot_path'])
            except (OSError,KeyError): valid=False
            if not valid:
                return {**request,**state,'status':RECONCILIATION,
                    'error':'Original source revision is missing or was changed; reconcile its retained work record'}
        if identity and state.get('launch_id')!=identity.get('launch_id'):
            return {**request,**state,'status':RECONCILIATION,'error':'Executor identity does not belong to the committed launch'}
        if identity:
            state.update(identity)
        value = {'process_id': process_id, 'command': request['command'], 'created_at': request['created_at'],
            **record_binding(request), **state}
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
