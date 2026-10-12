"""Real chunk/file/process/ledger checks and offline protocol contracts.

No provider output is substituted and no model/network success is claimed.
The transport check uses an actual OS connection refusal. Incomplete-response
objects below are parser inputs, not simulated research or model execution.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.agents.code_writes import CODE_CHUNK_CHARS, write_file_chunk
from research.agents.output_recovery import account_usage, model_turn_tools, prepare_output_recovery, record_provider_failure
from research.agents.provider import ProviderError, parse_response, responses_input
from research.agents.runtime import budget_reason
from research.agents.schemas import decode_tool_call

ROOT = Path(__file__).resolve().parents[1]


def empty_state():
    return {'status': 'executing', 'transcript': [], 'messages': [], 'pending_action': None,
            'totals': {'cost': None}, 'cost_complete': True, 'active_seconds': 0}


def incomplete_error():
    # Values follow the API's public incomplete-response contract. Never a
    # returned model fixture: directly invoke only the parser/error handler.
    body = {'id': 'contract-response', 'model': 'contract-input', 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'usage': {'input_tokens': 12826, 'input_tokens_details': {'cached_tokens': 10880},
                      'output_tokens': 16384, 'output_tokens_details': {'reasoning_tokens': 5238}},
            'output': [{'type': 'reasoning', 'encrypted_content': 'private-opaque-input'},
                       {'type': 'function_call', 'status': 'incomplete', 'name': 'write_file',
                        'call_id': 'contract-call', 'arguments': '{"path":"never.py","content":"unfinished'}]}
    try:
        parse_response(body, 'responses', request_id='contract-request',
                       pricing={'input_per_million': 2.5, 'cached_input_per_million': .25, 'output_per_million': 15})
    except ProviderError as error:
        return error
    raise AssertionError('An incomplete call was accepted')


def test_incomplete_usage_and_one_charged_recovery_are_retained():
    state = empty_state()
    account_usage(state, {'input_tokens': 3, 'output_tokens': 2, 'cost': .05}, 1)
    error = incomplete_error()
    turn = record_provider_failure(state, error, 223.577, 'contract-input')
    assert turn['status'] == 'incomplete' and not turn['tool_executed']
    assert turn['tool_calls'] == [] and state['pending_action'] is None
    assert state['totals']['cost'] == pytest.approx(.303345)
    assert state['totals']['output_tokens'] == 16386
    assert state['totals']['reasoning_tokens'] == 5238
    assert state['active_seconds'] == pytest.approx(224.577)
    assert 'private-opaque-input' not in json.dumps(turn)
    assert turn['incomplete_public_output'][0]['arguments'].endswith('unfinished')
    allowed = ['read_file', 'write_file', 'write_file_chunk', 'finish']
    assert prepare_output_recovery(state, error, {}, allowed)
    assert state['output_recovery']['strategy'] == 'split_into_smaller_actions'
    assert set(model_turn_tools(state, allowed)) == {'read_file', 'write_file', 'write_file_chunk'}
    assert not any(item.get('type') == 'function_call' for item in responses_input(state['messages']))
    assert budget_reason(state, {'agent_budget': {'cost': .3}})
    assert budget_reason(state, {'agent_budget': {'steps': 1}}) is None
    assert budget_reason(state, {'agent_budget': {'active_seconds': 200}})
    resumed = json.loads(json.dumps(state))
    assert prepare_output_recovery(resumed, error, {}, allowed)
    assert resumed['output_recovery']['used'] == 2
    assert not prepare_output_recovery(resumed, error, {}, allowed)
    assert not prepare_output_recovery(empty_state(), error, {'output_recovery_attempts': 0}, allowed)


def test_ambiguous_failure_and_unknown_usage_never_become_free_retries():
    state = empty_state()
    account_usage(state, {'input_tokens': 100, 'output_tokens': 30, 'cost': .075}, 1)
    error = ProviderError('Unknown outcome', code='transport_error', ambiguous=True)
    record_provider_failure(state, error, 120, 'contract-input')
    assert state['totals']['cost'] is None
    assert state['totals']['known_cost'] == .075
    assert state['cost_complete'] is False and state['usage_complete'] is False
    assert state['active_seconds'] == 121
    assert not prepare_output_recovery(state, error, {}, ['write_file_chunk'])
    assert 'output_recovery' not in state
    assert not prepare_output_recovery(empty_state(), incomplete_error(), {}, ['finish'])


def test_chunk_bounds_validate_native_and_real_writes(tmp_path):
    content = 'x' * (CODE_CHUNK_CHARS + 1)
    call = {'name': 'write_file_chunk', 'call_id': 'local-contract',
            'arguments': {'path': 'source.py', 'offset': 0, 'content': content}}
    with pytest.raises(ValueError, match='pattern or size'):
        decode_tool_call(call, ['write_file_chunk'])
    with pytest.raises(ValueError, match='characters'):
        write_file_chunk(tmp_path / 'source.py', content, 0)
    assert not (tmp_path / 'source.py').exists()
    for name, arguments in [('write_file', {'path': 'source.py', 'content': content}),
                             ('python', {'path': None, 'code': content, 'cwd': None, 'env_json': None, 'timeout': None})]:
        with pytest.raises(ValueError, match='pattern or size'):
            decode_tool_call({'name': name, 'arguments': arguments, 'call_id': 'local-contract'}, [name])
    for offset in (True, -1, 0.5):
        with pytest.raises(ValueError, match='byte offset'):
            write_file_chunk(tmp_path / 'source.py', 'print(1)', offset)


@pytest.mark.parametrize('scenario', ['chunks', 'transport', 'budget', 'dynamic-steps', 'errors'])
def test_actual_recovery_components(tmp_path, scenario):
    env = {**os.environ, 'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'recovery.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'), 'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, __file__, str(tmp_path), scenario], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=600 if scenario=='dynamic-steps' else 30)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import socket
    import time
    from sqlalchemy import select
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from services.api.db import migrate, Session, Project, TaskRun, ToolExecution, Provider, ModelRequest, asdict, uid
    from research.agents.runtime import AgentYield, ToolRuntime, run_agent
    from research.agents.budget import BudgetExceeded, make_request_guard

    directory, scenario = Path(sys.argv[1]), sys.argv[2]
    migrate()
    with Session.begin() as s:
        project = Project(name='Offline recovery component validation', budget={'allow_paid': True, 'cost_usd': 10})
        s.add(project); s.flush()
        run = TaskRun(project_id=project.id, request_id=uid(), status='running', kind='agent')
        s.add(run); s.flush()
        project_id, run_id = project.id, run.id
    workspace = directory / 'workspace'; workspace.mkdir()

    if scenario == 'dynamic-steps':
        progress={'turn':0,'writes':0,'offset':0}
        class Transport(BaseHTTPRequestHandler):
            def log_message(self,*_args): pass
            def do_POST(self):
                size=int(self.headers.get('Content-Length','0')); request=json.loads(self.rfile.read(size))
                if self.path=='/api/show':
                    value={'model_info':{'test.context_length':8192}}
                else:
                    progress['turn']+=1
                    pending=None
                    for message in request['messages']:
                        content=message.get('content','')
                        if content.startswith('AUTOMATIC CONTEXT:') and '{"next_required"' in content:
                            pending=json.loads(content[content.index('{"next_required"'):])['next_required']
                    index=progress['writes']
                    if pending:
                        action={'tool':'read_context_segment','arguments':{'segment_id':pending['segment_id'],'offset':pending['offset'],'limit':6000}}
                    elif index<401:
                        chunk=f'def value_{index}():\n    return {index} * {index}\n\n'
                        if index==400:
                            chunk+="print(sum(globals()['value_'+str(i)]() for i in range(401)))\n"
                        action={'tool':'write_file_chunk','arguments':{'path':'source.py','content':chunk,'offset':progress['offset']}}
                        progress['offset']+=len(chunk.encode())
                        progress['writes']+=1
                    else:
                        action={'tool':'finish','arguments':{'summary':'Required source generated through bounded work rounds.','artifacts':['source.py']}}
                    value={'model':'step-budget-integration-fixture','done':True,'done_reason':'stop',
                           'message':{'role':'assistant','content':json.dumps(action)},
                           'prompt_eval_count':100,'eval_count':30}
                response=json.dumps(value).encode(); self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(response))); self.end_headers(); self.wfile.write(response)
        server=ThreadingHTTPServer(('127.0.0.1',0),Transport)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            provider=Provider(name='Local dynamic step fixture',kind='ollama',base_url=f'http://127.0.0.1:{server.server_port}',
                              model='step-budget-fixture',allow_paid=False,config={'timeout':5,'adaptive_output_fallback_tokens':256})
            with Session.begin() as s:
                s.add(provider); s.flush(); snapshot=asdict(provider,True)
            config={'provider_snapshot':snapshot,'required_outputs':['source.py'],
                'instructions':'Implement the required Python source with independent squared-value functions and a computed sum, then finish.'}
            boundaries=[]
            while True:
                try:
                    result=run_agent(run_id,workspace,config)
                    break
                except AgentYield as error:
                    assert error.status=='waiting' and error.wait_for['type']=='work_round'
                    checkpoint=json.loads((workspace/'agent_session.json').read_text())
                    assert checkpoint['status']=='waiting'
                    boundaries.append(checkpoint['lifecycle']['round_index'])
                    assert 'totals' not in checkpoint and 'active_seconds' not in checkpoint
            saved=json.loads((workspace/'agent_session.json').read_text())
            assert saved['status']=='completed'
            assert progress['writes']==401 and progress['turn']==len(saved['transcript'])>=402
            assert boundaries==list(range(1,len(boundaries)+1)) and len(boundaries)>=13
            assert saved['lifecycle']['round_index']==len(boundaries)+1
            assert 'dynamic_step_budget' not in saved
            assert saved['result']['artifacts']==['source.py']
            executed=subprocess.run([sys.executable,'source.py'],cwd=workspace,capture_output=True,text=True,timeout=5)
            assert executed.returncode==0 and int(executed.stdout)==sum(i*i for i in range(401))
            ledger=json.loads((workspace.parent/'agent_usage.json').read_text())
            assert ledger['run_id']==run_id and ledger['accounted_transcript_steps']==progress['turn']
            assert ledger['totals']['input_tokens']==progress['turn']*100 and ledger['totals']['output_tokens']==progress['turn']*30
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        print('Actual protocol/executor chain crossed 150/400 turns, resumed 13 saved rounds under one Run ID, generated executable source and retained independent accounting')

    elif scenario == 'errors':
        requests=[]
        class Transport(BaseHTTPRequestHandler):
            def log_message(self,*_args): pass
            def do_POST(self):
                size=int(self.headers.get('Content-Length','0')); request=json.loads(self.rfile.read(size))
                if self.path=='/api/show':
                    value={'model_info':{}}
                else:
                    requests.append(request)
                    action={'tool':'read_file','arguments':{'path':'missing-input.csv'}}
                    value={'model':'error-circuit-fixture','done':True,'done_reason':'stop',
                           'message':{'role':'assistant','content':json.dumps(action)},
                           'prompt_eval_count':80,'eval_count':20}
                response=json.dumps(value).encode(); self.send_response(200)
                self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(response)))
                self.end_headers(); self.wfile.write(response)
        server=ThreadingHTTPServer(('127.0.0.1',0),Transport)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            provider=Provider(name='Local error circuit fixture',kind='ollama',base_url=f'http://127.0.0.1:{server.server_port}',
                              model='error-circuit-fixture',allow_paid=False,config={'timeout':5})
            with Session.begin() as s:
                s.add(provider); s.flush(); snapshot=asdict(provider,True)
            config={'provider_snapshot':snapshot,'execution_backend':'local',
                'agent_budget':{'steps':10},'instructions':'Inspect the input and continue only when the authorized file exists.'}
            yields=[]
            while True:
                try:
                    run_agent(run_id,workspace,config)
                except AgentYield as error:
                    yields.append(error.status)
                    if error.status=='waiting_input': break
                    assert error.status=='waiting' and error.wait_for['type']=='work_round'
                else:
                    raise AssertionError('Repeated missing-file errors did not pause for owner diagnosis')
            saved=json.loads((workspace/'agent_session.json').read_text())
            history=saved['error_recovery']['history']
            assert len(requests)==4
            assert [item['state'] for item in history[:3]]==[
                'observed','diagnosis_required','blocked_after_failed_diagnosis']
            assert any('ERROR RECOVERY REQUIRED' in json.dumps(request) for request in requests[2]['messages'])
            assert saved['status']=='waiting_input'
            with Session() as s:
                errors=list(s.scalars(select(ToolExecution).where(ToolExecution.run_id==run_id)))
                assert len(errors)==2
            assert yields[-1]=='waiting_input'
            assert [decision['action'] for decision in saved['lifecycle']['decisions']]==['diagnose','diagnose','pause']
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        print('Actual agent run diagnosed a repeated same-class tool error and paused after the repeated failed diagnosis')

    elif scenario == 'chunks':
        tool = ToolRuntime(run_id, workspace, ['write_file', 'read_file', 'start_process'])
        assert 'write_file_chunk' in tool.allowed
        assert 'write_file_chunk' not in ToolRuntime(run_id, workspace, ['read_file']).allowed
        first = '# UTF-8 byte offsets: π\nimport json\n'
        arguments = {'path': 'large_program.py', 'content': first, 'offset': 0}
        action_id = uid()
        # Interrupt after the real atomic file write but before a completion
        # receipt is saved. Replay the actual persisted intent, without a model.
        with Session.begin() as s:
            s.add(ToolExecution(id=action_id, run_id=run_id, tool='write_file_chunk', arguments=arguments))
        observed = tool.dispatch('write_file_chunk', arguments, project_id, action_id)
        replayed = tool.execute('write_file_chunk', arguments, action_id)
        assert replayed['replayed'] and observed['next_offset'] == len(first.encode())
        assert (workspace / 'large_program.py').read_text() == first
        offset = replayed['next_offset']
        source = first
        for index in range(150):
            chunk = f'def value_{index}():\n    return {index} * {index}\n\n'
            result = tool.execute('write_file_chunk', {'path': 'large_program.py', 'content': chunk, 'offset': offset})
            assert result['exit_code'] == 0
            offset = result['next_offset']; source += chunk
        last = "values = [" + ','.join(f'value_{index}()' for index in range(150)) + "]\nprint(json.dumps({'sum':sum(values),'count':len(values)}))\n"
        result = tool.execute('write_file_chunk', {'path': 'large_program.py', 'content': last, 'offset': offset})
        source += last
        assert len(source) > CODE_CHUNK_CHARS and (workspace / 'large_program.py').read_text() == source
        # A different intent must not append at a stale offset or overwrite a user edit.
        conflict = tool.execute('write_file_chunk', arguments)
        assert conflict['exit_code'] == 1 and (workspace / 'large_program.py').read_text() == source
        escape = tool.execute('write_file_chunk', {'path': '../escape.py', 'content': 'bad', 'offset': 0})
        assert escape['exit_code'] == 1 and not (directory / 'escape.py').exists()
        launched = tool.execute('start_process', {'command': [sys.executable, 'large_program.py']})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            actual = tool.processes.inspect(launched['process_id'])
            if actual['status'] in ('completed', 'failed', 'lost', 'cancelled'):
                break
            time.sleep(.03)
        assert actual['status'] == 'completed' and actual['exit_code'] == 0, actual
        output = json.loads(tool.processes.read_output(launched['process_id'])['content'])
        assert output == {'sum': sum(i*i for i in range(150)), 'count': 150}
        print('152 real chunks -> editable Python source -> actual interpreter result; crash replay and path/offset checks passed')

    elif scenario == 'transport':
        with socket.socket() as held:
            held.bind(('127.0.0.1', 0))
            config = {'provider_snapshot': {'kind': 'openai', 'base_url': f'http://127.0.0.1:{held.getsockname()[1]}/v1',
                      'model': 'no-model-server', 'config': {'api': 'responses', 'max_retries': 0, 'timeout': .2}},
                      'instructions': 'No remote request is authorized.'}
            outcomes=[]
            while True:
                try:
                    run_agent(run_id, workspace, config)
                except AgentYield as error:
                    outcomes.append(error.status)
                    if error.status=='waiting_input': break
                    assert error.status=='waiting' and error.wait_for['type']=='work_round'
                    checkpoint=json.loads((workspace/'agent_session.json').read_text())
                    assert checkpoint['transcript'][-1]['error']['code']=='transport_error'
                else:
                    raise AssertionError('An OS connection refusal was accepted')
        saved = json.loads((workspace / 'agent_session.json').read_text())
        ledger=json.loads((workspace.parent/'agent_usage.json').read_text())
        assert saved['status'] == 'waiting_input' and ledger['active_seconds'] > 0
        assert len(saved['transcript']) == 3 and saved['pending_action'] is None
        assert saved['transcript'][0]['error']['code'] == 'transport_error'
        assert saved['transcript'][0]['usage'] is None and ledger['totals']['cost'] is None
        assert 'output_recovery' not in saved
        assert outcomes==['waiting','waiting','waiting_input']
        assert saved['lifecycle']['state']=='blocked'
        print('Actual connection refusal checkpointed bounded diagnosis and paused without new evidence; independent ledger retains unknown usage and actual elapsed time')

    elif scenario == 'budget':
        pricing = {'input_per_million': 1, 'output_per_million': 1, 'cached_input_per_million': .1, 'currency': 'USD'}
        with Session.begin() as s:
            s.get(TaskRun, run_id).config = {'agent_budget': {'cost': .004198}}
            provider = Provider(name='Ledger only; no HTTP', kind='openai', base_url='https://api.openai.com/v1',
                                model='ledger-input', allow_paid=True, config={'budget_usd': 10, 'pricing': pricing})
            s.add(provider); s.flush(); value = asdict(provider, True)
        guard = make_request_guard({**value, '_usage_context': {'project_id': project_id, 'run_id': run_id}})
        before = {'phase': 'before', 'model': 'ledger-input', 'pricing': pricing, 'input_bytes': 0, 'max_output_tokens': 1}
        first = guard(before)
        guard({'phase': 'after', 'reservation': first, 'usage': {'input_tokens': 100, 'output_tokens': 0},
               'status': 'incomplete', 'incomplete_reason': 'max_output_tokens'})
        uncertain = guard(before)
        guard({'phase': 'error', 'reservation': uncertain, 'ambiguous': True})
        final = guard(before)  # Exactly 100 + 2049 + 2049 micro-USD.
        try:
            guard(before)
        except BudgetExceeded as error:
            assert 'Agent API spending limit' in str(error)
        else:
            raise AssertionError('Continuation bypassed the original run cost budget')
        with Session() as s:
            rows = list(s.scalars(select(ModelRequest).where(ModelRequest.run_id == run_id)))
            assert len(rows) == 3
            assert s.get(ModelRequest, first).details['response_status'] == 'incomplete'
            assert s.get(ModelRequest, uncertain).status == 'uncertain'
            assert s.get(ModelRequest, final).status == 'reserved'
        print('Original run budget counts received incomplete usage and uncertain reservations before allowing a new request')
