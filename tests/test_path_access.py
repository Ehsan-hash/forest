from pathlib import Path

import pytest
from fastapi import HTTPException

from research.agents.path_access import authorized_read_path, authorized_readonly_roots


def test_absolute_read_is_allowed_only_under_explicit_readonly_root(tmp_path):
    workspace = tmp_path / 'workspace'
    data = tmp_path / 'data' / 'train'
    workspace.mkdir()
    data.mkdir(parents=True)
    source = data / 'protocol.json'
    source.write_text('{}')
    assert authorized_read_path(workspace, str(source), {'readonly_paths': [str(data)]}) == source.resolve()
    with pytest.raises(HTTPException) as caught:
        authorized_read_path(workspace, str(source), {})
    assert caught.value.status_code == 403


def test_explicitly_denied_data_cannot_be_read_even_if_parent_is_authorized(tmp_path):
    workspace = tmp_path / 'workspace'
    data = tmp_path / 'data'
    heldout = data / 'test' / 'labels.csv'
    heldout.parent.mkdir(parents=True)
    workspace.mkdir()
    heldout.write_text('secret')
    with pytest.raises(HTTPException) as caught:
        authorized_read_path(workspace, str(heldout), {'readonly_paths': [str(data)], 'denied_readonly_paths': [str(heldout.parent)]})
    assert caught.value.status_code == 403
    with pytest.raises(ValueError, match='explicitly denied'):
        authorized_readonly_roots({'readonly_paths': [str(data)], 'denied_readonly_paths': [str(heldout.parent)]})


def test_file_names_do_not_override_explicit_owner_authorization(tmp_path):
    workspace = tmp_path / 'workspace'
    train = tmp_path / 'dataset' / 'train'
    workspace.mkdir()
    train.mkdir(parents=True)
    hidden = train / 'labels_test.csv'
    hidden.write_text('held out labels')
    config = {'readonly_paths': [str(train)]}
    assert authorized_read_path(workspace, str(hidden), config) == hidden.resolve()
    assert authorized_readonly_roots(config) == [train.resolve()]


def test_authorized_mounts_reject_symlinks_and_missing_paths(tmp_path):
    data = tmp_path / 'train'
    data.mkdir()
    (data / 'alias').symlink_to(tmp_path / 'outside', target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        authorized_readonly_roots({'readonly_paths': [str(data)]})
    with pytest.raises(ValueError, match='missing'):
        authorized_readonly_roots({'readonly_paths': [str(tmp_path / 'absent')]})
    with pytest.raises(ValueError, match='absolute'):
        authorized_readonly_roots({'readonly_paths': ['relative/train']})


def test_readonly_mount_cannot_cover_an_explicit_denied_subtree(tmp_path):
    data = tmp_path / 'data'
    train = data / 'train'
    test = data / 'test'
    train.mkdir(parents=True)
    test.mkdir()
    (train / 'features.csv').write_text('x')
    (test / 'labels.csv').write_text('hidden')
    with pytest.raises(ValueError, match='explicitly denied'):
        authorized_readonly_roots({'readonly_paths': [str(data)], 'denied_readonly_paths': [str(test)]})


def test_explicit_workspace_denial_applies_to_relative_and_absolute_reads(tmp_path):
    source = tmp_path / 'restricted.csv'; source.write_text('private by authorization')
    config = {'denied_readonly_paths': [str(source)]}
    for name in ('restricted.csv', str(source)):
        with pytest.raises(HTTPException) as caught:
            authorized_read_path(tmp_path, name, config)
        assert caught.value.status_code == 403
