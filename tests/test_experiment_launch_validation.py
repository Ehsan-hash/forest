"""Saved source is not an executable configuration; reject before enqueue."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['source_only', 'command', 'entrypoint', 'example',
                                 'override', 'remove_command', 'node_command', 'default_command',
                                 'empty', 'unknown_example', 'replay_after_edit'])
def test_isolated_experiment_launch(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'experiment.db'),
           'FOREST_OWNER_TOKEN': uuid4().hex, 'FOREST_MODEL': '', 'PYTHONPATH': str(ROOT)}
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), case],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def check(case):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.main import app
    from services.api.db import Session, TaskRun, Project, ExperimentSpec
    from test_api import create, ok, add_node

    with TestClient(app) as client:
        project = create(client)
        data = {'code': 'raise RuntimeError("reference source must not execute")'}
        body = {'request_id': uuid4().hex}
        if case == 'empty': data = {}
        if case == 'unknown_example': data['example'] = 'unsupported'
        if case in ('command', 'remove_command', 'replay_after_edit'):
            data['command'] = [sys.executable, '-c', 'pass']
        if case == 'entrypoint': data['entrypoint'] = 'example:run'
        if case == 'example': data['example'] = 'class_weight_calibration'
        if case == 'override': body['command'] = [sys.executable, '-c', 'pass']
        if case == 'remove_command': body['command'] = ''
        if case == 'node_command':
            node = add_node(client, project, config={'command': [sys.executable, '-c', 'pass']})
            data['node_id'] = node['id']
        if case == 'default_command':
            with Session.begin() as session:
                p = session.get(Project, project['id'])
                p.config = {**p.config, 'agent_execution': {'command': [sys.executable, '-c', 'pass']}}
        experiment = ok(client.post('/api/experiments', json={
            'project_id': project['id'], 'title': 'Reference source', 'data': data}))
        with Session() as session:
            before = [r.id for r in session.scalars(select(TaskRun))]
        response = client.post(f"/api/experiments/{experiment['id']}/launch", json=body)
        if case in ('source_only', 'remove_command', 'empty', 'unknown_example'):
            result = ok(response, 400)['detail']
            assert result['code'] == 'INVALID_EXPERIMENT_CONFIG', result
            assert 'source' in result['message'].lower()
            with Session() as session:
                assert [r.id for r in session.scalars(select(TaskRun))] == before
        else:
            run = ok(response)
            assert run['kind'] == 'experiment' and run['status'] == 'queued'
            assert run['config']['code'] == data['code']
            # Replay preserves the accepted run identity rather than queuing twice.
            assert ok(client.post(f"/api/experiments/{experiment['id']}/launch", json=body))['id'] == run['id']
        assert ok(client.get(f"/api/experiments/{experiment['id']}"))['data'] == data
        if case == 'replay_after_edit':
            with Session.begin() as session:
                saved = session.get(ExperimentSpec, experiment['id'])
                saved.data = {'code': data['code']}
                saved.revision += 1
            assert ok(client.post(f"/api/experiments/{experiment['id']}/launch", json=body))['id'] == run['id']
            assert client.post(f"/api/experiments/{experiment['id']}/launch", json={'request_id': uuid4().hex}).status_code == 400


if __name__ == '__main__':
    check(sys.argv[1])
