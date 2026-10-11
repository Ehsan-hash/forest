"""Versioned original file records, using references and direct byte comparison."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import ast
import shutil
import uuid

from .processes import atomic_json, read_json


def file_identity(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def equal_files(left, right):
    """Compare actual files; never infer equality from equal sizes or timestamps."""
    left, right = Path(left), Path(right)
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open('rb') as a, right.open('rb') as b:
            while True:
                first, second = a.read(1024 * 1024), b.read(1024 * 1024)
                if first != second:
                    return False
                if not first:
                    return True
    except OSError:
        return False


def record_file(path, root, *, relative_path=None):
    """Retain a real original only when it changes, with a readable revision."""
    path, root = Path(path).resolve(), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / 'index.json'
    index = read_json(index_path, {})
    prior = index.get(str(path))
    identity = file_identity(path)
    if prior and Path(prior['snapshot_path']).is_file():
        if prior.get('file_identity') == identity:
            return deepcopy(prior)
        if equal_files(path, prior['snapshot_path']):
            prior['file_identity'] = identity
            atomic_json(index_path, index)
            return deepcopy(prior)
    ident = str(uuid.uuid4())
    destination = root / (ident + '.original')
    temporary = root / (ident + '.tmp')
    shutil.copyfile(path, temporary)
    # The original record is published before its reference. A concurrent
    # source edit cannot silently assign a different file to the same record.
    if identity != file_identity(path) or not equal_files(path, temporary):
        temporary.unlink(missing_ok=True)
        raise OSError('File changed while recording its original: ' + str(path))
    temporary.replace(destination)
    record = {'artifact_id': ident, 'revision': (prior or {}).get('revision', 0) + 1,
              'path': str(path), 'snapshot_path': str(destination),
              'relative_path': relative_path, 'file_identity': identity,
              'size_bytes': identity[2]}
    if path.suffix == '.py':
        try:
            record['semantic_source'] = ast.dump(ast.parse(destination.read_text()), include_attributes=False)
        except (SyntaxError, UnicodeError):
            record['semantic_source'] = destination.read_text(errors='replace')
    index[str(path)] = record
    atomic_json(index_path, index)
    return deepcopy(record)


def matches_record(path, record):
    original = record.get('snapshot_path') or record.get('path')
    return bool(original and equal_files(path, original))
