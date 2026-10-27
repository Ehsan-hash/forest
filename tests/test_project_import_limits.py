"""Real project import checks for the bounded ZIP member preflight."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from pydantic import ValidationError
from sqlalchemy import select


def _archive_entries(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def _archive_with_entries(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def _project_ids():
    from services.api.db import Project, Session

    with Session() as session:
        return set(session.scalars(select(Project.id)))


@pytest.fixture
def api():
    from fastapi.testclient import TestClient
    from services.api.db import migrate
    from services.api.main import app

    migrate()
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def archive_member_cap():
    from services.api.config import settings

    original = settings.max_project_archive_members
    try:
        yield settings
    finally:
        settings.max_project_archive_members = original


def test_archive_member_setting_defaults_from_env_and_requires_positive_values(monkeypatch):
    from services.api.config import Settings

    monkeypatch.delenv('FOREST_MAX_PROJECT_ARCHIVE_MEMBERS', raising=False)
    assert Settings(_env_file=None).max_project_archive_members == 10000
    monkeypatch.setenv('FOREST_MAX_PROJECT_ARCHIVE_MEMBERS', '17')
    assert Settings(_env_file=None).max_project_archive_members == 17
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_project_archive_members=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_project_archive_members=-1)


def test_at_limit_import_restores_exact_file_bytes(api, archive_member_cap):
    project = api.post('/api/projects', json={'name': 'Archive member limit source'}).json()
    original = b'round-trip bytes\x00\xff\n'
    uploaded = api.post(
        f"/api/projects/{project['id']}/upload",
        files={'file': ('roundtrip.bin', original, 'application/octet-stream')},
    )
    assert uploaded.status_code == 200, uploaded.text
    exported = api.post(f"/api/projects/{project['id']}/export", json={})
    assert exported.status_code == 200, exported.text
    entries = _archive_entries(exported.content)
    archive_member_cap.max_project_archive_members = len(entries)

    imported = api.post(
        '/api/projects/import',
        files={'file': ('at-limit.zip', exported.content, 'application/zip')},
    )

    assert imported.status_code == 200, imported.text
    restored = api.get(f"/api/projects/{imported.json()['id']}/download", params={'path': 'uploads/roundtrip.bin'})
    assert restored.status_code == 200, restored.text
    assert restored.content == original


@pytest.mark.parametrize('extras', [
    [('files/empty-a.bin', b''), ('files/empty-b.bin', b'')],
    [('files/empty-directory/', b''), ('metadata/', b'')],
    [('outside-metadata.json', b'{}'), ('files/empty.bin', b'')],
])
def test_over_limit_counts_empty_directories_and_outside_entries_before_project_creation(
    api, archive_member_cap, extras
):
    project = api.post('/api/projects', json={'name': 'Archive member count source'}).json()
    exported = api.post(f"/api/projects/{project['id']}/export", json={})
    assert exported.status_code == 200, exported.text
    entries = _archive_entries(exported.content)
    archive_member_cap.max_project_archive_members = len(entries)
    before_ids = _project_ids()
    from services.api.config import settings

    projects_root = settings.data_dir / 'projects'
    before_dirs = {path.name for path in projects_root.iterdir() if path.is_dir()}

    rejected = api.post(
        '/api/projects/import',
        files={'file': ('over-limit.zip', _archive_with_entries(entries + extras), 'application/zip')},
    )

    assert rejected.status_code == 413, rejected.text
    assert rejected.json()['detail']['code'] == 'ARCHIVE_TOO_LARGE'
    assert _project_ids() == before_ids
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_dirs


def test_member_limit_counts_manifest_metadata_before_manifest_read_and_malformed_zip_is_unchanged(
    api, archive_member_cap
):
    project = api.post('/api/projects', json={'name': 'Archive metadata count source'}).json()
    exported = api.post(f"/api/projects/{project['id']}/export", json={})
    assert exported.status_code == 200, exported.text
    entries = _archive_entries(exported.content)
    archive_member_cap.max_project_archive_members = len(entries)
    before_ids = _project_ids()
    from services.api.config import settings

    projects_root = settings.data_dir / 'projects'
    before_dirs = {path.name for path in projects_root.iterdir() if path.is_dir()}

    metadata_entry = _archive_with_entries(entries + [('metadata.json', json.dumps({'ignored': True}).encode())])
    rejected = api.post('/api/projects/import', files={'file': ('metadata.zip', metadata_entry, 'application/zip')})
    assert rejected.status_code == 413, rejected.text
    assert rejected.json()['detail']['code'] == 'ARCHIVE_TOO_LARGE'
    assert _project_ids() == before_ids
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_dirs

    malformed_entries = [
        (name, b'{ malformed manifest' if name == 'forest-project.json' else content)
        for name, content in entries
    ]
    malformed_entries.append(('metadata.json', b'ignored'))
    over_limit_malformed = _archive_with_entries(malformed_entries)
    rejected_malformed = api.post(
        '/api/projects/import', files={'file': ('over-limit-malformed.zip', over_limit_malformed, 'application/zip')}
    )
    assert rejected_malformed.status_code == 413, rejected_malformed.text
    assert rejected_malformed.json()['detail']['code'] == 'ARCHIVE_TOO_LARGE'
    assert _project_ids() == before_ids
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_dirs

    malformed = api.post('/api/projects/import', files={'file': ('broken.zip', b'not a zip', 'application/zip')})
    assert malformed.status_code == 400, malformed.text
    assert malformed.json()['detail']['code'] == 'INVALID_ARCHIVE'
    assert _project_ids() == before_ids


@pytest.mark.parametrize('collision', [
    [('files/archive.txt', b'FIRST'), ('files/archive.txt', b'SECOND')],
    [('files/alias/./result.txt', b'FIRST'), ('files/alias/result.txt', b'SECOND')],
    [('files/caf\u00e9.txt', b'FIRST'), ('files/cafe\u0301.txt', b'SECOND')],
])
def test_destination_collisions_are_rejected_before_project_creation(api, collision):
    source = api.post('/api/projects', json={'name': 'Archive destination collision source'})
    assert source.status_code == 200, source.text
    exported = api.post(f"/api/projects/{source.json()['id']}/export", json={})
    assert exported.status_code == 200, exported.text
    entries = [
        (name, content)
        for name, content in _archive_entries(exported.content)
        if not name.startswith('files/')
    ]
    before_ids = _project_ids()
    from services.api.config import settings

    projects_root = settings.data_dir / 'projects'
    before_dirs = {path.name for path in projects_root.iterdir() if path.is_dir()}
    rejected = api.post(
        '/api/projects/import',
        files={'file': ('destination-collision.zip', _archive_with_entries(entries + collision), 'application/zip')},
    )

    assert rejected.status_code == 400, rejected.text
    assert rejected.json()['detail']['code'] == 'INVALID_ARCHIVE'
    assert _project_ids() == before_ids
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_dirs
