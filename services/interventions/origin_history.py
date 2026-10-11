"""Portable copy lineage retains original permission origins across projects."""
from copy import deepcopy
from pathlib import Path, PurePosixPath, PureWindowsPath
import uuid

from research.agents.path_access import copy_origin_catalog
from research.agents.processes import atomic_json


def validate_origins(records, source_root=None):
    if not isinstance(records, list):
        raise ValueError('Workspace permission origins must be an array')
    if source_root is not None and (not isinstance(source_root, str) or not (
            PurePosixPath(source_root).is_absolute() or PureWindowsPath(source_root).is_absolute())):
        raise ValueError('Workspace source root must be an explicit absolute path')
    def relative(value):
        if (not isinstance(value, str) or not value or '\\' in value or
                PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or
                '..' in PurePosixPath(value).parts):
            raise ValueError('Workspace origin paths must be project-relative')
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('files'), dict):
            raise ValueError('Workspace permission origin record must contain files')
        relative(record.get('workspace'))
        aliases = record.get('permission_aliases', {})
        if not isinstance(aliases, dict):
            raise ValueError('Workspace permission aliases must be an object')
        for filename, origins in [*record['files'].items(), *aliases.items()]:
            relative(filename)
            if (not isinstance(origins, list) or not origins or
                    any(not isinstance(value, str) or not (
                        PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()) for value in origins)):
                raise ValueError('Original permission origins must be explicit absolute paths')
    return records


def export_origins(root):
    return validate_origins(copy_origin_catalog(root))


def relocate_permission_config(original, translated, root, source_root, remap):
    """Relocate explicit in-project ACLs, leaving external authorization exact."""
    if not isinstance(original, dict) or not isinstance(translated, dict):
        return translated
    value = deepcopy(translated)
    source = (PureWindowsPath(source_root) if PureWindowsPath(source_root).drive else PurePosixPath(source_root)) if source_root else None
    for key in ('readonly_paths', 'denied_readonly_paths'):
        if key not in original:
            continue
        if not isinstance(original[key], list):
            value[key] = deepcopy(original[key])
            continue
        paths = []
        for text in original[key]:
            if not isinstance(text, str):
                paths.append(text)
                continue
            actual = type(source)(text) if source is not None else None
            relocated = (str(Path(root).resolve() / remap(actual.relative_to(source).as_posix()))
                         if actual is not None and actual.is_relative_to(source) else text)
            if key == 'denied_readonly_paths':
                paths.append(text)
            paths.append(relocated)
        value[key] = list(dict.fromkeys(paths)) if all(isinstance(text,str) for text in paths) else paths
    for key in ('bubblewrap', 'agent_execution'):
        if key in original:
            value[key] = relocate_permission_config(original[key], value.get(key), root, source_root, remap)
    return value


def import_origins(root, records, remap, source_root=None):
    for record in validate_origins(records, source_root):
        # Relocate copied workspace files, never relabel their original source.
        value = deepcopy(record)
        value['workspace'] = remap(record['workspace'])
        value['files'] = {remap(filename): deepcopy(origins) for filename, origins in record['files'].items()}
        aliases = {remap(filename): deepcopy(origins) for filename, origins in record.get('permission_aliases', {}).items()}
        if source_root is not None:
            source = PureWindowsPath(source_root) if PureWindowsPath(source_root).drive else PurePosixPath(source_root)
            if not source.is_absolute():
                raise ValueError('Workspace source root must be an explicit absolute path')
            for filename, origins in record['files'].items():
                for origin in [*origins, *record.get('permission_aliases', {}).get(filename, [])]:
                    original = type(source)(origin)
                    if original.is_relative_to(source):
                        alias = str(Path(root).resolve() / remap(original.relative_to(source).as_posix()))
                        aliases.setdefault(remap(filename), []).append(alias)
        if aliases:
            value['permission_aliases'] = {filename: list(dict.fromkeys(origins)) for filename, origins in aliases.items()}
        validate_origins([value])
        atomic_json(Path(root) / '.forest-interventions' / 'origins' / (str(uuid.uuid4()) + '.json'), value)
