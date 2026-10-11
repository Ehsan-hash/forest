import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.agents import codex_transport
from research.agents.provider import ModelClient, ProviderError


def client(tmp_path, events):
    return ModelClient({'id': 'unit-fixture', 'kind': 'codex_cli', 'base_url': 'http://127.0.0.1',
                        'model': 'fixture-model', 'config': {'reasoning_effort': 'medium',
                        '_forest_transport_trace_path': str(tmp_path/'trace.json')}},
                       request_guard=lambda event: events.append(event) or 'request-fixture')


def run_fixture(monkeypatch, tmp_path, stream, *, returncode=0, stderr=''):
    events = []
    c = client(tmp_path, events)
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/fixture/codex')
    def child(argv, **kwargs):
        assert json.loads((tmp_path/'trace.json').read_text())['state'] == 'prepared'
        return SimpleNamespace(returncode=returncode, stdout=stream, stderr=stderr)
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    return c, events


MESSAGE = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"decision":"real fixture"}'}}
RECEIPT = {'type': 'turn.completed', 'usage': {'input_tokens': 17, 'output_tokens': 4, 'cached_input_tokens': 0}}


def stream(*events):
    return '\n'.join(json.dumps(event) for event in events)


def test_trace_preserves_actual_raw_stream_and_schema(monkeypatch, tmp_path):
    c, events = run_fixture(monkeypatch, tmp_path, stream(MESSAGE, RECEIPT), stderr='fixture diagnostic')
    c.config['_forest_action_schema'] = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    result = c.complete([{'role': 'user', 'content': 'fixture'}])
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert trace['stdout'] == stream(MESSAGE, RECEIPT)
    assert trace['stderr'] == 'fixture diagnostic'
    assert trace['response_schema'] == c.config['_forest_action_schema']
    assert 'env' not in trace
    assert result['transport_trace_path'] == str(tmp_path/'trace.json')
    assert [event['phase'] for event in events] == ['before', 'after']


@pytest.mark.parametrize('payload', [
    stream(MESSAGE),
    stream(MESSAGE, {'type': 'turn.failed', 'error': {'code': 'failure'}}),
    stream(MESSAGE, RECEIPT, {'type': 'turn.failed', 'error': 'failed'}),
    stream(None, MESSAGE, RECEIPT),
    stream([], MESSAGE, RECEIPT),
    stream({'type': 'item.completed', 'item': None}, RECEIPT),
    stream({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': {'not': 'text'}}}, RECEIPT),
    stream(MESSAGE, {'type': 'turn.completed', 'usage': []}),
    stream(MESSAGE, {'type': 'turn.completed', 'usage': {'input_tokens': -1, 'output_tokens': 4}}),
    stream(MESSAGE, {'type': 'turn.completed', 'usage': {'input_tokens': True, 'output_tokens': 4}}),
    stream(MESSAGE, {'type': 'turn.completed', 'usage': {'input_tokens': '17', 'output_tokens': 4}}),
])
def test_fragments_and_malformed_streams_never_complete(monkeypatch, tmp_path, payload):
    c, events = run_fixture(monkeypatch, tmp_path, payload)
    with pytest.raises(ProviderError, match='verified completed'):
        c.complete([{'role': 'user', 'content': 'fixture'}])
    assert json.loads((tmp_path/'trace.json').read_text())['stdout'] == payload
    assert len(events) == 2
    assert events[-1]['phase'] in ('after', 'error')


def test_failed_process_preserves_measured_usage(monkeypatch, tmp_path):
    c, events = run_fixture(monkeypatch, tmp_path, stream(MESSAGE, RECEIPT), returncode=1)
    with pytest.raises(ProviderError) as captured:
        c.complete([{'role': 'user', 'content': 'fixture'}])
    assert captured.value.usage['input_tokens'] == 17
    assert captured.value.ambiguous is False
    assert events[-1]['status'] == 'failed'
    assert captured.value.partial_output == [{'type': 'output_text', 'text': MESSAGE['item']['text']}]


def test_explicit_input_rejection_is_distinct_from_model_output(monkeypatch, tmp_path):
    diagnostic = {'input_error_code': 'input_too_large', 'max_chars': 1048576, 'actual_chars': 5492757}
    stderr = 'Error: turn/start: turn/start failed: Input exceeds maximum length. (code -32602), data: ' + json.dumps(diagnostic)
    c, events = run_fixture(monkeypatch, tmp_path, stream({'type': 'thread.started', 'thread_id': 'fixture'}), returncode=1, stderr=stderr)
    with pytest.raises(ProviderError) as captured:
        c.complete([{'role': 'user', 'content': 'fixture'}])
    assert captured.value.code == 'input_too_large'
    assert captured.value.transport_diagnostics == diagnostic
    assert captured.value.usage is None
    assert events[-1]['ambiguous'] is False
    assert json.loads((tmp_path/'trace.json').read_text())['stderr'] == stderr


def test_timeout_partial_bytes_survive_cleanup(monkeypatch, tmp_path):
    events = []
    c = client(tmp_path, events)
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/fixture/codex')
    def child(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output=b'partial jsonl', stderr=b'local error')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    with pytest.raises(ProviderError) as captured:
        c.complete([{'role': 'user', 'content': 'fixture'}])
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert trace['state'] == 'timed_out'
    assert trace['stdout'] == 'partial jsonl'
    assert trace['stderr'] == 'local error'
    assert captured.value.ambiguous is True
    assert events[-1]['phase'] == 'error'


def test_os_failure_receipt_proves_no_child_started(monkeypatch, tmp_path):
    events = []
    c = client(tmp_path, events)
    monkeypatch.setattr(codex_transport.shutil, 'which', lambda value: '/fixture/codex')
    def child(argv, **kwargs):
        raise FileNotFoundError(2, 'fixture')
    monkeypatch.setattr(codex_transport.subprocess, 'run', child)
    with pytest.raises(ProviderError):
        c.complete([{'role': 'user', 'content': 'fixture'}])
    trace = json.loads((tmp_path/'trace.json').read_text())
    assert trace['state'] == 'not_started'
    assert trace['os_errno'] == 2
    assert events[-1]['ambiguous'] is False
