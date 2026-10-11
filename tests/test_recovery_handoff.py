"""Graph route handoffs use settled receipts and the original shared budget.

Only the model response is deterministic test input. The API, worker, executor,
Agent file authorization, checkpoint, scheduler and alternate process are real.
"""
import json
import sys
import uuid

import pytest

from test_worker import Harness, wait_until


PERMISSION_AGENT = '''
from pathlib import Path
from copy import deepcopy
import json
from research.agents import runtime
from services.api.db import Session, TaskRun

class PermissionModelFixture:
    native_tools = False
    api = 'codex_cli'
    request_guard = True

    def __init__(self, provider, credential=None, allow_paid=False):
        self.config = deepcopy(provider.get('config', {}))

    def _discover_local_capabilities(self):
        pass

    def build_request(self, messages, tools=None, max_output_tokens=None):
        return 'test-fixture', {'messages': messages, 'tools': tools,
            'max_output_tokens': max_output_tokens}

    def complete(self, messages, **kwargs):
        return {'model': 'deterministic-permission-fixture',
            'text': json.dumps({'tool': 'read_file', 'arguments': {
                'path': '/outside-authorized-workspace/blocked-training.csv',
                'offset': None, 'limit': None}}),
            'usage': {'input_tokens': 0, 'output_tokens': 0,
                'cached_input_tokens': 0, 'reasoning_tokens': 0, 'cost': None}}

def run(parameters, output):
    root = Path(output)
    if parameters.get('defer_exit'):
        import atexit, time
        atexit.register(time.sleep, 2)
    with Session() as session:
        task = session.get(TaskRun, root.name)
        config = deepcopy(task.config)
    config['provider_snapshot'] = {'id': 'fixture-no-transport', 'kind': 'codex_cli',
        'model': 'deterministic-permission-fixture', 'config': {}}
    original = runtime.ModelClient
    runtime.ModelClient = PermissionModelFixture
    try:
        return runtime.run_agent(root.name, root / 'workspace', config)
    except runtime.AgentYield:
        if parameters.get('crash_before_receipt'):
            import os
            os._exit(17)
        raise
    finally:
        runtime.ModelClient = original
'''


@pytest.fixture
def handoff_worker(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start_api()
        harness.start_worker()
        yield harness
    finally:
        harness.cleanup()


@pytest.mark.parametrize('handoff_failure', ['none', 'worker_restart', 'executor_crash'])
def test_permission_recovery_settles_source_reservation_before_real_alternate(handoff_worker, handoff_failure):
    h = handoff_worker
    project = h.request('POST', '/api/projects', json={
        'name': 'Existing route recovers after a settled permission receipt',
        'goal': 'Use the existing authorized route to compute the registered sum',
        'mode': 'manual', 'budget': {'seconds': 90, 'allow_paid': False},
    })
    graph = h.request('GET', f"/api/projects/{project['id']}/graph")
    branch = graph['branches'][0]
    branch_root = h.directory / 'data' / 'projects' / project['id'] / branch['workspace']
    branch_root.mkdir(parents=True, exist_ok=True)
    (branch_root / 'permission_agent.py').write_text(PERMISSION_AGENT)
    original_ids = {node['id'] for node in graph['nodes']}

    def add_node(title, config):
        current = h.request('GET', f"/api/projects/{project['id']}/graph")
        ident = str(uuid.uuid4())
        updated = h.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': current['revision'],
            'operation': 'add_node', 'params': {'id': ident, 'branch_id': branch['id'],
                'type': 'experiment', 'title': title,
                'instructions': 'Execute this existing registered route', 'config': config},
        })
        return next(node for node in updated['graph']['nodes'] if node['id'] == ident)

    source = add_node('Permission blocked route', {
        'kind': 'experiment', 'entrypoint': 'permission_agent:run',
        'role': 'Researcher', 'tools': ['read_file'],
        'parameters': {'defer_exit': handoff_failure == 'worker_restart',
            'crash_before_receipt': handoff_failure == 'executor_crash'},
    })
    alternate = add_node('Authorized deterministic sum', {
        'kind': 'command', 'command': [sys.executable, '-c',
            "from pathlib import Path; Path('answer.txt').write_text(str(sum(range(17))))"],
    })
    run = h.launch(source)
    assert run['config']['timeout'] == pytest.approx(90)
    assert run['resource']['time_budget']['requested_task_timeout_seconds'] is None

    if handoff_failure == 'worker_restart':
        receipt = h.output(run) / 'result.json'
        wait_until(lambda: receipt.is_file(), timeout=25)
        assert json.loads(receipt.read_text())['status'] == 'waiting_input'
        # Even a flushed durable handoff cannot release the live source's
        # reservation before its real executor exit and elapsed-time settlement.
        assert h.run(run)['status'] == 'running'
        assert len(h.request('GET', f"/api/projects/{project['id']}/runs")) == 1
        h.stop(h.worker)
        h.start_worker()

    source_done = wait_until(lambda: (value if (value := h.run(run))['status'] == 'waiting_input'
        and value['resource'].get('graph_recovery_handoff') else None), timeout=40)
    handoff = source_done['resource']['graph_recovery_handoff']
    assert handoff['handoff_status'] == 'admitted', source_done
    assert len(handoff['run_ids']) == 1
    assert source_done['resource']['elapsed_seconds'] > 0
    assert source_done['resource']['elapsed_seconds'] < 90
    assert not source_done['resource'].get('pending_graph_recovery')
    assert not source_done['resource'].get('graph_recovery_compute_hold')
    assert source_done['resource']['blocked_reason'] != 'time_budget_exhausted'
    if handoff_failure == 'executor_crash':
        assert source_done['resource']['attempts'][-1]['status'] == 'interrupted'
        assert len(source_done['resource']['attempts']) == 1

    alternative_run = h.request('GET', '/api/runs/' + handoff['run_ids'][0])
    assert alternative_run['node_id'] == alternate['id']
    assert alternative_run['project_id'] == run['project_id']
    finished = h.terminal(alternative_run, timeout=30)
    assert finished['status'] == 'completed', finished
    assert (h.output(finished) / 'workspace' / 'answer.txt').read_text() == '136'
    assert finished['resource']['elapsed_seconds'] + source_done['resource']['elapsed_seconds'] < 90
    session = json.loads((h.output(run) / 'workspace' / 'agent_session.json').read_text())
    assert session['execution_snapshot']['external_ledger_id'] == 'forest-project:' + project['id']
    assert session['status'] == 'waiting_input'
    assert session['lifecycle']['research_state'] == 'active'
    assert session['lifecycle']['decisions'][-1]['selected_node_ids'] == [alternate['id']]
    assert any('PATH_ESCAPE' in message.get('content', '')
        for message in session['messages'])

    runs = h.request('GET', f"/api/projects/{project['id']}/runs")
    assert {item['id'] for item in runs} == {run['id'], alternative_run['id']}
    assert len(finished['resource']['attempts']) == 1
    current = h.request('GET', f"/api/projects/{project['id']}/graph")
    assert {node['id'] for node in current['nodes']} == original_ids | {source['id'], alternate['id']}
    assert h.request('GET', f"/api/projects/{project['id']}")['mode'] == 'manual'
