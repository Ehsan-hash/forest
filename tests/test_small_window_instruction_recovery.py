"""Real local HTTP window rejection and original-control page delivery.

The HTTP endpoint is a transport contract fixture, not a scientific model.
Its successful replies acknowledge received bytes; they establish no research
result, model judgment, or completion claim.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from research.agents.context_store import (
    ContextStore, PAGE_PREFIX, message_chars, recover_context_rejection,
)
from research.agents.output_budget import output_allocation
from research.agents.policy import (
    PAGED_SYSTEM_FIELD, RESEARCH_FREEDOM, ANTI_DEFENSIVE_RESEARCH,
    agent_policy, instruction_metadata, support_agent_config,
)
from research.agents.provider import ModelClient, ProviderError
from research.agents.runtime import assemble_model_request
from research.agents.schemas import tool_definitions
from research.paper.style import REVISION_CONTRACT
from research.planning.model import context_model_json, PROTOCOL
from research.publication import publication_profile


def writer_session(profile='full_submission', *, support=False):
    config = {'role': 'Writer', 'authoring_mode': 'revision',
              'context_char_budget': 64000,
              'instructions': 'Preserve the exact owner instruction: λ local revision.'}
    if support:
        config.update(support_agent_config('forest-paper', 'revision', 'Writer'))
    selected = publication_profile({'publication_profile': profile})
    responsibility = 'Revise only necessary exact original spans and retain sound prose.'
    original = agent_policy('Writer', config, selected) + '\n' + responsibility
    return config, selected, responsibility, {
        'messages': [{'role': 'system', 'content': original},
                     {'role': 'user', 'content': 'Revise the supplied actual text.'}],
        'transcript': [],
    }


def client_config(config, profile, responsibility):
    return {'api': 'chat_completions', 'max_retries': 0,
            **instruction_metadata('Writer', config, profile, responsibility)}


@pytest.mark.parametrize('support', [False, True])
def test_active_policy_is_allocated_and_journaled_before_transport(tmp_path, support):
    config, profile, responsibility, state = writer_session(support=support)
    state['context_recovery_history'] = [{'reason': 'explicit pre-generation rejection'}]
    client = ModelClient({'kind': 'openai', 'model': 'transport-contract',
        'base_url': 'http://127.0.0.1:1/v1', 'config': {
            **client_config(config, profile, responsibility),
            'context_window_tokens': 8192}})
    request, tools = assemble_model_request(state, tmp_path, config, client,
                                           ['read_context_segment', 'finish'])
    active = request[0]['content']
    assert RESEARCH_FREEDOM in active and ANTI_DEFENSIVE_RESEARCH in active
    assert 'name: forest-academic-writing' in active
    assert 'Avoid a single semantic unit forming a paragraph alone.' in active
    assert REVISION_CONTRACT in active and responsibility in active
    assert 'PUBLICATION SCOPE: full_submission' in active
    if support:
        assert 'FOREST SUPPORT WORKFLOW: forest-paper' in active
        assert 'PHASE: revision' in active
    assert tools == ['read_context_segment']
    allocation = output_allocation(client.config, request, requested=1024)
    assert allocation['tokens'] > 0
    _, journal_payload = client.build_request(request, max_output_tokens=allocation['tokens'])
    assert journal_payload['messages'] == [
        {'role': message['role'], 'content': message['content']} for message in request]
    assert state['context_management']['transport_input_characters'] == message_chars(request)
    assert all(PAGED_SYSTEM_FIELD not in message for message in journal_payload['messages'])
    store = ContextStore(tmp_path)
    identifier = store.index['current']['controls:0']
    assert (store.root / (identifier + '.txt')).read_text() == state['messages'][0]['content']
    assert identifier in active and identifier in store.index['required']


@pytest.mark.parametrize('api', ['chat_completions', 'responses'])
def test_real_http_rejection_recovers_and_delivers_every_original_page(tmp_path, api):
    config, profile, responsibility, state = writer_session()
    original_state = copy.deepcopy(state['messages'])
    received = []
    physical_input_bytes = 24576

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(raw)
            messages = payload.get('messages', payload.get('input'))
            size = sum(len(message['content'].encode('utf-8')) for message in messages)
            size += len(json.dumps(payload.get('tools', []), ensure_ascii=False).encode('utf-8'))
            rejected = size > physical_input_bytes
            received.append({'payload': payload, 'input_bytes': size,
                             'rejected_before_generation': rejected})
            if rejected:
                status = 400
                value = {'error': {'code': 'context_length_exceeded',
                                  'message': 'Physical transport input window exceeded before generation'}}
            else:
                status = 200
                value = {'id': f'transport-receipt-{len(received)}',
                    'choices': [{'finish_reason': 'stop', 'message': {
                        'role': 'assistant', 'content': json.dumps({'received_input_bytes': size})}}],
                    'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}
                if api == 'responses':
                    value = {'id': f'transport-receipt-{len(received)}', 'status': 'completed',
                        'output': [{'type': 'message', 'status': 'completed', 'content': [
                            {'type': 'output_text', 'text': json.dumps({'received_input_bytes': size})}]}],
                        'usage': {'input_tokens': 0, 'output_tokens': 0}}
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('x-request-id', f'physical-window-{len(received)}')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ModelClient({'kind': 'openai', 'model': 'transport-contract',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1',
        'config': {**client_config(config, profile, responsibility), 'api': api}})
    successful_pages = {}
    try:
        for _ in range(200):
            request, tools = assemble_model_request(state, tmp_path, config, client,
                                                   ['read_context_segment', 'finish'])
            actual_tools = tool_definitions(tools) if client.native_tools else None
            _, journal_payload = client.build_request(request, tools=actual_tools, max_output_tokens=128)
            try:
                response = client.complete(request, tools=actual_tools, max_output_tokens=128)
            except ProviderError as error:
                assert error.code == 'context_window_exceeded' and not error.ambiguous
                assert error.http_status == 400 and error.request_id.startswith('physical-window-')
                assert recover_context_rejection(state, error)
                continue
            assert received[-1]['payload'] == journal_payload
            assert json.loads(response['text'])['received_input_bytes'] <= physical_input_bytes
            for message in journal_payload.get('messages', journal_payload.get('input')):
                if message['content'].startswith(PAGE_PREFIX):
                    page = json.loads(message['content'][len(PAGE_PREFIX):])
                    successful_pages[page['offset']] = page['content']
                    assert page['authority'] == 'system'
                    assert page['origin'] == 'Original system controls'
            state['context_management']['consecutive_rejections'] = 0
            store = ContextStore(tmp_path)
            pending = store.pending()
            if pending is None:
                assert tools == ['read_context_segment', 'finish']
                break
            assert tools == ['read_context_segment']
            # This is the real editable context reader; the HTTP fixture
            # merely acknowledges bytes and makes no research judgment.
            store.read(pending['segment_id'], pending['offset'])
        else:
            pytest.fail('Original controls did not finish delivery after physical-window recovery')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        (tmp_path / 'received_transport.json').write_text(json.dumps(received, ensure_ascii=False))

    assert received[0]['rejected_before_generation']
    initial_messages = received[0]['payload'].get('messages', received[0]['payload'].get('input'))
    assert initial_messages[0]['content'] == original_state[0]['content']
    assert any(not record['rejected_before_generation'] for record in received[1:])
    assert ''.join(successful_pages[offset] for offset in sorted(successful_pages)) == original_state[0]['content']
    assert state['messages'] == original_state
    store = ContextStore(tmp_path)
    identifier = store.index['current']['controls:0']
    assert (store.root / (identifier + '.txt')).read_text() == original_state[0]['content']
    assert store.index['segments'][identifier]['authority'] == 'system'
    assert store.coverage_characters(identifier) == len(original_state[0]['content'])
    for record in received[1:]:
        actual_messages = record['payload'].get('messages', record['payload'].get('input'))
        active = actual_messages[0]['content']
        assert 'name: forest-academic-writing' in active
        assert 'Avoid a single semantic unit forming a paragraph alone.' in active
        assert RESEARCH_FREEDOM in active and ANTI_DEFENSIVE_RESEARCH in active
        assert identifier in active
        assert all(PAGED_SYSTEM_FIELD not in message for message in actual_messages)
        if api == 'responses':
            assert record['payload']['tools']


@pytest.mark.parametrize('custom_adapter', [False, True])
def test_stage_only_real_provider_archives_and_delivers_its_full_original_policy(tmp_path, custom_adapter):
    received = []
    physical_input_bytes = 24576

    class Transport(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(payload)
            messages = payload['input']
            size = sum(len(message.get('content', '').encode('utf-8'))
                       for message in messages if isinstance(message.get('content', ''), str))
            size += len(json.dumps(payload.get('tools', [])).encode())
            if size > physical_input_bytes:
                status = 400
                value = {'error': {'code': 'context_length_exceeded'}}
            else:
                status = 200
                footer = next((message['content'] for message in messages
                    if message.get('content', '').startswith('CURRENT TASK STATE: ')), None)
                assert footer is not None
                current, _ = json.JSONDecoder().raw_decode(footer[len('CURRENT TASK STATE: '):])
                pending = current['next_required']
                if pending:
                    name = 'read_context_segment'
                    arguments = {'segment_id': pending['segment_id'], 'offset': pending['offset'],
                                 'limit': 6000, 'notes': None}
                else:
                    name, arguments = 'submit_result', {'result_json': '{"transport_fixture":true}'}
                value = {'id': f'context-protocol-{len(received)}', 'status': 'completed',
                    'output': [{'type': 'function_call', 'name': name,
                                'arguments': json.dumps(arguments),
                                'call_id': f'context-call-{len(received)}'}],
                    'usage': {'input_tokens': 0, 'output_tokens': 0}}
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('x-request-id', f'context-window-{len(received)}')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = [{'role': 'system', 'content': 'Retain this exact owner system instruction λ.',
                 'owner_instruction_version': 7},
                {'role': 'user', 'content': 'Revise the provided text and return the requested JSON.'}]
    untouched = copy.deepcopy(original)
    class InheritedProvider(ModelClient):
        """An actual custom adapter inheriting FOREST's request policy."""
        def build_request(self, *args, **kwargs):
            endpoint, payload = super().build_request(*args, **kwargs)
            self.prepared_payloads = getattr(self, 'prepared_payloads', []) + [copy.deepcopy(payload)]
            return endpoint, payload

    provider_class = InheritedProvider if custom_adapter else ModelClient
    client = provider_class({'kind': 'openai', 'model': 'transport-contract',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1',
        'config': {'api': 'responses', 'max_retries': 0}})
    try:
        result, response = context_model_json(client, original, tmp_path,
                                             output_stage='manuscript_revision')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        (tmp_path / 'stage_only_transport.json').write_text(json.dumps(received, ensure_ascii=False))
    assert result == {'transport_fixture': True}  # Protocol acknowledgment, never science.
    assert original == untouched
    if custom_adapter:
        assert client.prepared_payloads == received
    directory = Path(response['context_session_path'])
    archived = json.loads((directory / 'original_messages.json').read_text())
    assert len(archived) == 2 and archived[0]['role'] == 'system' and archived[1] == original[1]
    assert archived[0]['owner_instruction_version'] == 7
    assert archived[0]['content'].endswith(original[0]['content'])
    assert 'name: forest-academic-writing' in archived[0]['content']
    assert 'FULL SUBMISSION CONTRACT' in archived[0]['content']
    assert REVISION_CONTRACT in archived[0]['content']
    assert received[0]['input'][0]['content'] == archived[0]['content']
    assert received[0]['input'][1] == original[1]
    store = ContextStore(directory)
    identifier = store.index['current']['controls:0']
    full_controls = (store.root / (identifier + '.txt')).read_text()
    assert full_controls == archived[0]['content'] + '\n' + PROTOCOL
    assert store.coverage_characters(identifier) == len(full_controls)
    assert store.pending() is None
    delivered = {}
    for payload in received[1:]:
        for message in payload['input']:
            content = message.get('content', '')
            if isinstance(content, str) and content.startswith(PAGE_PREFIX):
                page = json.loads(content[len(PAGE_PREFIX):])
                if page['segment_id'] == identifier:
                    delivered[page['offset']] = page['content']
                    assert page['authority'] == 'system'
    assert ''.join(delivered[offset] for offset in sorted(delivered)) == full_controls
