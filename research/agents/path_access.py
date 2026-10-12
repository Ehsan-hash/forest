"""Shared researcher file authorization for FOREST file and command tools."""
from __future__ import annotations

from pathlib import Path
import os

from services.api.common import error


def _paths(config, key):
    config = config or {}
    raw = config.get(key)
    if raw is None and isinstance(config.get('bubblewrap'), dict):
        raw = config['bubblewrap'].get(key)
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(value, str) or not value.strip() for value in raw):
        raise ValueError(f'{key} must be a list of explicit absolute paths')
    if any(not Path(value).expanduser().is_absolute() for value in raw):
        raise ValueError(f'{key} entries must be explicit absolute paths')
    return [Path(value).expanduser().resolve() for value in raw]


def _within(path, roots):
    return any(path == root or path.is_relative_to(root) for root in roots)


def denied_origin(path, config=None, origins=()):
    """Explicit ACLs apply to a file and its retained copy origins."""
    denied = _paths(config, 'denied_readonly_paths')
    return next((candidate for value in (path, *origins)
                 if _within(candidate := Path(value).expanduser().resolve(), denied)), None)


def copy_origin_catalog(root):
    """Load trusted lineage records outside writable research workspaces."""
    import json
    records = []
    for path in (Path(root) / '.forest-interventions' / 'origins').glob('*.json'):
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('files'), dict):
            raise ValueError('Workspace copy origin record requires reconciliation')
        records.append(value)
    return records


def copy_origins(root, path, catalog=None):
    """Direct path lineage is permission metadata, never content identity."""
    import json
    root, path = Path(root).resolve(), Path(path).resolve()
    values = [str(path)]
    for record in copy_origin_catalog(root) if catalog is None else catalog:
        source = (root / record['workspace']).resolve()
        if path.is_relative_to(source):
            values.extend(record['files'].get(path.relative_to(source).as_posix(), []))
            values.extend(record.get('permission_aliases',{}).get(path.relative_to(source).as_posix(),[]))
    for parent in path.parents:
        if not parent.is_relative_to(root):
            break
        if parent.name != 'workspace':
            continue
        marker = parent.parent / 'workspace_preparation.json'
        if marker.is_file():
            value = json.loads(marker.read_text())
            values.extend(value.get('source_origins', {}).get(path.relative_to(parent).as_posix(), []))
        break
    return list(dict.fromkeys(values))


def authorized_read_path(workspace, value, config=None, *, must_exist=True):
    """Resolve workspace-relative reads or exact authorized external RO mounts."""
    if not isinstance(value, str) or not value.strip():
        error('INVALID_PATH', 'File path must be a nonempty string', 422)
    root = Path(workspace).resolve()
    candidate = Path(value).expanduser()
    authorized_root = None
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    denied = _paths(config, 'denied_readonly_paths')
    if denied_origin(resolved, config, copy_origins(root, resolved, catalog=[])):
        error('PATH_ESCAPE', 'File access is explicitly denied by the project authorization', 403)
    if not candidate.is_absolute() or resolved.is_relative_to(root):
        if not resolved.is_relative_to(root):
            error('PATH_ESCAPE', 'Path escapes project workspace', 403)
        protected_relative = resolved.relative_to(root)
    else:
        allowed = _paths(config, 'readonly_paths')
        if _within(resolved, denied) or not _within(resolved, allowed):
            error('PATH_ESCAPE', 'Absolute file access requires an explicit read-only project authorization', 403)
        authorized_root = next((allowed_root for allowed_root in allowed
                                if _within(resolved, [allowed_root])), None)
        protected_relative = resolved.relative_to(authorized_root)
    if must_exist and not resolved.exists():
        error('MISSING_ARTIFACT', f'Missing project file: {value}', 404)
    if not resolved.is_file():
        error('INVALID_PATH', 'Read path must identify a file', 422)
    return resolved


def authorized_readonly_roots(config=None):
    roots = _paths(config, 'readonly_paths')
    denied = _paths(config, 'denied_readonly_paths')
    for root in roots:
        if not root.exists():
            raise ValueError(f'configured researcher read-only path is missing: {root}')
        if not os.access(root, os.R_OK):
            raise PermissionError(f'configured researcher read-only path is not readable: {root}')
        if any(root == blocked or root.is_relative_to(blocked) or blocked.is_relative_to(root)
               for blocked in denied):
            raise ValueError(f'researcher read-only path overlaps an explicitly denied path: {root}')
        if root.is_dir():
            for current, directories, files in os.walk(root):
                if any((Path(current) / name).is_symlink() and not _within((Path(current) / name).resolve(), roots)
                       for name in (*directories, *files)):
                    raise ValueError(f'researcher read-only path contains a symlink outside the authorized roots: {root}')
                if not os.access(current, os.R_OK | os.X_OK) or any(
                        not os.access(Path(current) / name, os.R_OK) for name in files):
                    raise PermissionError(f'researcher read-only tree contains unreadable files: {root}')
                directories[:] = list(directories)
    return roots
