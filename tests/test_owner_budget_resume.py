"""Explicit owner admission reopens real allowance without replaying owned work."""
import json
import sys
import time
import uuid

import pytest

from test_worker import wait_until
from test_submission_handoff import SUBMISSION_AGENT, FITTED_CHILD
from tests.test_intervention_worker import intervention_harness


def _prepare_parent(h, *, project_seconds, task_timeout=None, job_seconds=20):
    h.start_api(); h.start_worker()
    project = h.request('POST', '/api/projects', json={
        'name': 'Owner changes actual compute allowance', 'mode': 'manual',
        'goal': 'Fit and validate after original computation settles',
        'budget': {'seconds': project_seconds}})
    graph = h.request('GET', f"/api/projects/{project['id']}/graph")
    branch = graph['branches'][0]
    workspace = h.directory / 'data' / 'projects' / project['id'] / branch['workspace']
    workspace.mkdir(parents=True, exist_ok=True)
    # The original job is a real detached process. The marker is outside its
    # editable workspace and prevents fixture re-entry from launching it again.
    injected = '''    from research.execution.process_manager import process_manager
    import sys
    if not (folder/'original-job-started.txt').exists():
        original_job=process_manager(folder/'workspace',config).start(
            [sys.executable,'-c',"import time;from pathlib import Path;Path('original-job-launch.txt').write_text('one launch');time.sleep(JOB_SECONDS);Path('original-job-completed.txt').write_text('completed')"],
            process_id='owned-before-submission')
        (folder/'original-job-started.txt').write_text(json.dumps(original_job))
    original=runtime.ModelClient
'''.replace('JOB_SECONDS', str(job_seconds))
    (workspace / 'submission_agent.py').write_text(SUBMISSION_AGENT.replace(
        '    original=runtime.ModelClient\n', injected))
    (workspace / 'fitted_child.py').write_text(FITTED_CHILD)
    source_id = str(uuid.uuid4())
    result = h.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
        'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'], 'operation': 'add_node',
        'params': {'id': source_id, 'branch_id': branch['id'], 'type': 'experiment',
            'title': 'Parent with accepted original work', 'instructions': 'Inspect the actual child fit receipt',
            'config': {'kind': 'experiment', 'entrypoint': 'submission_agent:run', 'role': 'Researcher',
                'execution_backend': 'local', 'tools': ['experiment_run', 'finish'],
                **({'timeout': task_timeout} if task_timeout is not None else {}),
                'parameters': {'child_delay': .1}}}})
    parent = h.launch(next(node for node in result['graph']['nodes'] if node['id'] == source_id))
    return project, parent


def _original_receipt(h, parent):
    return json.loads((h.output(parent) / 'workspace' / '.forest-processes' /
                       'owned-before-submission' / 'completion.json').read_text())


def _exhausted(h, parent):
    value = h.run(parent)
    return value if (value['status'] == 'waiting_input' and value['resource'].get('hard_budget_exhausted')
        and not value['resource'].get('execution_handoff_compute_hold')) else None


def test_owner_budget_extension_resumes_same_intent_after_real_job_cancel(tmp_path, intervention_harness):
    h = intervention_harness
    try:
        project, parent = _prepare_parent(h, project_seconds=3)
        before = wait_until(lambda: _exhausted(h, parent), timeout=20)
        assert before['resource']['time_budget']['requested_task_timeout_seconds'] is None
        assert before['resource']['elapsed_seconds'] >= 3
        assert _original_receipt(h, parent)['status'] == 'cancelled'
        original_launch_id = _original_receipt(h, parent)['launch_id']
        pending = before['resource']['pending_submission']
        usage_path = h.output(parent) / 'agent_usage.json'
        prior_usage = json.loads(usage_path.read_text())
        h.request('PATCH', f"/api/projects/{project['id']}", json={'budget': {'seconds': 100}})
        resumed = h.request('POST', f"/api/runs/{parent['id']}/resume", json={})
        assert resumed['id'] == parent['id']
        assert not resumed['resource'].get('hard_budget_exhausted')
        assert resumed['resource']['elapsed_seconds'] >= before['resource']['elapsed_seconds']
        final = h.terminal(parent, timeout=35)
        assert final['status'] == 'completed', final
        runs = h.request('GET', f"/api/projects/{project['id']}/runs")
        assert len(runs) == 2
        child = next(row for row in runs if row['id'] != parent['id'])
        assert child['status'] == 'completed'
        receipt = final['resource']['submission_receipts'][pending['action_id']]
        assert receipt['run_ids'] == [child['id']]
        assert receipt['settled_at']
        measurements = json.loads((h.output(child) / 'real-fit.json').read_text())
        assert measurements['validation_mse'] < .01
        assert measurements == child['metrics']
        assert final['resource']['elapsed_seconds'] >= before['resource']['elapsed_seconds']
        history = final['resource']['time_budget']['exhaustion_history']
        assert history[0]['limit'] == 3
        assert history[0]['retired_by_explicit_resume']['project_budget_seconds'] == 100
        assert _original_receipt(h, parent)['launch_id'] == original_launch_id
        assert _original_receipt(h, parent)['status'] == 'cancelled'
        assert not (h.output(parent) / 'workspace' / 'original-job-completed.txt').exists()
        usage = json.loads(usage_path.read_text())
        assert usage['run_id'] == prior_usage['run_id'] == parent['id']
        assert usage['active_seconds'] >= prior_usage['active_seconds']
        assert len(json.loads((h.output(parent) / 'workspace' / 'agent_session.json').read_text())['transcript']) == 2
    finally:
        h.cleanup()


@pytest.mark.parametrize('new_project_seconds', [100, None])
def test_project_growth_cannot_reopen_exhausted_requested_task_limit(tmp_path, intervention_harness, new_project_seconds):
    h = intervention_harness
    try:
        project, parent = _prepare_parent(h, project_seconds=6, task_timeout=3)
        before = wait_until(lambda: _exhausted(h, parent), timeout=20)
        assert before['resource']['time_budget']['requested_task_timeout_seconds'] == 3
        assert _original_receipt(h, parent)['status'] == 'cancelled'
        h.request('PATCH', f"/api/projects/{project['id']}", json={'budget': {'seconds': new_project_seconds}})
        response = h.client.post(f"/api/runs/{parent['id']}/resume", json={})
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'TIME_BUDGET_EXHAUSTED'
        after = h.run(parent)
        assert after['resource']['hard_budget_exhausted'] == before['resource']['hard_budget_exhausted']
        assert after['resource']['elapsed_seconds'] >= before['resource']['elapsed_seconds']
        assert len(h.request('GET', f"/api/projects/{project['id']}/runs")) == 1
    finally:
        h.cleanup()


@pytest.mark.parametrize('uncertain_receipt', [False, True])
def test_early_manual_resume_waits_for_original_job_and_actual_submission(tmp_path, intervention_harness, uncertain_receipt):
    h = intervention_harness
    try:
        project, parent = _prepare_parent(h, project_seconds=40, job_seconds=4)
        waiting = wait_until(lambda: (value if (value := h.run(parent))['status'] == 'waiting_input'
            and value['resource'].get('execution_handoff_compute_hold') else None), timeout=20)
        accepted = waiting['resource']['pending_submission']
        original = json.loads((h.output(parent) / 'original-job-started.txt').read_text())
        assert original['status'] == 'running'
        if uncertain_receipt:
            (h.output(parent) / 'workspace' / '.forest-processes' / 'owned-before-submission' /
             'completion.json').write_text('{unreadable original receipt')
            waiting = wait_until(lambda: (value if (value := h.run(parent))['resource'].get('blocked_reason')
                == 'execution_reconciliation' else None), timeout=10)
        resumed = h.request('POST', f"/api/runs/{parent['id']}/resume", json={})
        wait_until(lambda: (value if (value := h.run(parent))['resource'].get('last_intervention', {}).get('id')
            == resumed['intervention']['id'] else None), timeout=10)
        time.sleep(.5)
        current = h.run(parent)
        assert current['status'] == 'waiting_input'
        assert current['resource']['pending_submission']['action_id'] == accepted['action_id']
        assert len(h.request('GET', f"/api/projects/{project['id']}/runs")) == 1
        assert current['resource']['execution_handoff_compute_hold']
        assert current['resource']['elapsed_seconds'] >= waiting['resource']['elapsed_seconds']
        state = json.loads((h.output(parent) / 'workspace' / 'agent_session.json').read_text())
        assert len(state['transcript']) == 1
        assert state['wait_for'] == {'type': 'research_submission', 'action_id': accepted['action_id']}
        final = h.terminal(parent, timeout=30)
        assert final['status'] == 'completed', final
        assert _original_receipt(h, parent)['launch_id'] == original['launch_id']
        assert _original_receipt(h, parent)['status'] == 'completed'
        runs = h.request('GET', f"/api/projects/{project['id']}/runs")
        assert len(runs) == 2
        child = next(row for row in runs if row['id'] != parent['id'])
        assert child['status'] == 'completed'
        assert json.loads((h.output(child) / 'real-fit.json').read_text())['validation_mse'] < .01
    finally:
        h.cleanup()
