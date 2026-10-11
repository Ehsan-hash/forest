"""Bubblewrap isolation for researcher-owned local command processes.

The child sees a minimal runtime filesystem, its writable workspace, and exact
explicit read-only mounts. The host root and unrelated home/data paths are not
mounted; network access and inherited credentials are unavailable.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from research.agents.processes import ManagedProcesses

_SAFE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DENIED_ENV_NAMES = {
    "HOME", "PATH", "CODEX_HOME", "OPENAI_API_KEY", "FOREST_OWNER_TOKEN",
    "FOREST_OWNER_TOKEN_FILE", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH",
}


def _credential_name(name: str) -> bool:
    upper = name.upper()
    return upper in _DENIED_ENV_NAMES or upper.endswith(("_TOKEN", "_API_KEY", "_PASSWORD", "_SECRET"))


class BubblewrapProcesses(ManagedProcesses):
    """ManagedProcesses whose task child sees only explicit project/data mounts."""

    def __init__(self, workspace, config=None):
        workspace=Path(workspace).resolve()
        super().__init__(workspace,directory=workspace.parent/'.forest-processes'/workspace.name,
            legacy_directory=workspace/'.forest-processes',code_inputs=(config or {}).get('code_inputs'))
        self.config = dict(config or {})
        isolation = self.config.get("bubblewrap", {})
        if not isinstance(isolation, dict):
            raise ValueError("bubblewrap configuration must be an object")
        self.isolation = isolation
        from research.agents.path_access import authorized_readonly_roots
        self.readonly_roots = authorized_readonly_roots(self.config)
        # Runtime dependency mounts are derived from the trusted worker
        # interpreter, never supplied by project/run configuration.
        self.runtime_roots = sorted({Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}, key=str)
        if any(not path.exists() for path in self.runtime_roots):
            raise ValueError('A worker Python runtime dependency path is missing')
        executable = '/usr/bin/bwrap' if Path('/usr/bin/bwrap').is_file() else 'bwrap'
        self.executable = shutil.which(executable)
        if not self.executable or not Path(self.executable).is_file():
            raise RuntimeError("bubblewrap execution was requested but /usr/bin/bwrap is unavailable")

    def _wrap(self, command, cwd, env):
        if isinstance(command, str):
            command = ["/bin/sh", "-c", command]
        if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
            raise ValueError("command must be a nonempty argv list or shell command string")
        if Path(command[0]).is_absolute() and Path(command[0]).exists():
            command = [str(Path(command[0]).resolve()), *command[1:]]

        workspace = str(self.workspace)
        args = [
            self.executable,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--disable-userns",
            "--tmpfs", "/",
            "--dir", "/tmp",
            "--dir", "/mnt",
            "--dev", "/dev",
            "--proc", "/proc",
        ]

        # Supply executable/runtime libraries without exposing /home, /data,
        # logs, credentials, host caches, or other arbitrary root content.
        for source in ("/usr", "/lib", "/lib64", "/bin", "/sbin"):
            if Path(source).exists():
                args.extend(["--ro-bind", source, source])
        for source in ("/etc/ssl/certs", "/etc/alternatives"):
            if Path(source).exists():
                parent = str(Path(source).parent)
                if parent not in ("/usr", "/lib", "/lib64", "/bin", "/sbin"):
                    args.extend(["--dir", parent])
                args.extend(["--ro-bind", source, source])

        def ensure_parents(target):
            parents = list(Path(target).parents)
            for parent in reversed(parents[:-1]):
                if str(parent) != "/":
                    args.extend(["--dir", str(parent)])

        ensure_parents(workspace)
        args.extend(["--bind", workspace, workspace])

        # The project may expose prepared train/validation inputs. These mounts
        # are explicit and read-only; hidden evaluator files are never mounted.
        for path in [*self.runtime_roots, *self.readonly_roots]:
            ensure_parents(path)
            args.extend(["--ro-bind", str(path), str(path)])

        home = Path.home()
        # Root is a tmpfs, so no host home or runtime socket needs a masking
        # mount. Keep the legacy hidden_paths setting accepted as a denylist.

        # WSL CUDA exposes its NVIDIA device through /dev/dxg.
        for device in ("/dev/dxg", "/dev/nvidiactl", "/dev/nvidia0", "/dev/nvidia-uvm"):
            path = Path(device)
            if path.exists():
                args.extend(["--dev-bind", str(path), str(path)])

        child_env = {}
        for key, value in (env or {}).items():
            if _SAFE_ENV_NAME.fullmatch(str(key)) and not _credential_name(str(key)):
                child_env[str(key)] = str(value)
        python_dir = str(Path(sys.executable).resolve().parent)
        search_path = os.pathsep.join(item for item in (python_dir, "/usr/local/bin", "/usr/bin", "/bin") if item)
        defaults = {
            "HOME": "/tmp",
            "PATH": search_path,
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "MPLBACKEND": "Agg",
            "TMPDIR": "/tmp",
            "CUDA_VISIBLE_DEVICES": "0",
        }
        child_env = {**child_env, **defaults}
        args.extend(["--chdir", str(cwd), "--clearenv"])
        for key, value in sorted(child_env.items()):
            args.extend(["--setenv", key, value])
        args.extend(["--", *command])
        return args

    def start(self, command, *, process_id=None, cwd=".", env=None, timeout=None):
        requested_cwd = (self.workspace / cwd).resolve()
        if not requested_cwd.is_relative_to(self.workspace) or not requested_cwd.is_dir():
            raise ValueError("Process working directory must exist inside the workspace")
        wrapped = self._wrap(command, requested_cwd, env)
        # ManagedProcesses persists the wrapped launch intent, supervises its
        # process group, and retains the ordinary recovery/cancellation receipt.
        return super().start(wrapped, process_id=process_id, cwd=cwd, env=None, timeout=timeout)
