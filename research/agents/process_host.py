"""Small independent supervisor for actual commands; survives agent exits."""
from __future__ import annotations
import fcntl
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import uuid

# Executed by absolute path so it also works in isolated branch workspaces.
from processes import atomic_json, read_json, configuration_hash, file_hash, workspace_inventory
import psutil

# This supervisor is launched by absolute filename from a task workspace.
# Resolve the shared checkout policy from its installation, not task files.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.execution.repository import task_environment


def execute_child(folder):
    request = read_json(folder / 'request.json')
    state=read_json(folder/'state.json')
    if (not state.get('launch_committed') or state.get('configuration_hash')!=configuration_hash(request)):
        raise RuntimeError('Child launch does not match the committed request')
    for relative,expected_hash in (request.get('source_hashes') or {}).items():
        source=Path(request['workspace'])/relative
        if not source.is_file() or file_hash(source)!=expected_hash:
            raise RuntimeError('Command source/configuration changed before execution: '+relative)
        if source.suffix=='.py':
            # Timestamp/size based bytecode can otherwise execute old helper
            # code after a same-second, same-size method correction. These are
            # derived interpreter caches, not retained scientific artifacts.
            cache=source.parent/'__pycache__'
            if cache.is_dir() and not cache.is_symlink():
                for bytecode in cache.glob(source.stem+'.*.pyc'):
                    if not bytecode.is_symlink(): bytecode.unlink(missing_ok=True)
    os.chdir(request['cwd'])
    # Repository credentials belong to the worker's checkout step. Detached
    # tasks do not inherit its Git helpers, SSH agent or registry location.
    env = task_environment({
        'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''),
        'PYTHONUNBUFFERED': '1', 'MPLBACKEND': 'Agg',
        **{str(k): str(v) for k, v in request['env'].items()}})
    atomic_json(folder / 'identity.json', {'pid': os.getpid(), 'process_created': psutil.Process().create_time(),
        'launch_id':state['launch_id'],'configuration_hash':state['configuration_hash'],
        'resolved_executable': shutil.which(request['command'][0], path=env['PATH']), 'python_environment': sys.executable})
    os.execvpe(request['command'][0], request['command'], env)


def process_group_exists(process_group_id):
    """Return whether a live process remains in the command's owned group.

    ``killpg(pgid, 0)`` also succeeds for groups containing only zombies.
    Those processes cannot write more output and no signal can make them exit,
    so waiting for the group would leave the durable receipt nonterminal.
    """
    process_group_id = int(process_group_id)
    for process in psutil.process_iter():
        try:
            if os.getpgid(process.pid) != process_group_id:
                continue
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
        except (PermissionError, psutil.AccessDenied):
            # We cannot safely conclude that an inaccessible member is gone.
            return True
        try:
            if process.status() != psutil.STATUS_ZOMBIE:
                return True
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
        except psutil.AccessDenied:
            return True
    return False


def supervise(folder):
    with (folder / 'supervisor.lease').open('a') as lease:
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        prior=read_json(folder/'state.json')
        if (folder / 'identity.json').exists() or prior.get('launch_committed') or prior.get('status') in ('completed', 'failed', 'cancelled'):
            return
        request = read_json(folder / 'request.json')
        if request.get('configuration_hash')!=configuration_hash(request):
            raise RuntimeError('Launch request configuration changed')
        # Committing before Popen closes the crash window that otherwise lets
        # another supervisor launch a second command while its identity is late.
        state = {'status': 'starting', 'host_pid': os.getpid(), 'host_created': psutil.Process().create_time(),
            'started_at': time.time(),'launch_committed':True,'launch_id':str(uuid.uuid4()),
            'configuration_hash':request['configuration_hash']}
        atomic_json(folder / 'state.json', state)
        with (folder / 'stdout.log').open('ab') as stdout, (folder / 'stderr.log').open('ab') as stderr:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(folder), '--child'],
                                       cwd=request['cwd'], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
            state.update(status='running', process_group_id=process.pid)
            atomic_json(folder / 'state.json', state)
            cancelled_at = None
            timed_out = False
            # The command owns its process group. A shell or script may exit
            # while a descendant is still writing to the task workspace, so
            # keep this receipt nonterminal until the complete group is gone.
            while process.poll() is None or process_group_exists(process.pid):
                cancel = (folder / 'cancel.json').exists()
                timed_out = bool(request.get('timeout') and time.time() - state['started_at'] >= float(request['timeout']))
                if cancel or timed_out:
                    if cancelled_at is None:
                        cancelled_at = time.monotonic()
                        try:
                            os.killpg(process.pid, signal.SIGCONT)
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    elif time.monotonic() - cancelled_at >= 3:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                time.sleep(.1)
            state.update(status='cancelled' if (folder / 'cancel.json').exists() else ('completed' if process.returncode == 0 else 'failed'),
                         exit_code=process.returncode, finished_at=time.time(), elapsed_seconds=time.time() - state['started_at'])
            if timed_out:
                state.update(status='failed', error='Configured process timeout exceeded')
            artifacts=[]
            workspace=Path(request['workspace'])
            before=request.get('workspace_inventory') or {}
            for relative,metadata in workspace_inventory(workspace).items():
                if before.get(relative)==metadata: continue
                path=workspace/relative
                # Preserve original observations outside the editable working
                # file. A later scientific code/result edit does not erase the
                # evidence that actually belonged to this completed launch.
                snapshot=folder/'artifacts'/relative
                snapshot.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,snapshot)
                with snapshot.open('rb') as handle: os.fsync(handle.fileno())
                artifacts.append({'path':str(snapshot),'original_path':str(path),
                    'relative_path':relative,'sha256':file_hash(snapshot),**metadata})
            for stream in ('stdout','stderr'):
                path=folder/(stream+'.log')
                artifacts.append({'path':str(path),'stream':stream,'sha256':file_hash(path),
                    'size':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns})
            # Publish immutable observations before marking executor terminal.
            atomic_json(folder/'completion.json',{**state,'artifacts':artifacts})
            atomic_json(folder / 'state.json', state)


if __name__ == '__main__':
    target = Path(sys.argv[1]).resolve()
    if '--child' in sys.argv:
        execute_child(target)
    else:
        supervise(target)
