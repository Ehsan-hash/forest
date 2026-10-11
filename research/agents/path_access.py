"""Shared researcher file authorization for FOREST file and command tools."""
from __future__ import annotations

from pathlib import Path
import os
import re

from services.api.common import error


PRIVATE_SPLIT_TOKENS = {'test', 'tests', 'testset', 'testing', 'evaluator', 'private', 'heldout', 'holdout'}


def _contains_private_split(path):
    return any({token for token in re.split(r'[^a-z0-9]+', Path(part).stem.casefold()) if token}
               & PRIVATE_SPLIT_TOKENS for part in Path(path).parts)


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


def authorized_read_path(workspace, value, config=None, *, must_exist=True):
    """Resolve workspace-relative reads or exact authorized external RO mounts."""
    if not isinstance(value, str) or not value.strip():
        error('INVALID_PATH', 'File path must be a nonempty string', 422)
    root = Path(workspace).resolve()
    candidate = Path(value).expanduser()
    authorized_root = None
    if not candidate.is_absolute():
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            error('PATH_ESCAPE', 'Path escapes project workspace', 403)
        protected_relative = resolved.relative_to(root)
    else:
        resolved = candidate.resolve()
        allowed = _paths(config, 'readonly_paths')
        denied = _paths(config, 'denied_readonly_paths')
        if _within(resolved, denied) or not _within(resolved, allowed):
            error('PATH_ESCAPE', 'Absolute file access requires an explicit read-only project authorization', 403)
        authorized_root = next((allowed_root for allowed_root in allowed
                                if _within(resolved, [allowed_root])), None)
        protected_relative = resolved.relative_to(authorized_root)
    if (_contains_private_split(protected_relative)
            or (authorized_root is not None and _contains_private_split(authorized_root.name))):
        error('PATH_ESCAPE', 'Held-out test/evaluator data cannot be read by researcher tools', 403)
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
               for blocked in denied) or _contains_private_split(root.name):
            raise ValueError(f'researcher read-only path overlaps protected test/evaluator data: {root}')
        if root.is_dir():
            for current, directories, files in os.walk(root):
                if any((Path(current) / name).is_symlink() for name in (*directories, *files)):
                    raise ValueError(f'researcher read-only path contains symlinks with ambiguous authorization: {root}')
                if not os.access(current, os.R_OK | os.X_OK) or any(
                        not os.access(Path(current) / name, os.R_OK) for name in files):
                    raise PermissionError(f'researcher read-only tree contains unreadable files: {root}')
                if any(_contains_private_split(name) for name in directories + files):
                    raise ValueError(f'researcher read-only directory contains a protected test/evaluator subtree: {root}')
                directories[:] = list(directories)
    return roots
