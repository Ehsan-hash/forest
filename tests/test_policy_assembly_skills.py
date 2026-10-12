from pathlib import Path

import pytest

from research.agents.policy import (
    ANTI_DEFENSIVE_RESEARCH,
    RESEARCH_FREEDOM,
    RESEARCH_POLICY_MARKER,
    ROLES,
    VERSION_EIGHT_ROLE_INSTRUCTIONS,
    agent_policy,
    ensure_research_messages,
    support_agent_config,
    workflow_dispatch_options,
    workflow_instructions,
)
from research.paper.style import PRESS_RELEASE_WRITING, writing_contract


SKILLS = Path(__file__).resolve().parents[1] / '.agents' / 'skills'


def test_project_skills_are_discoverable_and_link_to_operational_sources():
    expected = {
        'forest-autonomous-research',
        'forest-experimentation',
        'forest-research-judgment',
        'forest-academic-writing',
    }
    assert {path.name for path in SKILLS.iterdir()} == expected
    for name in expected:
        text = (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')
        packaged = (Path(__file__).resolve().parents[1] / 'research' / 'agents' / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')
        assert text.startswith('---\nname: ' + name + '\n')
        assert text == packaged
        assert 'description:' in text.split('---', 2)[1]
        assert len(text.split('---', 2)[2].strip()) > 300
    writing = (SKILLS / 'forest-academic-writing' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'Avoid a single semantic unit forming a paragraph alone.' in writing
    for title in (
        'Single strongest advantage', 'No process chronology',
        'No self-weakening language', 'Choose the correct scientific comparison',
        'Handle unfavorable material through scientific framing',
        'Explicit advantage statements', 'Experimental argumentative duty',
        'Abstract and introduction', 'Conclusion',
    ):
        assert 'Rule ' in writing and title in writing
    assert '## Editorial Priority' in writing
    for step in (
        'Remove irrelevant material.', 'Narrow the scientific claim.',
        'Choose the appropriate evaluation dimension.', 'Explain the observed trade-off.',
        'Reorganize the experimental presentation.', 'Revise the central scientific story.',
    ):
        assert step in writing
    for step in (
        'Quote the exact original span.', 'Explain the concrete writing problem.',
        'Provide the smallest necessary replacement.', 'Preserve the author\'s wording habits.',
        'Preserve paragraph structure.', 'Leave problem-free text unchanged.',
    ):
        assert step in writing
    assert 'MINIMAL-EDIT REVISION' in PRESS_RELEASE_WRITING


def test_registered_roles_receive_shared_policies_and_role_applicable_skills():
    for role in ROLES:
        instructions = agent_policy(role, {}, None)
        assert RESEARCH_POLICY_MARKER in instructions
        assert RESEARCH_FREEDOM in instructions
        assert ANTI_DEFENSIVE_RESEARCH in instructions
        assert 'forest-autonomous-research' in instructions
    assert 'forest-experimentation' in agent_policy('Experimenter', {}, None)
    assert 'forest-research-judgment' in agent_policy('Researcher', {}, None)
    writer = agent_policy('Writer', {}, None)
    assert 'forest-academic-writing' in writer
    assert 'Avoid a single semantic unit forming a paragraph alone.' in writer
    assert 'Avoid a single semantic unit forming a paragraph alone.' in writing_contract()
    engineer = agent_policy('Engineer', {}, None)
    assert 'Avoid a single semantic unit forming a paragraph alone.' not in engineer
    explicit_writing_role = agent_policy('New Manuscript Editor', {'authoring_mode': 'manuscript'}, None)
    assert 'Avoid a single semantic unit forming a paragraph alone.' in explicit_writing_role
    assert 'Avoid a single semantic unit forming a paragraph alone.' not in agent_policy('Writer', {'authoring_mode': 'none'}, None)


def test_current_direction_review_removes_the_old_fixed_counterexample_quota():
    historical = VERSION_EIGHT_ROLE_INSTRUCTIONS['Research Direction Reviewer']
    current = agent_policy('Research Direction Reviewer', {}, None)
    assert 'default two' in historical
    assert 'default two' not in ROLES['Research Direction Reviewer'].lower()
    assert 'default two' not in current.lower()
    assert 'fixed count of checks' not in current.lower()


def test_dynamic_and_recovered_requests_inherit_once_without_mutating_history():
    source = [
        {'role': 'system', 'content': 'Continue the recovered task.'},
        {'role': 'user', 'content': 'Inspect the new evidence.'},
    ]
    prepared = ensure_research_messages(source, {'role': 'New Supporting Analyst'}, stage='analysis')
    assert source[0]['content'] == 'Continue the recovered task.'
    assert prepared[0]['role'] == 'system'
    assert RESEARCH_POLICY_MARKER in prepared[0]['content']
    assert 'ROLE RESPONSIBILITY: Advance the current research goal' in prepared[0]['content']
    again = ensure_research_messages(prepared, {'role': 'New Supporting Analyst'}, stage='analysis')
    assert sum(RESEARCH_POLICY_MARKER in message.get('content', '') for message in again if message['role'] == 'system') == 1
    assert len(again) == len(prepared)
    assert 'forest-research-judgment' in prepared[0]['content']


@pytest.mark.parametrize('stage,expected_role', [
    ('paper_generate', 'Writer'), ('paper_drafting', 'Writer'),
    ('manuscript_revision', 'Writer'), ('figure_design', 'Figure Designer'),
    ('visual_review', 'Visual Selector'),
])
def test_output_stages_select_the_manuscript_policy(stage, expected_role):
    from research.agents.policy import _STAGE_ROLE
    assert _STAGE_ROLE[stage] == expected_role
    prompt = ensure_research_messages([{'role': 'user', 'content': 'Prepare a publication artifact.'}], stage=stage)[0]['content']
    assert 'Avoid a single semantic unit forming a paragraph alone.' in prompt
    if stage == 'figure_design':
        assert 'figure or caption' in prompt.lower() or 'layout' in prompt.lower()


def test_unknown_roles_receive_the_project_judgment_skill():
    prompt = agent_policy('Project Specific Research Role', {}, None)
    assert 'forest-research-judgment' in prompt
    assert 'BEST ESTIMATE' in prompt


def test_workflow_dispatch_resolves_real_support_roles_and_phase_instructions():
    options = workflow_dispatch_options()
    assert len(options) >= 20
    assert all(item['support_role'] in ROLES for item in options)
    config = support_agent_config(
        'forest-experimentation', 'execute', 'Experimenter',
        parent_node_id='parent-17', task='Run the declared comparison',
    )
    assert config['role'] == config['agent_support_role'] == 'Experimenter'
    assert config['agent_workflow_id'] == 'forest-experimentation'
    assert config['agent_workflow_phase'] == 'execute'
    assert config['parent_node_id'] == 'parent-17'
    assert 'Run the declared scientific computation' in config['instructions']
    request = ensure_research_messages(
        [{'role': 'user', 'content': config['task']}], config, stage='support_dispatch'
    )
    assert RESEARCH_POLICY_MARKER in request[0]['content']
    assert 'FOREST SUPPORT WORKFLOW: forest-experimentation' in request[0]['content']
    assert 'PHASE: execute' in request[0]['content']
    assert 'ROLE RESPONSIBILITY:' in request[0]['content']
    assert sum('FOREST SUPPORT WORKFLOW:' in message.get('content', '')
               for message in request if message['role'] == 'system') == 1
    with pytest.raises(ValueError, match='Unsupported workflow'):
        workflow_instructions('forest-experimentation', 'execute', 'Reviewer')
