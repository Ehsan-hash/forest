"""Persisted output-recovery permission gates at the actual runtime boundary.

Each scenario runs in a disposable interpreter and SQLite database. The model
boundary is an httpx.MockTransport, while parsing, accounting, persistence and
run_agent remain the real implementations.
"""
from __future__ import annotations

import json
import os
from functools import partial
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_scenario(tmp_path, scenario):
    env = {**os.environ,
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'agent.db'),
           'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                             str(tmp_path), scenario], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['persisted', 'controls', 'debug', 'approval', 'pending'])
def test_output_recovery_permissions(scenario, tmp_path):
    _run_scenario(tmp_path, scenario)


def _ollama_body(action, *, done_reason='stop'):
    return {
        'model': 'offline-recovery-permissions',
        'done': True,
        'done_reason': done_reason,
        'message': {'role': 'assistant', 'content': json.dumps(action)},
        'prompt_eval_count': 80,
        'eval_count': 20,
    }


def _incomplete_body():
    return {
        'model': 'offline-recovery-permissions',
        'done': True,
        'done_reason': 'length',
        'message': {'role': 'assistant',
                    'content': '{"tool":"read_file","arguments":'},
        'prompt_eval_count': 80,
        'eval_count': 20,
    }


def _create_run(project_tools):
    from services.api.db import Project, Session, TaskRun, uid

    with Session.begin() as session:
        project = Project(name='Output recovery permission fixture',
                          config={'tools': list(project_tools)})
        session.add(project)
        session.flush()
        run = TaskRun(project_id=project.id, request_id=uid(),
                      status='running', kind='agent')
        session.add(run)
        session.flush()
        return project.id, run.id


def _config():
    return {
        'provider_snapshot': {
            'kind': 'ollama',
            'base_url': 'http://127.0.0.1:11434',
            'model': 'offline-recovery-permissions',
            'config': {'max_retries': 0,
                       'adaptive_output_fallback_tokens': 256},
        },
        'execution_backend': 'local',
        'instructions': 'Read input.txt and finish after inspecting its contents.',
    }


def _capture_pending_checkpoint(directory):
    """Run a real truncated response, then interrupt before the next request."""
    import httpx
    import research.agents.provider as provider_module
    import research.agents.runtime as runtime_module
    from research.agents.runtime import run_agent
    from services.api.db import migrate

    migrate()
    project_id, run_id = _create_run(['read_file', 'finish'])
    workspace = directory / 'workspace'
    workspace.mkdir()
    (workspace / 'input.txt').write_text('actual persisted recovery input\n')
    posts = []

    class StopBeforeRecoveryRequest(Exception):
        pass

    def reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=_incomplete_body())

    original_assemble = runtime_module.assemble_model_request
    assembly_calls = 0

    def interrupt_before_second_request(*args, **kwargs):
        nonlocal assembly_calls
        assembly_calls += 1
        if assembly_calls == 2:
            raise StopBeforeRecoveryRequest()
        return original_assemble(*args, **kwargs)

    original_client = provider_module.httpx.Client
    transport = httpx.MockTransport(reply)
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=transport)), \
         patch.object(runtime_module, 'assemble_model_request',
                      interrupt_before_second_request):
        try:
            run_agent(run_id, workspace, _config())
        except StopBeforeRecoveryRequest:
            pass
        else:
            raise AssertionError('checkpoint interruption did not occur before recovery request')

    saved = json.loads((workspace / 'agent_session.json').read_text())
    assert len(posts) == 1
    assert saved['output_recovery']['pending'] is True
    assert saved.get('pending_model_request') is None
    return project_id, run_id, workspace, saved


def _set_project_tools(project_id, tools):
    from services.api.db import Project, Session

    with Session.begin() as session:
        project = session.get(Project, project_id)
        project.config = {**project.config, 'tools': list(tools)}


def _set_human_review_tools(project_id, tools):
    from services.api.db import Project, Session

    with Session.begin() as session:
        project = session.get(Project, project_id)
        project.config = {**project.config, 'human_review_tools': list(tools)}


def _assert_paused_without_request(run_id, workspace, config, expected_reason):
    import httpx
    import research.agents.provider as provider_module
    from research.agents.runtime import AgentYield, run_agent
    from services.api.db import Session, TaskRun

    requests = []

    def unexpected(request):
        requests.append(request)
        raise AssertionError('finish-only recovery attempted a model request')

    original_client = provider_module.httpx.Client
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(unexpected))):
        try:
            run_agent(run_id, workspace, config)
        except AgentYield as error:
            assert error.status == 'waiting_input'
            assert expected_reason in error.reason
        else:
            raise AssertionError('finish-only recovery unexpectedly returned a result')
    assert requests == []
    saved = json.loads((workspace / 'agent_session.json').read_text())
    assert saved['status'] == 'waiting_input'
    assert saved['output_recovery']['pending'] is True
    with Session() as session:
        run = session.get(TaskRun, run_id)
        assert run.resource['blocked_reason'] == 'output_recovery_no_enabled_tools'


def _resume_with_read_and_finish(run_id, workspace, config):
    import httpx
    import research.agents.provider as provider_module
    from research.agents.runtime import run_agent

    actions = [
        {'tool': 'read_file', 'arguments': {'path': 'input.txt'}},
        {'tool': 'finish', 'arguments': {
            'summary': 'Read the actual recovery input.', 'artifacts': ['input.txt']}},
    ]
    posts = []

    def reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(actions[len(posts) - 1]))

    original_client = provider_module.httpx.Client
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(reply))):
        result = run_agent(run_id, workspace, config)
    assert result['artifacts'] == ['input.txt']
    assert len(posts) == 2
    saved = json.loads((workspace / 'agent_session.json').read_text())
    assert saved['status'] == 'completed'
    assert saved['output_recovery']['pending'] is False
    ledger = json.loads((workspace.parent / 'agent_usage.json').read_text())
    assert ledger['run_id'] == saved['run_id']
    assert ledger['accounted_transcript_steps'] == len(saved['transcript'])
    assert ledger['totals']['input_tokens'] == 240
    assert ledger['totals']['output_tokens'] == 60
    return saved, ledger


def _debug_rewrite_scenario(directory):
    import httpx
    import research.agents.debug as debug_module
    import research.agents.provider as provider_module
    import research.agents.runtime as runtime_module
    from research.agents.runtime import run_agent

    project_id, run_id, workspace, _ = _capture_pending_checkpoint(directory)
    _set_project_tools(project_id, ['read_file', 'finish'])
    actions = [
        {'tool': 'read_file', 'arguments': {'path': 'input.txt'}},
        {'tool': 'read_file', 'arguments': {'path': 'input.txt'}},
        {'tool': 'finish', 'arguments': {
            'summary': 'Read the actual recovery input.', 'artifacts': ['input.txt']}},
    ]
    posts = []
    rewritten = False

    def reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(actions[len(posts) - 1]))

    original_client = provider_module.httpx.Client
    original_debug_checkpoint = debug_module.checkpoint

    def rewrite_debug_action(run_id_value, stage, payload, enabled):
        nonlocal rewritten
        if stage == 'before_tool' and not rewritten:
            rewritten = True
            return {'tool': 'finish', 'arguments': {
                'summary': 'debug edit must not finish recovery', 'artifacts': []}}
        return original_debug_checkpoint(run_id_value, stage, payload, enabled)

    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(reply))), \
         patch.object(debug_module, 'checkpoint', rewrite_debug_action):
        result = runtime_module.run_agent(run_id, workspace, _config())
    assert rewritten is True
    assert result['artifacts'] == ['input.txt']
    assert len(posts) == 3
    saved = json.loads((workspace / 'agent_session.json').read_text())
    assert saved['status'] == 'completed'
    ledger = json.loads((workspace.parent / 'agent_usage.json').read_text())
    assert ledger['run_id'] == saved['run_id']
    assert ledger['accounted_transcript_steps'] == len(saved['transcript'])
    assert ledger['totals']['input_tokens'] == 320
    assert ledger['totals']['output_tokens'] == 80


def _approval_edit_scenario(directory):
    import httpx
    import research.agents.provider as provider_module
    import research.agents.runtime as runtime_module
    from research.agents.runtime import AgentYield, run_agent
    from services.api.db import Project, Session, TaskRun, ToolExecution
    from services.interventions.controls import answer_decision
    from services.interventions.models import ActionDecision
    from sqlalchemy import select

    project_id, run_id, workspace, _ = _capture_pending_checkpoint(directory)
    _set_project_tools(project_id, ['read_file', 'finish'])
    _set_human_review_tools(project_id, ['read_file'])

    proposal_posts = []

    def proposal_reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        proposal_posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(
            {'tool': 'read_file', 'arguments': {'path': 'input.txt'}}))

    original_client = provider_module.httpx.Client
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(proposal_reply))):
        try:
            run_agent(run_id, workspace, _config())
        except AgentYield as error:
            assert error.status == 'waiting_input'
            assert error.wait_for.get('decision_id')
        else:
            raise AssertionError('reviewed recovery action did not pause for a decision')
    saved = json.loads((workspace / 'agent_session.json').read_text())
    assert saved['output_recovery']['pending'] is True
    assert saved['pending_action']['tool'] == 'read_file'
    with Session() as session:
        decision = session.scalar(select(ActionDecision).where(ActionDecision.run_id == run_id))
        project_revision = session.get(Project, project_id).revision
        assert decision.status == 'pending'
        assert session.scalar(select(TaskRun).where(TaskRun.id == run_id)).status == 'running'

    accepted = answer_decision(
        decision.id, expected_revision=project_revision, choice='edit',
        action={'tool': 'finish', 'arguments': {
            'summary': 'must not finish before recovery read', 'artifacts': []}})
    assert accepted['status'] == 'accepted'
    # Future reads no longer need review, but the already-saved decision remains
    # the durable action candidate and must be staled by the recovery guard.
    _set_human_review_tools(project_id, [])

    class StopBeforeFinishRequest(Exception):
        pass

    resumed_posts = []
    original_assemble = runtime_module.assemble_model_request
    assembly_calls = 0

    def stop_before_finish_request(*args, **kwargs):
        nonlocal assembly_calls
        assembly_calls += 1
        if assembly_calls == 2:
            raise StopBeforeFinishRequest()
        return original_assemble(*args, **kwargs)

    def resumed_reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        resumed_posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(
            {'tool': 'read_file', 'arguments': {'path': 'input.txt'}}))

    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(resumed_reply))), \
         patch.object(runtime_module, 'assemble_model_request', stop_before_finish_request):
        try:
            run_agent(run_id, workspace, _config())
        except StopBeforeFinishRequest:
            pass
        else:
            raise AssertionError('recovery resumed to a finish request before the actual read')
    assert len(proposal_posts) == 1 and len(resumed_posts) == 1
    assert not (workspace / 'agent_result.json').exists()
    with Session() as session:
        decision = session.get(ActionDecision, decision.id)
        assert decision.status == 'stale'
        reads = list(session.scalars(select(ToolExecution).where(
            ToolExecution.run_id == run_id, ToolExecution.tool == 'read_file')))
        assert len(reads) == 1 and reads[0].status == 'completed'
        assert reads[0].result['content'] == 'actual persisted recovery input\n'
    ledger = json.loads((workspace.parent / 'agent_usage.json').read_text())
    assert ledger['totals']['input_tokens'] == 240
    assert ledger['totals']['output_tokens'] == 60

    finish_posts = []

    def finish_reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        finish_posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(
            {'tool': 'finish', 'arguments': {
                'summary': 'finished after actual recovery read', 'artifacts': ['input.txt']}}))

    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(finish_reply))):
        result = run_agent(run_id, workspace, _config())
    assert result['artifacts'] == ['input.txt']
    assert len(finish_posts) == 1
    ledger = json.loads((workspace.parent / 'agent_usage.json').read_text())
    assert ledger['totals']['input_tokens'] == 320
    assert ledger['totals']['output_tokens'] == 80


def _pending_finish_scenario(directory):
    import httpx
    import research.agents.provider as provider_module
    from research.agents.runtime import AgentYield, run_agent
    from services.api.db import Project, Session, TaskRun
    import uuid

    project_id, run_id, workspace, saved = _capture_pending_checkpoint(directory)
    _set_project_tools(project_id, ['finish'])
    with Session() as session:
        observed_revision = session.get(Project, project_id).revision
    saved['pending_action'] = {
        'id': str(uuid.uuid4()),
        'tool': 'finish',
        'arguments': {'summary': 'stale pending finish', 'artifacts': []},
        'observed_graph_revision': observed_revision,
    }
    (workspace / 'agent_session.json').write_text(json.dumps(saved))
    requests = []

    def unexpected(request):
        requests.append(request)
        raise AssertionError('saved finish action caused a recovery model request')

    original_client = provider_module.httpx.Client
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(unexpected))):
        try:
            run_agent(run_id, workspace, _config())
        except AgentYield as error:
            assert error.status == 'waiting_input'
            assert 'no actionable continuation tool' in error.reason
        else:
            raise AssertionError('saved pending finish completed or returned normally')
    assert requests == []
    resumed = json.loads((workspace / 'agent_session.json').read_text())
    assert resumed['pending_action'] is None
    assert resumed['output_recovery']['pending'] is True
    assert not (workspace / 'agent_result.json').exists()
    with Session() as session:
        assert session.get(TaskRun, run_id).resource['blocked_reason'] == 'output_recovery_no_enabled_tools'


def _persisted_scenario(directory):
    from services.api.db import Session, TaskRun

    project_id, run_id, workspace, saved = _capture_pending_checkpoint(directory)
    config = _config()

    _set_project_tools(project_id, ['finish'])
    _assert_paused_without_request(run_id, workspace, config,
                                   'no actionable continuation tool')
    _assert_paused_without_request(run_id, workspace, config,
                                   'no actionable continuation tool')

    _set_project_tools(project_id, ['read_file', 'finish'])
    final, ledger = _resume_with_read_and_finish(run_id, workspace, config)
    assert len(final['transcript']) == 3
    assert final['transcript'][0]['status'] == 'incomplete'
    assert all(turn.get('usage', {}).get('input_tokens') == 80
               for turn in final['transcript'])
    assert ledger['accounted_transcript_steps'] == 3
    with Session() as session:
        assert session.get(TaskRun, run_id).status == 'running'


def _controls_scenario(directory):
    import httpx
    import research.agents.provider as provider_module
    from research.agents.runtime import AgentYield, run_agent
    from services.api.db import Session, TaskRun, migrate

    migrate()
    config = _config()

    empty_project, empty_run = _create_run([])
    empty_workspace = directory / 'empty-run' / 'workspace'
    empty_workspace.mkdir(parents=True)
    with Session() as session:
        assert session.get(TaskRun, empty_run).resource.get('blocked_reason') is None
    try:
        run_agent(empty_run, empty_workspace, config)
    except AgentYield as error:
        assert error.status == 'waiting_input'
        assert 'No enabled tools' in error.reason
    else:
        raise AssertionError('empty permissions unexpectedly completed')
    with Session() as session:
        assert session.get(TaskRun, empty_run).resource['blocked_reason'] == 'no_enabled_tools'

    finish_project, finish_run = _create_run(['finish'])
    finish_workspace = directory / 'finish-run' / 'workspace'
    finish_workspace.mkdir(parents=True)
    posts = []

    def reply(request):
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {}})
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=_ollama_body(
            {'tool': 'finish', 'arguments': {'summary': 'ordinary completion', 'artifacts': []}}))

    original_client = provider_module.httpx.Client
    with patch.object(provider_module.httpx, 'Client',
                      partial(original_client, transport=httpx.MockTransport(reply))):
        result = run_agent(finish_run, finish_workspace, config)
    assert result['artifacts'] == []
    assert len(posts) == 1
    saved = json.loads((finish_workspace / 'agent_session.json').read_text())
    assert saved['status'] == 'completed'
    ledger = json.loads((finish_workspace.parent / 'agent_usage.json').read_text())
    assert ledger['run_id'] == saved['run_id']
    assert ledger['accounted_transcript_steps'] == 1
    assert ledger['totals']['input_tokens'] == 80
    assert ledger['totals']['output_tokens'] == 20


def _debug_scenario(directory):
    _debug_rewrite_scenario(directory)


if __name__ == '__main__':
    directory, scenario = Path(sys.argv[1]), sys.argv[2]
    if scenario == 'persisted':
        _persisted_scenario(directory)
    elif scenario == 'controls':
        _controls_scenario(directory)
    elif scenario == 'debug':
        _debug_scenario(directory)
    elif scenario == 'approval':
        _approval_edit_scenario(directory)
    elif scenario == 'pending':
        _pending_finish_scenario(directory)
    else:
        raise SystemExit(f'unknown scenario: {scenario}')
