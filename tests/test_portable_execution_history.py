"""Copied/imported history cannot admit a pending original side effect."""
from copy import deepcopy
import json
import subprocess
import sys
import time
import uuid
import io
import shutil
import zipfile

from test_worker import Harness, wait_until
from services.interventions.history import EXECUTION_CONTROL_FIELDS, historical_resource


def test_all_durable_intents_are_preserved_as_non_executable_history():
    original = {key: {'original': key} for key in EXECUTION_CONTROL_FIELDS}
    original.update(elapsed_seconds=12.5, external_ledger_id='original-owner-ledger',
                    checkpoint='original-checkpoint', pending_intervention={'id': 'past-intervention'})
    result = historical_resource(original)
    assert not EXECUTION_CONTROL_FIELDS.intersection(result)
    assert result['historical_execution_control'] == {key: original[key] for key in EXECUTION_CONTROL_FIELDS}
    assert result['historical_pending_intervention'] == {'id': 'past-intervention'}
    assert result['elapsed_seconds'] == 12.5
    assert result['external_ledger_id'] == 'original-owner-ledger'
    assert result['checkpoint'] == 'original-checkpoint'
    assert historical_resource(result) == result
    assert original['pending_intervention']['id'] == 'past-intervention'


def test_real_import_and_duplicate_do_not_admit_original_pending_experiment(tmp_path):
    h = Harness(tmp_path)
    try:
        h.start_api()
        project, node = h.project_node(seconds=0)
        source = h.launch(node)
        action_id = str(uuid.uuid4())
        pending = {'action_id': action_id, 'tool': 'experiment_run',
            'arguments': {'entrypoint': 'historical_only:run', 'parameters': {'seed': 913}},
            'configuration': {}, 'accepted_at': source['created_at']}
        resource = {'pending_submission': pending, 'elapsed_seconds': 0.0,
            'wait_for': {'type': 'research_submission', 'action_id': action_id},
            'blocked_reason': 'research_submission'}
        source_script = """
import json,sys
from services.api.db import Session,TaskRun
with Session.begin() as s:
    row=s.get(TaskRun,sys.argv[1]); row.resource=json.loads(sys.argv[2]); row.status=sys.argv[3]
"""
        subprocess.run([sys.executable, '-c', source_script, source['id'], json.dumps(resource),
                        'interrupted'], env=h.env, check=True)
        exported = h.client.post(f"/api/projects/{project['id']}/export", json={})
        exported.raise_for_status()
        copied = h.request('POST', f"/api/projects/{project['id']}/duplicate", json={})
        imported_response = h.client.post('/api/projects/import', files={
            'file': ('historical.zip', exported.content, 'application/zip')})
        imported_response.raise_for_status()
        imported = imported_response.json()
        # The original owner has cancelled; this test starts only the imported
        # histories, not the pending source's actual authorized continuation.
        subprocess.run([sys.executable, '-c', source_script, source['id'], json.dumps(resource),
                        'cancelled'], env=h.env, check=True)
        before = {}
        for target in (copied, imported):
            runs = h.request('GET', f"/api/projects/{target['id']}/runs")
            assert len(runs) == 1
            row = runs[0]
            assert row['status'] == 'interrupted'
            assert not EXECUTION_CONTROL_FIELDS.intersection(row['resource'])
            assert row['resource']['historical_execution_control']['pending_submission'] == pending
            before[target['id']] = deepcopy(row)
        h.start_worker()
        time.sleep(1.5)
        for target in (copied, imported):
            runs = h.request('GET', f"/api/projects/{target['id']}/runs")
            assert len(runs) == 1
            assert runs[0] == before[target['id']]
            graph = h.request('GET', f"/api/projects/{target['id']}/graph")
            assert all(value['id'] != action_id for value in graph['nodes'])
    finally:
        h.cleanup()


def test_copy_origin_acl_survives_real_fork_export_import_and_duplicate(tmp_path):
    from research.agents.processes import atomic_json
    from research.agents.path_access import copy_origin_catalog, copy_origins, denied_origin
    from services.worker.execute import prepare_task_workspace
    h = Harness(tmp_path)
    try:
        h.start_api(); h.start_worker()
        project, node = h.project_node(seconds=0)
        pid = project['id']
        graph = h.request('GET', f'/api/projects/{pid}/graph')
        branch = graph['branches'][0]
        root = h.directory / 'data' / 'projects' / pid
        external = tmp_path / 'original-explicitly-authorized.csv'
        external.write_text('id,observation\n1,7\n')
        shutil.copy2(external, root / branch['workspace'] / 'measurement.csv')
        internal = root / branch['workspace'] / 'internal.csv'
        internal.write_text('id,observation\n1,9\n')
        atomic_json(root / '.forest-interventions' / 'origins' / 'original-copy.json', {
            'workspace': branch['workspace'], 'files': {'measurement.csv': [str(external)]}})
        forked = h.request('POST', f'/api/projects/{pid}/graph/commands', json={
            'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
            'operation': 'fork_branch', 'targets': [node['id']], 'params': {'name': 'Measured route'}})
        fork = next(value for value in forked['graph']['branches'] if value['id'] != branch['id'])
        wait_until(lambda: next(value for value in h.request('GET', f'/api/projects/{pid}/graph')['branches']
                   if value['id'] == fork['id'])['status'] == 'active')
        assert (root / fork['workspace'] / 'measurement.csv').read_bytes() == external.read_bytes()
        current = h.request('GET', f'/api/projects/{pid}')
        h.request('PATCH', f'/api/projects/{pid}', json={'expected_revision': current['revision'],
            'config': {**current['config'], 'agent_execution': {
                **current['config'].get('agent_execution', {}),
                'denied_readonly_paths': [str(external), str(internal)]}}})
        exported = h.client.post(f'/api/projects/{pid}/export', json={})
        exported.raise_for_status()
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(exported.content)).read('forest-project.json'))
        assert any(record['workspace'] == fork['workspace'] and str(external) in
                   record['files']['measurement.csv'] for record in manifest['workspace_origins'])
        copied = h.request('POST', f'/api/projects/{pid}/duplicate', json={})
        imported = h.client.post('/api/projects/import', files={
            'file': ('permission-lineage.zip', exported.content, 'application/zip')})
        imported.raise_for_status()
        for target in (copied, imported.json()):
            target_root = h.directory / 'data' / 'projects' / target['id']
            target_branch = next(value for value in h.request('GET', f"/api/projects/{target['id']}/graph")['branches']
                                 if value['name'] == 'Measured route')
            original_copy = target_root / target_branch['workspace'] / 'measurement.csv'
            catalog = copy_origin_catalog(target_root)
            origins = copy_origins(target_root, original_copy, catalog)
            assert str(external) in origins
            effective_config = target['config']['agent_execution']
            assert denied_origin(original_copy, effective_config, origins) == external
            internal_copy = original_copy.parent / 'internal.csv'
            internal_origins = copy_origins(target_root, internal_copy, catalog)
            assert str(internal) in internal_origins
            assert denied_origin(internal_copy, effective_config, internal_origins) is not None
            output = target_root / 'runs' / 'new-task'; (output / 'workspace').mkdir(parents=True)
            prepare_task_workspace(target_root, original_copy.parent, output, effective_config)
            assert not (output / 'workspace' / 'measurement.csv').exists()
            assert not (output / 'workspace' / 'internal.csv').exists()
    finally:
        h.cleanup()
