"""Exercise both real source-packaging CLIs against isolated temporary trees."""
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts import package_source
from scripts.release import create_source
from scripts.package_source import source_tree as export_source_tree


def test_source_release_preserves_project_skill_discovery(tmp_path):
    from scripts.package_source import package
    source = tmp_path / 'source'
    skill = source / '.agents' / 'skills' / 'forest-academic-writing' / 'SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: forest-academic-writing\ndescription: Own writing skill.\n---\n')
    archive = tmp_path / 'release.zip'
    package(archive, source)
    with zipfile.ZipFile(archive) as output:
        assert output.read('forest/.agents/skills/forest-academic-writing/SKILL.md') == skill.read_bytes()
    directory = tmp_path / 'export'
    export_source_tree(directory, source)
    assert (directory / skill.relative_to(source)).read_bytes() == skill.read_bytes()


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / 'source'
    (root / 'scripts').mkdir(parents=True)
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    for name in ('package_source.py', 'release.py'):
        shutil.copyfile(scripts / name, root / 'scripts' / name)
    (root / 'README.md').write_text('Public source\n')
    (root / 'docs').mkdir()
    return root


def run_packager(entrypoint, root, destination):
    arguments = ['--output', str(destination)] if entrypoint == 'package_source.py' else [str(destination)]
    return subprocess.run(
        [sys.executable, str(root / 'scripts' / entrypoint), *arguments],
        cwd=root.parent, capture_output=True, text=True,
    )


@pytest.mark.parametrize('entrypoint', ['package_source.py', 'release.py'])
def test_packaging_clis_reject_detected_secret_before_writing_archive(source_tree, tmp_path, entrypoint):
    (source_tree / 'docs' / 'example.md').write_text('Token: ' + 'sk-' + 'x' * 32)
    destination = tmp_path / 'source.zip'
    result = run_packager(entrypoint, source_tree, destination)
    assert result.returncode != 0
    assert 'Possible secret in eligible source file: docs/example.md' in result.stderr
    assert not destination.exists()


@pytest.mark.parametrize('entrypoint', ['package_source.py', 'release.py'])
def test_packaging_clis_share_public_allowlist_and_exclude_private_files(source_tree, tmp_path, entrypoint):
    private_files = [
        '.env', '.env.local', 'secrets.json', 'docs/.env', 'docs/.env.production',
        'docs/credentials.yaml', 'docs/owner-token.txt', 'docs/private.key',
        'docs/secrets/notes.md', 'docs/output/result.json', 'var/projects/run.py',
        'docs/arbitrary.bin',
        'docs/api-key.txt', 'docs/tokens.json', 'docs/id_ecdsa',
        'docs/tmp/local-notes.md', 'docs/.vscode/settings.json',
        'docs/.cache/state.json', 'docs/forest.egg-info/PKG-INFO.txt',
        'docs/._guide.md', 'tmp/notes.md',
        'docs/ACCEPTANCE.md', 'docs/QUALIFICATION_REPORT.md',
        'docs/RESEARCH_FINDINGS.md', 'docs/WRITING_REVIEW.md',
        'docs/FEATURES.md', 'docs/RUNTIME_UPGRADE.md',
        'docs/PIPELINE_LIVE_VALIDATION.md',
        'docs/research_tasks/notes.md',
        'docs/research_tasks/example/nested/observations.json',
    ]
    for name in private_files:
        path = source_tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Private value: ' + 'sk-' + 'x' * 32)
    (source_tree / '.env.example').write_text('EXAMPLE_SETTING=\n')
    (source_tree / 'docs' / 'public.md').write_text('Public guide\n')
    destination = tmp_path / 'source.zip'
    result = run_packager(entrypoint, source_tree, destination)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            'forest/README.md', 'forest/.env.example', 'forest/docs/public.md',
            'forest/scripts/package_source.py', 'forest/scripts/release.py',
        }
        assert archive.read('forest/docs/public.md') == b'Public guide\n'
        assert archive.testzip() is None


def test_source_archive_includes_cli_and_excludes_directory_binding(source_tree, tmp_path):
    cli = source_tree / 'forest_cli'
    cli.mkdir()
    (cli / 'main.py').write_text('def main(): return 0\n')
    binding = cli / '.forest'
    binding.mkdir()
    (binding / 'project.json').write_text('{"project_id": "private-local-binding"}')
    destination = tmp_path / 'cli-source.zip'
    result = run_packager('package_source.py', source_tree, destination)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        assert 'forest/forest_cli/main.py' in archive.namelist()
        assert not any('.forest/' in name for name in archive.namelist())


def test_packaging_cli_uses_default_output_directory(source_tree):
    result = subprocess.run(
        [sys.executable, str(source_tree / 'scripts' / 'package_source.py')],
        cwd=source_tree.parent, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    destination = source_tree / 'output' / 'forest-source.zip'
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert 'forest/README.md' in archive.namelist()


@pytest.mark.parametrize('relative_source', ['README.md', 'scripts/package_source.py'])
def test_packaging_rejects_direct_source_destination_without_changing_source(source_tree, relative_source):
    source = source_tree / relative_source
    before = source.read_bytes()
    with pytest.raises(ValueError, match='eligible source file'):
        package_source.package(source, source_tree)
    assert source.read_bytes() == before


def test_packaging_cli_rejects_source_destination_without_changing_source(source_tree):
    destination = source_tree / 'README.md'
    before = destination.read_bytes()
    result = run_packager('package_source.py', source_tree, destination)
    assert result.returncode != 0
    assert destination.read_bytes() == before


@pytest.mark.parametrize('alias_kind', ['symlink', 'hardlink'])
def test_packaging_rejects_source_alias_without_changing_source(source_tree, tmp_path, alias_kind):
    source = source_tree / 'README.md'
    alias = tmp_path / 'source.zip'
    if alias_kind == 'symlink':
        alias.symlink_to(source)
    else:
        alias.hardlink_to(source)
    before = source.read_bytes()
    with pytest.raises(ValueError, match='eligible source file'):
        package_source.package(alias, source_tree)
    assert source.read_bytes() == before
    assert alias.read_bytes() == before


def test_packaging_replaces_existing_archive_after_success(source_tree, tmp_path):
    destination = tmp_path / 'source.zip'
    destination.write_bytes(b'previous delivery')
    expected = list(package_source.source_files(source_tree))
    count = package_source.package(destination, source_tree)
    assert count == len(expected)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            'forest/' + path.relative_to(source_tree).as_posix() for path in expected
        }


@pytest.mark.parametrize('preexisting', [False, True], ids=['absent', 'existing'])
def test_packaging_write_failure_preserves_destination_and_source(
    source_tree, tmp_path, monkeypatch, preexisting,
):
    destination = tmp_path / 'source.zip'
    if preexisting:
        destination.write_bytes(b'previous complete archive')
    before_destination = destination.read_bytes() if preexisting else None
    before_siblings = {path.name for path in destination.parent.iterdir()}
    before_sources = {
        path.relative_to(source_tree): path.read_bytes()
        for path in package_source.source_files(source_tree)
    }

    original_write = package_source.zipfile.ZipFile.write
    writes = 0

    def fail_write(self, *args, **kwargs):
        nonlocal writes
        writes += 1
        if writes > 1:
            raise OSError('injected write failure')
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(package_source.zipfile.ZipFile, 'write', fail_write)
    with pytest.raises(OSError, match='injected write failure'):
        package_source.package(destination, source_tree)

    assert writes > 1
    assert {path.name for path in destination.parent.iterdir()} == before_siblings
    assert (destination.read_bytes() if preexisting else None) == before_destination
    assert destination.exists() is preexisting
    assert {
        path.relative_to(source_tree): path.read_bytes()
        for path in package_source.source_files(source_tree)
    } == before_sources


@pytest.mark.parametrize('preexisting', [False, True], ids=['absent', 'existing'])
def test_packaging_replace_failure_cleans_temporary_archive_and_preserves_destination(
    source_tree, tmp_path, monkeypatch, preexisting,
):
    destination = tmp_path / 'source.zip'
    if preexisting:
        destination.write_bytes(b'previous complete archive')
    before_destination = destination.read_bytes() if preexisting else None
    before_siblings = {path.name for path in destination.parent.iterdir()}
    before_sources = {
        path.relative_to(source_tree): path.read_bytes()
        for path in package_source.source_files(source_tree)
    }

    def fail_replace(*args, **kwargs):
        raise OSError('injected replace failure')

    monkeypatch.setattr(package_source.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected replace failure'):
        package_source.package(destination, source_tree)

    assert {path.name for path in destination.parent.iterdir()} == before_siblings
    assert (destination.read_bytes() if preexisting else None) == before_destination
    assert destination.exists() is preexisting
    assert {
        path.relative_to(source_tree): path.read_bytes()
        for path in package_source.source_files(source_tree)
    } == before_sources


@pytest.mark.parametrize('token', [
    'github_pat_' + 'x' * 50,
    'glpat-' + 'x' * 25,
    'xoxb-' + '1' * 30,
    'ASIA' + 'A' * 16,
    '-----BEGIN ' + 'ENCRYPTED PRIVATE KEY-----',
])
def test_packaging_rejects_additional_credential_formats_without_exposing_values(source_tree, tmp_path, token):
    (source_tree / 'docs' / 'example.md').write_text('Accidental credential: ' + token)
    result=run_packager('package_source.py',source_tree,tmp_path/'source.zip')
    assert result.returncode != 0
    assert 'Possible secret in eligible source file: docs/example.md' in result.stderr
    assert token not in result.stderr
    assert token not in result.stdout


def test_packaging_does_not_follow_source_or_directory_symlinks(source_tree,tmp_path):
    private=tmp_path/'private'
    private.mkdir()
    (private/'notes.md').write_text('Secret: '+'sk-'+'x'*32)
    (source_tree/'docs'/'linked.md').symlink_to(private/'notes.md')
    (source_tree/'docs'/'linked-directory').symlink_to(private,target_is_directory=True)
    (source_tree/'examples').symlink_to(private,target_is_directory=True)
    destination=tmp_path/'source.zip'
    result=run_packager('package_source.py',source_tree,destination)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(destination) as archive:
        assert not any('linked' in name or name.startswith('forest/examples/') for name in archive.namelist())


def test_directory_export_matches_source_zip_and_preserves_source_modes(source_tree,tmp_path):
    script=source_tree/'scripts'/'run.sh'
    script.write_text('#!/bin/sh\nexit 0\n')
    script.chmod(0o755)
    (source_tree/'docs'/'QUALIFICATION_REPORT.md').write_text('Private development history')
    (source_tree/'docs'/'PUBLIC.md').write_text('Public operating guide')
    destination=tmp_path/'github'
    result=subprocess.run([sys.executable,str(source_tree/'scripts'/'package_source.py'),'--directory',str(destination)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    zipped=tmp_path/'source.zip'
    create_source(zipped,source_tree)
    with zipfile.ZipFile(zipped) as archive:
        assert {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()} == {name.removeprefix('forest/') for name in archive.namelist()}
        for name in archive.namelist():
            assert (destination/name.removeprefix('forest/')).read_bytes()==archive.read(name)
    assert (destination/'scripts'/'run.sh').stat().st_mode & 0o111
    assert not (destination/'.git').exists()


def test_directory_export_excludes_private_research_records_before_secret_scanning(source_tree,tmp_path):
    private_files=[
        'docs/PIPELINE_LIVE_VALIDATION.md',
        'docs/research_tasks/notes.md',
        'docs/research_tasks/example/nested/observations.json',
    ]
    for name in private_files:
        path=source_tree/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('Private development record: '+'sk-'+'x'*32)
    public_files=['docs/PUBLICATION_DELIVERY.md','docs/research_tasks-guide.md']
    for name in public_files:
        (source_tree/name).write_text('Public product guide\n')
    destination=tmp_path/'github'
    result=subprocess.run(
        [sys.executable,str(source_tree/'scripts'/'package_source.py'),'--directory',str(destination)],
        capture_output=True,text=True,
    )
    assert result.returncode == 0, result.stderr
    assert all(not (destination/name).exists() for name in private_files)
    assert not (destination/'docs/research_tasks').exists()
    assert all((destination/name).read_text()=='Public product guide\n' for name in public_files)


def test_directory_export_rejects_existing_files_or_secrets_without_partial_output(source_tree,tmp_path):
    destination=tmp_path/'github'
    destination.mkdir()
    existing=destination/'README.md'
    existing.write_text('Existing user content')
    with pytest.raises(ValueError,match='new or empty'):
        export_source_tree(destination,source_tree)
    assert existing.read_text()=='Existing user content'
    existing.unlink()
    (source_tree/'docs'/'oops.md').write_text('sk-'+'x'*32)
    with pytest.raises(ValueError,match='Possible secret'):
        export_source_tree(destination,source_tree)
    assert list(destination.iterdir())==[]


def test_release_preserves_existing_destination(source_tree, tmp_path):
    destination = tmp_path / 'source.zip'
    destination.write_bytes(b'existing delivery')
    with pytest.raises(ValueError, match='already exists'):
        create_source(destination, source_tree)
    assert destination.read_bytes() == b'existing delivery'


def test_release_rejects_nonportable_name_before_writing_archive(source_tree, tmp_path):
    (source_tree / 'docs' / 'CON.md').write_text('Incompatible Windows filename\n')
    destination = tmp_path / 'source.zip'
    with pytest.raises(ValueError, match='Windows-incompatible'):
        create_source(destination, source_tree)
    assert not destination.exists()
