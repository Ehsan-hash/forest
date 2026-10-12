import json
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest

from runners.bubblewrap import BubblewrapProcesses


@pytest.mark.skipif(not shutil.which("bwrap") or not Path("/var/tmp").is_dir(),
                    reason="bubblewrap Linux runtime is unavailable")
def test_bubblewrap_hides_private_files_blocks_network_and_limits_writes():
    root = Path(tempfile.mkdtemp(prefix="forest-bwrap-", dir="/var/tmp"))
    workspace = root / "workspace"
    secret_dir = root / "evaluator"
    public_dir = root / "tests"
    workspace.mkdir()
    secret_dir.mkdir()
    public_dir.mkdir()
    secret_file = secret_dir / "hidden-labels.txt"
    secret_file.write_text("private test labels", encoding="utf-8")
    public_file = public_dir / "train.txt"
    public_file.write_text("public train data", encoding="utf-8")

    script = """
import json, socket
from pathlib import Path
secret = Path({secret!r})
public = Path({public!r})
public_write_blocked = False
try:
    (public.parent / 'forbidden-write.txt').write_text('should not be written')
except OSError:
    public_write_blocked = True
network_blocked = socket.socket().connect_ex(('1.1.1.1', 53)) != 0
result = {{'secret_visible': secret.exists(), 'public_text': public.read_text(),
          'public_write_blocked': public_write_blocked, 'network_blocked': network_blocked}}
Path('sandbox-result.json').write_text(json.dumps(result), encoding='utf-8')
""".format(secret=str(secret_file), public=str(public_file))
    manager = BubblewrapProcesses(workspace, {
        "bubblewrap": {"denied_readonly_paths": [str(secret_dir)], "readonly_paths": [str(public_dir)]}
    })
    state = manager.start(["/usr/bin/python3", "-c", script], cwd=".", timeout=20)
    deadline = time.monotonic() + 25
    while state["status"] not in {"completed", "failed", "cancelled", "lost"} and time.monotonic() < deadline:
        time.sleep(0.05)
        state = manager.inspect(state["process_id"])

    assert state["status"] == "completed", manager.read_output(state["process_id"])
    observed = json.loads((workspace / "sandbox-result.json").read_text(encoding="utf-8"))
    assert observed == {
        "secret_visible": False,
        "public_text": "public train data",
        "public_write_blocked": True,
        "network_blocked": True,
    }
    assert not (public_dir / "forbidden-write.txt").exists()


def test_bubblewrap_does_not_mount_the_host_root(tmp_path):
    manager = object.__new__(BubblewrapProcesses)
    manager.executable = '/usr/bin/bwrap'
    manager.workspace = tmp_path.resolve()
    manager.config = {'bubblewrap': {}}
    manager.isolation = {}
    manager.readonly_roots = []
    manager.runtime_roots = []
    wrapped = manager._wrap(['/usr/bin/python3', '-c', 'pass'], manager.workspace, {})
    assert '--tmpfs' in wrapped and wrapped[wrapped.index('--tmpfs') + 1] == '/'
    assert not any(wrapped[i:i + 2] == ['--ro-bind', '/'] for i in range(len(wrapped) - 1))
    assert str(manager.workspace) in wrapped


def test_project_configuration_cannot_expand_runtime_mounts_or_replace_bubblewrap(tmp_path):
    manager = object.__new__(BubblewrapProcesses)
    manager.executable = '/usr/bin/bwrap'
    manager.workspace = tmp_path.resolve()
    secret = (tmp_path.parent / 'untrusted-runtime')
    manager.config = {'bubblewrap': {'runtime_paths': [str(secret)], 'executable': str(secret / 'bwrap'),
                                     'python_bin_dir': str(secret)}}
    manager.isolation = manager.config['bubblewrap']
    manager.readonly_roots = []
    manager.runtime_roots = []
    wrapped = manager._wrap(['/usr/bin/python3', '-c', 'pass'], manager.workspace, {})
    assert str(secret) not in wrapped
    assert wrapped[0] == manager.executable


def test_command_preflight_rejects_explicit_denied_subtree_in_writable_workspace(tmp_path):
    manager=object.__new__(BubblewrapProcesses)
    manager.workspace=tmp_path.resolve()
    forbidden=tmp_path/'private'/'authorized-by-name-is-insufficient.txt'
    manager.config={'denied_readonly_paths':[str(forbidden)]}
    with pytest.raises(PermissionError,match='explicitly denied'):
        manager._wrap(['/usr/bin/python3','-c','pass'],manager.workspace,{})


def test_command_preflight_does_not_expose_denied_runtime_mounted_files(tmp_path):
    manager=object.__new__(BubblewrapProcesses)
    manager.workspace=tmp_path.resolve()
    runtime=tmp_path.parent/'runtime-records'
    manager.runtime_roots=[runtime]
    manager.config={'denied_readonly_paths':[str(runtime/'protected-evaluation.csv')]}
    with pytest.raises(PermissionError,match='explicitly denied'):
        manager._wrap(['/usr/bin/python3','-c','pass'],manager.workspace,{})
