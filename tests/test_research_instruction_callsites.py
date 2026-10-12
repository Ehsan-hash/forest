import json

from PIL import Image

from research.agents.policy import RESEARCH_POLICY_MARKER


def assert_writing_policy(system, role=None):
    assert RESEARCH_POLICY_MARKER in system
    assert 'forest-academic-writing' in system
    assert 'Avoid a single semantic unit forming a paragraph alone.' in system
    if role:
        assert 'ROLE RESPONSIBILITY:' in system


class CapturingClient:
    api = 'ollama'

    def __init__(self, response='{}'):
        self.config = {}
        self.response = response
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {'text': self.response, 'model': 'scripted-callsite-fixture', 'usage': {}}


def test_storyboard_generation_assembles_the_figure_writing_skill(tmp_path, monkeypatch):
    import research.figures.narrative as narrative
    from research.figures.model_workflow import design_storyboard

    monkeypatch.setattr(narrative, 'story_design_request', lambda context, mode: {
        'instruction': 'Create a grounded storyboard.', 'prompt': '{}',
    })
    monkeypatch.setattr(narrative, 'validate_storyboard', lambda story, context, mode: {'status': 'fixture'})
    client = CapturingClient()

    assert design_storyboard(client, {}, tmp_path, mode='method_only') == {}
    assert_writing_policy(client.calls[0][0][0]['content'], role=True)


def test_direct_figure_review_assembles_writing_skill_for_each_reviewer(tmp_path, monkeypatch):
    import research.figures.model_workflow as workflow

    monkeypatch.setattr(workflow, 'review_requests', lambda bundle, context: [{
        'role': 'Evidence Reviewer', 'instruction': 'Review the measured figure.', 'prompt': 'Inspect it.',
    }])
    monkeypatch.setattr(workflow, '_validate_review_response', lambda review, role, bundle: (review, []))
    candidate = tmp_path / 'candidate.png'
    Image.new('RGB', (8, 8), 'white').save(candidate)
    client = CapturingClient()

    workflow.review_with_models(client, {'candidates': [{'outputs': {'png': str(candidate)}}]}, tmp_path / 'review')
    assert len(client.calls) == 1
    assert_writing_policy(client.calls[0][0][0]['content'], role=True)


def test_figure_production_direct_reply_assembles_the_writing_skill():
    from research.figures.production import _json_reply

    client = CapturingClient()
    assert _json_reply(client, 'Return the supplied schema.', {'source': 'actual input'}) == {}
    assert_writing_policy(client.calls[0][0][0]['content'], role=True)


def test_statistical_manuscript_review_assembles_revision_skill(tmp_path, monkeypatch):
    import research.planning.model as planning_model
    import research.paper.statistical_narrative as narrative

    captured = {}

    def context_model_json(client, messages, workspace, **kwargs):
        captured['messages'] = messages
        captured['kwargs'] = kwargs
        return None, {'usage': {}, 'model': 'scripted-callsite-fixture'}

    monkeypatch.setattr(narrative, '_has_statistical_visuals', lambda evidence, draft: True)
    monkeypatch.setattr(narrative, '_rows_and_refs', lambda evidence: {})
    monkeypatch.setattr(narrative, '_targets', lambda evidence, draft, rows: (
        {(0, 0, 'text'): {'text': 'Observed prose.', '_locator': ('blocks', 0)}},
        [{'fixture': 'visual'}],
    ))
    monkeypatch.setattr(planning_model, 'context_model_json', context_model_json)
    monkeypatch.setattr(narrative, '_parse_response', lambda response: {
        'verdict': 'pass', 'edits': [], 'remaining_issues': [],
    })

    evidence = {'statistics': {'records': []}, 'metrics': [], 'runs': [], 'sources': [],
                'claims': [], 'figures': []}
    draft = {'sections': [], 'appendices': []}
    revised, report = narrative.review_statistical_explanations(CapturingClient(), evidence, draft)

    assert revised == draft
    assert report['actual_model_call'] is True
    assert_writing_policy(captured['messages'][0]['content'], role=True)
    assert 'MINIMAL-EDIT REVISION' in captured['messages'][0]['content']
    assert captured['kwargs']['output_stage'] == 'manuscript_revision'
