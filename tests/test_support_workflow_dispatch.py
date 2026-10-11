"""A scripted local transport exercises real delegate tool and worker paths.

The transport fixture only validates request routing and policy inheritance;
its finish responses are not scientific work or scientific evidence.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_delegate_research_schedules_and_runs_a_policy_inheriting_child(tmp_path):
    env = {**os.environ,
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'dispatch.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_WORKER_CONCURRENCY': '1',
           'PYTHONPATH': str(ROOT), 'PYTHONUNBUFFERED': '1'}
    completed = subprocess.run([sys.executable, __file__, str(tmp_path)], cwd=ROOT,
                               env=env, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(next(line.removeprefix('REPORT:') for line in completed.stdout.splitlines()
                             if line.startswith('REPORT:')))
    assert report['parent_status'] == report['child_status'] == 'completed'
    assert report['tool_name'] == 'delegate_research'
    assert {key: report['edge'][key] for key in ('source', 'target', 'relation')} == {
        'source': report['parent_node_id'], 'target': report['child_node_id'],
        'relation': 'derived_from'}
    assert report['child_config']['agent_workflow_id'] == 'forest-experimentation'
    assert report['child_config']['agent_workflow_phase'] == 'execute'
    assert report['child_config']['agent_support_role'] == 'Experimenter'
    assert report['parent_node_id'] == report['child_config']['parent_node_id']
    assert report['model_request_count'] == 3
    assert report['policy_request_count'] == 1
    assert report['child_policy_delivered'] is True


if __name__ == '__main__':
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sys.path.insert(0, str(ROOT))
    from tests.test_worker import Harness, wait_until

    directory = Path(sys.argv[1])
    captures = []
    lock = threading.Lock()
    state = {'project_id': None, 'revision': None}

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _write(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == '/api/show':
                return self._write({'model_info': {}})
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages = request.get('messages', [])
            system = '\n'.join(m.get('content', '') for m in messages if m.get('role') == 'system')
            with lock:
                captures.append({'messages': messages, 'system': system})
                index = len(captures)
            if 'FOREST SUPPORT WORKFLOW: forest-experimentation' in system:
                action = {'tool': 'finish', 'arguments': {
                    'summary': 'Scripted transport confirms a support task reached its real worker session.',
                    'artifacts': []}}
            elif index == 1:
                action = {'tool': 'delegate_research', 'arguments': {
                    'workflow_id': 'forest-experimentation', 'phase': 'execute',
                    'support_role': 'Experimenter',
                    'instructions': 'Inspect the delegated question and report actual evidence only; this fixture runs no experiment.',
                    'expected_revision': state['revision'],
                    'title': 'Support dispatch plumbing check'}}
            else:
                action = {'tool': 'finish', 'arguments': {
                    'summary': 'Parent scheduled its supporting task through the research workflow.',
                    'artifacts': []}}
            return self._write({'model': 'scripted-local-transport-fixture',
                'message': {'role': 'assistant', 'content': json.dumps(action)},
                'done': True, 'prompt_eval_count': 100, 'eval_count': 30})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    harness = Harness(directory)
    harness.env['FOREST_WORKER_CONCURRENCY'] = '1'
    try:
        harness.start_api()
        provider = harness.request('POST', '/api/providers', json={
            'name': 'Local scripted dispatch transport', 'kind': 'ollama',
            'base_url': f'http://127.0.0.1:{server.server_port}',
            'model': 'scripted-local-transport-fixture', 'allow_paid': False,
            'config': {'timeout': 20}})
        project = harness.request('POST', '/api/projects', json={
            'name': 'Support workflow dispatch ' + str(uuid.uuid4())[:8],
            'goal': 'Route a real supporting task through the registered worker workflow.',
            'config': {'provider_id': provider['id']},
            'budget': {'max_runs': 8, 'seconds': 120, 'allow_paid': False}})
        state['project_id'] = project['id']
        graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        parent_id = str(uuid.uuid4())
        response = harness.request('POST', f"/api/projects/{project['id']}/graph/commands", json={
            'request_id': str(uuid.uuid4()), 'expected_revision': graph['revision'],
            'operation': 'add_node', 'targets': [],
            'params': {'id': parent_id, 'branch_id': graph['branches'][0]['id'],
                'type': 'task', 'title': 'Workflow dispatcher parent',
                'instructions': 'Create one supporting task that can inspect this research question.',
                'config': {'kind': 'agent', 'agent_budget': {'steps': 8}, 'timeout': 30}}})
        parent = next(node for node in response['graph']['nodes'] if node['id'] == parent_id)
        queued_parent = harness.launch(parent)
        state['revision'] = harness.request('GET', f"/api/projects/{project['id']}/graph")['revision']
        harness.start_worker()
        parent_result = harness.terminal(queued_parent, timeout=45)
        assert parent_result['status'] == 'completed', parent_result

        def child_run():
            current = harness.request('GET', f"/api/projects/{project['id']}/graph")
            child = next((node for node in current['nodes']
                          if node['title'] == 'Support dispatch plumbing check'), None)
            if not child:
                return None
            runs = harness.request('GET', f"/api/projects/{project['id']}/runs")
            row = next((run for run in runs if run.get('node_id') == child['id']), None)
            return (child, row) if row else None

        child, child_queued = wait_until(child_run, timeout=20)
        child_result = harness.terminal(child_queued, timeout=45)
        assert child_result['status'] == 'completed', child_result
        final_graph = harness.request('GET', f"/api/projects/{project['id']}/graph")
        edge = next(e for e in final_graph['edges'] if e['source'] == parent_id and
                    e['target'] == child['id'] and e['relation'] == 'derived_from')
        child_config = child['config']
        child_captures = [item for item in captures if 'FOREST SUPPORT WORKFLOW: forest-experimentation' in item['system']]
        assert child_captures, 'No actual child model request carried the selected workflow prompt.'
        system = child_captures[0]['system']
        assert 'FOREST_SHARED_RESEARCH_V2' in system
        assert 'RESEARCH FREEDOM' in system and 'ANTI-DEFENSIVE RESEARCH' in system
        assert 'SUPPORT ROLE: Experimenter' in system
        assert 'Run the declared scientific computation' in system
        assert 'ROLE RESPONSIBILITY:' in system
        assert all('FOREST_SHARED_RESEARCH_V2' in item['system'] for item in captures)
        report = {
            'parent_status': parent_result['status'], 'child_status': child_result['status'],
            'tool_name': 'delegate_research', 'parent_node_id': parent_id,
            'child_node_id': child['id'], 'edge': edge,
            'child_config': child_config, 'model_request_count': len(captures),
            'policy_request_count': len(child_captures),
            'child_policy_delivered': True,
            'evidence_scope': 'Actual HTTP API, delegate_research ToolRuntime, graph/scheduler, queued child, worker and provider request; scripted local model output tests dispatch only and is not scientific evidence.'}
        (directory / 'support-dispatch-report.json').write_text(json.dumps(report, indent=2))
        print('REPORT:' + json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        harness.cleanup()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
