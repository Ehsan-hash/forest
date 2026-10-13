"""Real API/worker/HTTP-provider wiring smoke; model prose is a labelled protocol fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[3]
origin = os.environ.get('FOREST_WEB_URL', 'http://127.0.0.1:18340')
requests = []


class ProtocolFixture(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        task = next((json.loads(m['content']) for m in value['messages']
                     if m['role'] == 'user' and m['content'].startswith('{')), None)
        context = task['context']; citations = task['citations']
        requests.append({'model': value['model'], 'question': task['question'],
                         'focus': context['focus'], 'coverage': context['coverage'],
                         'run_states': {r['id']: r['status'] for r in context['runs']},
                         'citations': [{'id': c['id'], 'title': c['title'], 'read_scope': c['read_scope']} for c in citations]})
        report = {
            'summary': 'PROTOCOL FIXTURE: actual source context contains completed and failed experiments; model quality is not assessed.',
            'experiments': [{'node_id': r['node_id'], 'run_ids': [r['id']],
                             'what_was_done': r['config'].get('node_title', r['kind']),
                             'result': json.dumps({'status': r['status'], 'metrics': r['metrics']}),
                             'problems': r['error'] or 'None recorded'} for r in context['runs']],
            'findings': [{'title': 'Inspect actual failure', 'detail': 'The saved run contains an actual command failure.',
                          'severity': 'warning', 'evidence_label': 'REPORTED',
                          'node_ids': [r['node_id'] for r in context['runs'] if r['status'] == 'failed'],
                          'run_ids': [r['id'] for r in context['runs'] if r['status'] == 'failed'],
                          'citation_ids': []}],
            'recommendations': [{'title': 'Read retrieved source metadata', 'action': 'Open the cited source and inspect its scope.',
                                 'rationale': 'Protocol fixture verifies a retrieved source ID reaches the report.',
                                 'priority': 'high', 'kind': 'reading', 'node_ids': [], 'run_ids': [],
                                 'citation_ids': [citations[0]['id']] if citations else []}],
            'limitations': ['Model output is an explicitly authored local protocol fixture, not an actual AI judgment.'],
            'coverage': {k: context['coverage'][k] for k in ('node_ids', 'run_ids', 'resource_ids')},
        }
        payload = json.dumps({'id': 'local-protocol-fixture', 'model': value['model'],
                              'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(report)}}],
                              'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)


def run_smoke():
    server = ThreadingHTTPServer(('127.0.0.1', 0), ProtocolFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    client = httpx.Client(base_url=origin, timeout=45)
    project = provider = output = None
    def api(method, path, body=None):
        response = client.request(method, '/api' + path, json=body)
        response.raise_for_status(); return response.json() if response.content else None
    def terminal(run_id, expected):
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            value = api('GET', '/runs/' + run_id)
            if value['status'] == expected: return value
            if value['status'] in ('failed', 'cancelled', 'interrupted') and expected != value['status']:
                raise AssertionError(value.get('error'))
            time.sleep(.1)
        raise AssertionError(value)
    try:
        provider = api('POST', '/providers', {'name': 'Isolated analysis HTTP protocol fixture', 'kind': 'openai',
            'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'declared-protocol-fixture', 'allow_paid': False,
            'config': {'api': 'chat_completions', 'model_capabilities': {'context_window_tokens': 131072, 'max_output_tokens': 8192}, 'max_output_tokens': 4096}})
        project = api('POST', '/projects', {'name': 'Analysis wiring smoke — protocol fixture',
            'goal': 'Inspect trapezoidal numerical integration results and actual command failures', 'mode': 'manual',
            'budget': {'seconds': 300, 'allow_paid': False},
            'config': {'provider_id': provider['id'], 'publication_profile': {'id': 'operational'}}})
        def add(title, kind, config):
            graph = api('GET', f'/projects/{project["id"]}/graph')
            result = api('POST', f'/projects/{project["id"]}/graph/commands', {'request_id': str(uuid4()),
                'expected_revision': graph['revision'], 'operation': 'add_node', 'targets': [],
                'params': {'title': title, 'type': kind, 'config': config,
                    'position': {'x': 120 + 400 * len(graph['nodes']), 'y': 180}}, 'run': False})
            return next(n for n in result['graph']['nodes'] if n['title'] == title)
        sample = json.loads((ROOT / 'examples/cli/node.json').read_text())
        measured = add('Actual numerical measurement', 'experiment', {**sample['config'], 'timeout': 30})
        failed = add('Actual diagnostic failure', 'implementation', {'kind': 'command', 'timeout': 30,
            'command': ['python3', '-c', "print('actual analysis smoke failure',flush=True);raise SystemExit(7)"]})
        completed = api('POST', '/nodes/' + measured['id'] + '/run', {'request_id': str(uuid4())})
        actual_completed = terminal(completed['id'], 'completed')
        failure = api('POST', '/nodes/' + failed['id'] + '/run', {'request_id': str(uuid4())})
        actual_failed = terminal(failure['id'], 'failed')
        graph_before = api('GET', f'/projects/{project["id"]}/graph')
        analyses = []
        for scope, focus in [('project', {}), ('node', {'node_id': failed['id']}), ('branch', {'branch_id': measured['branch_id']})]:
            query = '?scope=' + scope + ''.join('&' + key + '=' + value for key, value in focus.items())
            context = api('GET', f'/projects/{project["id"]}/research/analysis/context' + query)
            intent = {'request_id': str(uuid4()), 'expected_revision': context['snapshot']['project_revision'],
                'expected_fingerprint': context['snapshot']['fingerprint'], 'scope': scope, **focus,
                'question': 'Summarize the actual experiments, failure and relevant references', 'language': 'en',
                'literature_query': 'trapezoidal rule numerical integration error', 'literature_sources': ['crossref'],
                'literature_limit': 2, 'timeout': 60}
            accepted = api('POST', f'/projects/{project["id"]}/research/analysis', intent)
            terminal(accepted['id'], 'completed')
            report = api('GET', '/runs/' + accepted['id'] + '/research-analysis')
            assert report['report'] and report['current'] and not report['artifact_error'], report
            assert report['report']['coverage']['node_ids'] == context['coverage']['node_ids']
            assert {actual_completed['id'], actual_failed['id']} == set(report['report']['coverage']['run_ids'])
            replay = api('POST', f'/projects/{project["id"]}/research/analysis', intent)
            assert replay['id'] == accepted['id']
            analyses.append(report)
        assert api('GET', f'/projects/{project["id"]}/graph') == graph_before
        assert len(api('GET', f'/projects/{project["id"]}/research/analysis')['runs']) == 3
        assert len(requests) == 3
        evidence = {'method': 'REAL API + REAL WORKER + actual ModelClient HTTP transport + authored local provider protocol fixture; actual Crossref retrieval where completed',
                    'live_model_quality': 'NOT RUN', 'project_id': project['id'],
                    'actual_experiments': [{'id': r['id'], 'status': r['status'], 'node_id': r['node_id'], 'metrics': r['metrics'], 'error': r['error']} for r in [actual_completed, actual_failed]],
                    'analysis_reports': analyses, 'provider_requests': requests, 'graph_unchanged': True}
        output = ROOT / 'docs/ui-redesign/evidence/analysis-worker-smoke.json'
        output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(json.dumps({'status': 'PASS', 'scopes': 3, 'actual_experiments': 2, 'provider_protocol_requests': len(requests),
                          'live_model_quality': 'NOT RUN', 'literature_searches': [a['literature_searches'] for a in analyses], 'evidence': str(output)}))
    finally:
        if project and os.environ.get('FOREST_RETAIN_ANALYSIS_SMOKE') != '1': api('DELETE', '/projects/' + project['id'])
        if provider:
            receipt = api('POST', '/providers/' + provider['id'] + '/retire', {})
            assert receipt['provider']['status'] == 'retired'
            saved = next(row for row in api('GET', '/providers') if row['id'] == provider['id'])
            assert saved['status'] == 'retired'
            if output:
                evidence['provider_cleanup'] = {'id': provider['id'], 'status': saved['status'],
                    'method': 'POST /providers/{id}/retire followed by actual GET readback',
                    'updated_reference_count': receipt['updated_reference_count'],
                    'reports_current_before_provider_retirement': True,
                    'note': 'Retirement removes the fixture provider from saved project config; retained reports may then be stale.'}
                output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        client.close(); server.shutdown(); server.server_close()


if __name__ == '__main__': run_smoke()
