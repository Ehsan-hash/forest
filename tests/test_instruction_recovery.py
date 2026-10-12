"""Active policy survives an actual context catalog rebase and provider build."""
from research.agents.context_store import pack_context
from research.agents.policy import (
    RESEARCH_POLICY, agent_policy, authoring_mode, instruction_metadata, publication_scope,
    support_agent_config,
)
from research.agents.provider import ModelClient, ProviderError
from research.publication import publication_profile
from research.paper.style import REVISION_CONTRACT
import pytest
import socket


@pytest.mark.parametrize('phase,role,mode', [
    ('draft', 'Writer', 'manuscript'),
    ('revision', 'Writer', 'revision'),
    ('visualization', 'Figure Designer', 'manuscript'),
    ('abstract', 'Writer', 'manuscript'),
])
def test_paper_support_phase_selects_active_writing_mode(phase, role, mode):
    config = support_agent_config('forest-paper', phase, role)
    assert authoring_mode(role, config) == mode
    profile = publication_profile({'publication_profile': 'operational'})
    active = agent_policy(role, config, profile)
    assert 'name: forest-academic-writing' in active
    assert 'Avoid a single semantic unit forming a paragraph alone.' in active
    assert 'PHASE: ' + phase in active
    if mode == 'revision':
        assert 'AUTHORING MODE: MINIMAL REVISION' in active
    for explicit_mode in ('none', 'manuscript', 'revision'):
        override = {**config, 'authoring_mode': explicit_mode}
        assert authoring_mode(role, override) == explicit_mode


@pytest.mark.parametrize('role', ['Writer', 'Manuscript Writer', 'Manuscript Editor',
                                 'Paper Revision Agent', 'Figure Caption Writer'])
def test_writing_aliases_use_the_actual_selected_profile(role):
    assert publication_scope(role, {})
    profile = publication_profile({'publication_profile': 'operational'})
    policy = agent_policy(role, {}, profile)
    assert 'name: forest-academic-writing' in policy
    assert 'Avoid a single semantic unit forming a paragraph alone.' in policy
    assert 'operational scope' in policy
    assert 'FULL SUBMISSION CONTRACT' not in policy
    if role in {'Manuscript Editor', 'Paper Revision Agent'}:
        assert 'MINIMAL-EDIT REVISION' in policy
    assert RESEARCH_POLICY in policy


@pytest.mark.parametrize('support', [False, True])
def test_catalog_recovery_preserves_effective_custom_role_and_workflow(tmp_path, support):
    role = 'Paper Revision Agent'
    config = {'authoring_mode': 'revision', 'credentials': 'must not copy'}
    if support:
        config.update(support_agent_config('forest-paper', 'revision', 'Writer',
            parent_node_id='original-parent', task='Revise the supplied manuscript.'))
        role = config['role']
    profile = publication_profile({'publication_profile': 'operational'})
    responsibility = 'Revise the supplied text with exact original spans and preserve its technical meaning.'
    state = {'messages': [
        {'role': 'system', 'content': agent_policy(role, config, profile) + responsibility},
        {'role': 'user', 'content': 'Revise these original paragraphs.'}],
        'transcript': [], 'context_recovery_history': [{'reason': 'context_window_exceeded'}]}
    packed = pack_context(state, tmp_path, 3000)
    assert 'Original system controls are retained' in packed[0]['content']
    client = ModelClient({'kind': 'ollama', 'model': 'fixture-model',
        'base_url': 'http://127.0.0.1:11434', 'config': {}})
    metadata = instruction_metadata(role, config, profile, responsibility)
    assert 'credentials' not in metadata
    client.config = {**client.config, **metadata}
    _, payload = client.build_request(packed)
    active = payload['messages'][0]['content']
    assert 'name: forest-academic-writing' in active
    assert 'Avoid a single semantic unit forming a paragraph alone.' in active
    assert responsibility in active
    assert 'operational scope' in active
    assert 'FULL SUBMISSION CONTRACT' not in active
    assert RESEARCH_POLICY in active
    if support:
        assert 'FOREST SUPPORT WORKFLOW: forest-paper' in active
        assert 'PHASE: revision' in active


@pytest.mark.parametrize('stage_key', ['output_stage', 'research_stage'])
def test_complete_resolves_the_configured_stage_before_first_policy_injection(stage_key):
    # The real transport connects to a reserved non-listening local socket.
    # Capture its actual payload; no successful response or model is invented.
    class RecordingClient(ModelClient):
        def build_request(self, *args, **kwargs):
            endpoint, payload = super().build_request(*args, **kwargs)
            self.actual_request = payload
            return endpoint, payload
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        client = RecordingClient({'kind': 'openai', 'model': 'transport-contract',
            'base_url': f'http://127.0.0.1:{listener.getsockname()[1]}',
            'config': {'api': 'chat_completions', 'max_retries': 0,
                       stage_key: 'manuscript_revision', 'publication_profile': 'operational'}})
        with pytest.raises(ProviderError) as caught:
            client.complete([{'role': 'user', 'content': 'Revise the provided text.'}], max_output_tokens=100)
        assert caught.value.code == 'transport_error'
    active = client.actual_request['messages'][0]['content']
    assert 'name: forest-academic-writing' in active
    assert 'MINIMAL-EDIT REVISION' in active
    assert 'ROLE RESPONSIBILITY: Write the strongest supported contribution' in active
    assert 'operational scope' in active


@pytest.mark.parametrize('role,mode,support,stage,revision', [
    ('Paper Revision Agent', None, False, 'paper', True),
    ('Manuscript Editor', None, False, 'paper', True),
    ('Writer', None, True, 'paper', True),
    ('Writer', None, False, 'manuscript_revision', True),
    ('Paper Revision Agent', 'manuscript', False, 'paper', False),
    ('Paper Revision Agent', 'none', False, 'paper', False),
])
def test_recovered_real_payload_preserves_revision_mode_precedence(tmp_path, role, mode, support, stage, revision):
    class RecordingClient(ModelClient):
        def build_request(self, *args, **kwargs):
            endpoint, payload = super().build_request(*args, **kwargs)
            self.actual_request = payload
            return endpoint, payload

    config = {} if mode is None else {'authoring_mode': mode}
    if support:
        config.update(support_agent_config('forest-paper', 'revision', 'Writer'))
    profile = publication_profile({'publication_profile': 'operational'})
    original = agent_policy(role, config, profile)
    state = {'messages': [{'role': 'system', 'content': original},
                          {'role': 'user', 'content': 'Revise the supplied text.'}],
             'transcript': [], 'context_recovery_history': [{'reason': 'context_window_exceeded'}]}
    packed = pack_context(state, tmp_path, 3000)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        client = RecordingClient({'kind': 'openai', 'model': 'transport-contract',
            'base_url': f'http://127.0.0.1:{listener.getsockname()[1]}', 'config': {
                'api': 'chat_completions', 'max_retries': 0, 'research_stage': stage,
                **instruction_metadata(role, config, profile)}})
        _, journal_payload = client.build_request(packed, max_output_tokens=100)
        with pytest.raises(ProviderError) as caught:
            client.complete(packed, max_output_tokens=100)
        assert caught.value.code == 'transport_error'
    assert client.actual_request == journal_payload
    active = client.actual_request['messages'][0]['content']
    assert (REVISION_CONTRACT in active) is revision
    assert ('name: forest-academic-writing' in active) is (mode != 'none')
