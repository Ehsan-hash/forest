import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['actionable', 'history'])
def test_isolated_actionable_decisions(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'decisions.db'),
           'FOREST_OWNER_TOKEN': uuid4().hex, 'FOREST_MODEL': '', 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), case], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


def check(case):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.main import app
    from services.api.db import Session, TaskRun
    from services.interventions.models import ActionDecision
    from test_api import create, ok

    with TestClient(app) as client:
        project = create(client)
        other = create(client)
        ids = {}
        with Session.begin() as session:
            run = TaskRun(project_id=project['id'], request_id=uuid4().hex, status='waiting_input')
            foreign = TaskRun(project_id=other['id'], request_id=uuid4().hex, status='waiting_input')
            session.add_all([run, foreign]); session.flush()
            for day, (label, status, consumed) in enumerate([
                ('pending', 'pending', None), ('accepted', 'accepted', None),
                ('rejected', 'rejected', None), ('empty', 'accepted', ''),
                ('stale', 'stale', None),
            ]):
                ident = uuid4().hex; ids[label] = ident
                session.add(ActionDecision(id=ident, project_id=project['id'], run_id=run.id,
                    action_id=ident, observed_revision=project['revision'], proposed={'tool': label},
                    status=status, consumed_at=consumed,
                    created_at=(datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()))
            for day in range(50):
                ident = uuid4().hex
                session.add(ActionDecision(id=ident, project_id=project['id'], run_id=run.id,
                    action_id=ident, observed_revision=project['revision'], proposed={'tool': 'consumed'},
                    status='accepted', consumed_at='2025-01-01T00:00:00+00:00',
                    created_at=(datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()))
            ident = uuid4().hex
            session.add(ActionDecision(id=ident, project_id=other['id'], run_id=foreign.id,
                action_id=ident, observed_revision=other['revision'], proposed={'tool': 'foreign'}, status='pending'))
        path = f"/api/projects/{project['id']}/decisions"
        with Session() as session:
            before = [(r.id, r.status, r.consumed_at) for r in session.scalars(select(ActionDecision).order_by(ActionDecision.id))]
        if case == 'actionable':
            rows = ok(client.get(path, params={'status': 'actionable', 'limit': 50}))
            assert [r['id'] for r in rows] == [ids[k] for k in ('empty', 'rejected', 'accepted', 'pending')]
            assert all(r['project_id'] == project['id'] and r['run_status'] == 'waiting_input' for r in rows)
            assert [r['id'] for r in ok(client.get(path, params={'status': 'actionable', 'limit': 2}))] == [ids['empty'], ids['rejected']]
        else:
            rows = ok(client.get(path))
            assert len(rows) == 50 and all(r['proposed']['tool'] == 'consumed' for r in rows)
            assert [r['id'] for r in ok(client.get(path, params={'status': 'pending'}))] == [ids['pending']]
            assert len(ok(client.get(path, params={'status': 'accepted', 'limit': 200}))) == 52
            assert ok(client.get(path, params={'status': 'unknown'})) == []
        with Session() as session:
            assert [(r.id, r.status, r.consumed_at) for r in session.scalars(select(ActionDecision).order_by(ActionDecision.id))] == before


if __name__ == '__main__':
    check(sys.argv[1])
