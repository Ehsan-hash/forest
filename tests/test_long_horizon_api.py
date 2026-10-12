"""Real API and persistence checks for bounded-round continuation and recovery."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_long_horizon_api_preserves_identity_checkpoint_and_resources(tmp_path):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path/'api.db'),
           'FOREST_DATA_DIR': str(tmp_path/'data'), 'FOREST_MODEL': '', 'PYTHONPATH': str(ROOT)}
    checked = subprocess.run([sys.executable, __file__, 'run'], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
    assert checked.returncode == 0, checked.stdout + checked.stderr


if __name__ == '__main__':
    from datetime import datetime, timezone
    import json
    import time
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.main import app
    from services.api.db import Session, Project, TaskRun, Provider, ModelRequest
    from services.api.common import project_dir
    from services.interventions.lifecycle import lifecycle_target

    with TestClient(app) as client:
        created = client.post('/api/projects', json={
            'name': 'Checkpoint recovery contract', 'mode': 'manual',
            'goal': 'Retain real research history across executor boundaries',
            'config': {'controller': {'max_cycles': 1}},
            'budget': {'allow_paid': False, 'seconds': 100, 'cost_usd': 1},
        })
        created.raise_for_status()
        pid = created.json()['id']
        started = client.post(f'/api/projects/{pid}/research/start', json={'autonomous': False, 'max_cycles': 2})
        started.raise_for_status()
        assert 'max_cycles' not in started.json()
        assert started.json()['legacy_max_cycles'] == 1
        with Session.begin() as session:
            run = TaskRun(project_id=pid, request_id='round-resume', kind='agent', status='paused',
                config={'kind': 'agent', 'agent_budget': {'steps': 10, 'cost': .8}},
                resource={'elapsed_seconds': 2}, pid=99999998, process_created=123,
                started_at=datetime.now(timezone.utc).isoformat())
            session.add(run); session.flush()
            run.output_path = 'runs/' + run.id
            rid = run.id
            provider = Provider(name='Existing ledger', kind='codex_cli', model='ledger-only',
                base_url='http://127.0.0.1', allow_paid=False)
            session.add(provider); session.flush()
            usage = ModelRequest(provider_id=provider.id, project_id=pid, run_id=rid, model='ledger-only',
                status='settled', estimated_microusd=25000, reserved_microusd=25000)
            session.add(usage); session.flush(); usage_id = usage.id
            workspace = project_dir(pid)/run.output_path/'workspace'
            workspace.mkdir(parents=True)
            path = workspace/'agent_session.json'
            checkpoint = {'run_id': rid, 'status': 'waiting',
                'transcript': [{'step': 401, 'observation': 'Historical protocol decision'}],
                'wait_for': {'type': 'work_round'}, 'lifecycle': {'phase': 'continue', 'round_index': 41},
                'recovery': {'reason': 'round_boundary'}, 'goal_contract': {'goal': 'Retain research history'}}
            path.write_text(json.dumps(checkpoint))
            usage_path=workspace.parent/'agent_usage.json'
            usage_path.write_text(json.dumps({'run_id':rid,'totals':{'cost':.025},'active_seconds':2}))
        original = path.read_bytes()
        original_usage = usage_path.read_bytes()
        summary = client.get(f'/api/runs/{rid}/session?summary=true')
        summary.raise_for_status()
        saved = summary.json()['session']
        assert saved['lifecycle'] == checkpoint['lifecycle']
        assert saved['recovery'] == checkpoint['recovery']
        assert saved['goal_contract'] == checkpoint['goal_contract']
        assert saved['wait_for']['type'] == 'work_round'
        assert saved['transcript_count'] == 1
        assert saved['usage']['totals']['cost'] == .025
        assert saved['usage']['active_seconds'] == 2
        legacy_usage=workspace/'agent_usage.json'
        usage_path.replace(legacy_usage)
        assert client.get(f'/api/runs/{rid}/session?summary=true').json()['session']['usage']['totals']['cost'] == .025
        legacy_usage.write_text(json.dumps({'run_id':'other-run','totals':{'cost':999}}))
        invalid=client.get(f'/api/runs/{rid}/session?summary=true')
        invalid.raise_for_status()
        assert invalid.json()['session']['usage'] is None
        assert invalid.json()['session']['usage_error']['code']=='LEDGER_INVALID'
        legacy_usage.unlink()
        usage_path.write_bytes(original_usage)
        for broken in (b'{truncated',b'\xff',b'[]',json.dumps({'run_id':rid,'totals':None}).encode()):
            usage_path.write_bytes(broken)
            diagnostic=client.get(f'/api/runs/{rid}/session?summary=true')
            diagnostic.raise_for_status()
            observed=diagnostic.json()['session']
            assert observed['usage'] is None
            assert observed['usage_error']['code'] in ('LEDGER_UNREADABLE','LEDGER_INVALID')
            assert observed['lifecycle']==checkpoint['lifecycle']
            assert path.read_bytes()==original
        usage_path.write_bytes(original_usage)
        assert client.get(f'/api/runs/{rid}/session?summary=true').json()['session']['usage_error'] is None
        path.write_text(json.dumps({**checkpoint,'status':'waiting_input',
            'reconciliation_reason':'Original executor receipt cannot be reconciled'}))
        paused=client.get(f'/api/runs/{rid}/session?summary=true').json()['session']
        assert paused['status']=='waiting_input'
        assert paused['reconciliation_reason']=='Original executor receipt cannot be reconciled'
        path.write_bytes(original)
        full=client.get(f'/api/runs/{rid}/session').json()['session']
        assert 'totals' not in full and 'active_seconds' not in full
        from scripts.qualify_release import evidence
        def observed_api(method, endpoint, **kwargs):
            response=client.request(method,endpoint,**kwargs)
            response.raise_for_status()
            return response.json()
        _,qualification,receipts,errors=evidence(observed_api,{'id':rid,'metrics':{}},[])
        assert qualification['supervisory_usage']['totals']['cost'] == .025
        assert 'totals' not in qualification
        assert receipts == [] and errors == ['No observed successful real-process receipt']
        resumed = client.post(f'/api/runs/{rid}/resume', json={})
        assert resumed.status_code == 200, resumed.text
        deadline = time.monotonic() + 10
        while resumed.json()['status'] == 'pausing' and time.monotonic() < deadline:
            time.sleep(.02)
            resumed = client.get(f'/api/runs/{rid}')
            resumed.raise_for_status()
        assert resumed.json()['id'] == rid and resumed.json()['status'] == 'queued', resumed.text
        assert resumed.json()['config']['_next_attempt']['mode'] == 'continue'
        assert path.read_bytes() == original
        assert usage_path.read_bytes() == original_usage
        with Session() as session:
            run = session.get(TaskRun, rid)
            assert run.resource['elapsed_seconds'] >= 2
            assert run.metrics == {} and run.exit_code is None
            assert session.get(ModelRequest, usage_id).estimated_microusd == 25000
            assert len(list(session.scalars(select(TaskRun).where(TaskRun.project_id == pid)))) == 1
            target = {'id': rid, 'project_id': pid, 'kind': run.kind, 'output_path': run.output_path,
                      'pid': 99999998, 'process_created': 123, 'config': run.config, 'status': 'paused'}
        for invalid in ({**checkpoint, 'run_id': 'other-run'}, {**checkpoint, 'status': 'completed'}):
            path.write_text(json.dumps(invalid))
            try: lifecycle_target(target, 'resume')
            except RuntimeError as exc: assert 'lost' in str(exc)
            else: raise AssertionError('A mismatched or completed checkpoint must not resume execution')
        path.write_bytes(original)
