"""Invalid public-memory queries replan without claiming a delivered page."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

import pytest

from research.experiments.study_memory import ContextQueryError, StudyMemory
from research.experiments.study_runtime import StudyRuntime, _is_context_read_action


def fixture(tmp_path):
    from test_study_runtime import _context_read_episode_fixture
    runtime, manifest_path, episode, _ = _context_read_episode_fixture(tmp_path, [])
    payload = episode['payload']
    read = next(row for row in payload['action_bindings'] if row.get('action_name') == 'read_context_segment')
    action = {'kind': 'read_context_segment', 'action_work_id': read['work_id'],
        'context_segment_id': payload['context_memory']['segment_id'],
        'context_pointer': '/', 'context_offset': 0, 'context_limit': 1885}
    return runtime, manifest_path, episode, read, action


@pytest.mark.parametrize('changed', [
    {}, {'context_pointer': '/absent'}, {'context_pointer': '/messages/999'},
    {'context_pointer': '/messages/01'}, {'context_pointer': 'messages'},
    {'context_segment_id': 'unregistered-segment'}, {'context_offset': -1},
    {'context_offset': True}, {'context_limit': 0}, {'context_limit': True},
])
def test_invalid_query_returns_existing_replan_feedback_without_read_completion(tmp_path, changed):
    runtime, _, episode, read, action = fixture(tmp_path)
    action.update(changed)
    original = deepcopy(action)
    workspace = Path(read['memory_workspace'])
    sources = {path: path.read_bytes() for path in workspace.rglob('*.txt')}
    measured = []
    outcome = runtime.execute_decision_actions(episode, [action], context_response_capacity=1000000,
        context_read_measure=lambda *_args, **_kwargs: measured.append(True) or 1)
    assert outcome['state'] == 'retryable' and outcome['request_new_decision'] is True
    feedback = outcome['response_validation_feedback']
    assert 'invalid_context_query' in json.dumps(feedback)
    row = outcome['actions'][0]
    assert row['response_validation_feedback']['root_pointer'] == ''
    assert row['response_validation_feedback']['available_segment_ids'] == sorted(read['available_segment_ids'])
    assert row['result']['content'] is None and row['result']['delivery_status'] == 'not_delivered'
    assert row['result']['scientific_progress_eligible'] is False
    assert not _is_context_read_action(row) and measured == []
    assert action == original and {path: path.read_bytes() for path in sources} == sources
    with runtime.ledger.connect() as db:
        assert db.execute('SELECT 1 FROM forest_study_work WHERE work_id=?', (read['work_id'],)).fetchone() is None
        assert db.execute("SELECT 1 FROM forest_study_events WHERE event_type='context_page_read'").fetchone() is None


def test_bad_pointer_then_fresh_corrected_model_action_survives_restart(tmp_path):
    from test_aide_runtime import NonformalClient
    from research.experiments.study_context import restore_messages
    runtime, manifest_path, episode, read, action = fixture(tmp_path)
    corrected = {**action, 'context_pointer': ''}
    def decision(selected):
        return json.dumps({'mode': 'action', 'actions': [selected], 'submission': {},
            'rationale': 'Nonformal public-memory query fixture; no scientific result claimed.'})
    class QueryClient(NonformalClient):
        def complete(self, messages, **kwargs):
            chosen = action if not self.calls else corrected
            schema = self.config['_forest_action_schema']
            current_id = next(identifier for identifier in schema['properties']['actions']['items']
                ['properties']['action_work_id']['enum'] if '--read-context--' in identifier)
            self.responses.append(decision({**chosen, 'action_work_id': current_id}))
            return super().complete(messages, **kwargs)
    client = QueryClient([])
    client.config['max_input_characters'] = 1000000
    first = runtime.run_one(owner='nonformal-query', work_id=episode['work_id'], client=client)
    assert first['state'] == 'retryable'
    assert first['action_result']['request_new_decision'] is True
    assert first['action_result']['actions'][0]['reason'] == 'invalid_context_query'
    assert first['response_validation_feedback']
    with runtime.ledger.connect() as db:
        original_request = tuple(db.execute('SELECT request_json,response_json FROM forest_study_model_calls').fetchone())
    restored = StudyRuntime(campaign=runtime.campaign, ledger=runtime.ledger_path,
        ledger_id='ledger-test', manifest=manifest_path)
    second = restored.run_one(owner='nonformal-query-resumed', work_id=episode['work_id'], client=client)
    assert len(client.calls) == 2 and not second.get('resumed_saved_decision', False)
    delivered = '\n'.join(row['content'] for row in restore_messages(client.calls[1][0]))
    assert 'invalid_context_query' in delivered and 'empty pointer' in delivered
    assert second['action_result'].get('actions'), second['action_result']
    page = second['action_result']['actions'][0]['result']
    assert page['operation'] == 'read_context_segment' and page['pointer'] == ''
    memory = StudyMemory(Path(read['memory_workspace']).parent)
    expected = memory.read_json(action['context_segment_id'], limit=action['context_limit'])
    assert page['content'] == expected['content'] and page['returned_characters'] > 0
    assert page['scientific_progress_eligible'] is False
    with restored.ledger.connect() as db:
        records = db.execute('SELECT request_json,response_json,status FROM forest_study_model_calls ORDER BY recorded_at').fetchall()
        assert tuple(records[0][:2]) == original_request
        assert [row[2] for row in records] == ['completed', 'completed']
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind IN ('fit','infer')").fetchone()[0] == 0


def test_slash_remains_a_real_empty_key_when_the_original_source_has_one(tmp_path):
    memory = StudyMemory(tmp_path)
    segment = memory.save_json('nonformal', {'': 'Actual empty-key value.', 'source': [1, 2]})['segment_id']
    assert json.loads(memory.read_json(segment, pointer='/')['content']) == 'Actual empty-key value.'
    assert json.loads(memory.read_json(segment, pointer='')['content']) == {'': 'Actual empty-key value.', 'source': [1, 2]}
    with pytest.raises(ContextQueryError):
        memory.read_json(segment, pointer='/absent')


def test_fresh_public_catalog_matches_current_bindings_without_changing_previous_messages(tmp_path):
    runtime, _, episode, _, _ = fixture(tmp_path)
    previous = deepcopy(episode['payload'])
    old_messages = deepcopy(previous['messages'])
    history = [{'actions': [{'action_work_id': 'nonformal-observation', 'kind': 'analysis',
        'state': 'retryable', 'result': {'operation': 'invalid_context_query', 'content': None}}]}]
    current = runtime._attach_public_context(previous, previous, history=history)
    marker = 'Arm-public context catalog (metadata only): '
    notes = [message['content'] for message in current['messages'] if message['content'].startswith(marker)]
    assert len(notes) == 1
    catalog, _ = json.JSONDecoder().raw_decode(notes[0][len(marker):])
    assert catalog == current['context_memory']['catalog']
    assert catalog['current']['study-public:public-context'] == current['context_memory']['segment_id']
    assert current['context_memory']['segment_id'] != previous['context_memory']['segment_id']
    assert previous['messages'] == old_messages
    old_science = [message for message in old_messages if not message['content'].startswith(marker)]
    current_science = [message for message in current['messages'] if not message['content'].startswith(marker)]
    assert current_science == old_science


def test_source_and_measurement_failures_still_escape_query_feedback(tmp_path):
    runtime, _, episode, read, action = fixture(tmp_path)
    action['context_pointer'] = ''
    def broken_measurement(*_args, **_kwargs):
        raise ValueError('Nonformal infrastructure measurement failure')
    with pytest.raises(ValueError, match='infrastructure measurement failure'):
        runtime.execute_decision_action(episode, action, context_response_capacity=1000000,
            context_read_measure=broken_measurement)
    memory = StudyMemory(Path(read['memory_workspace']).parent)
    (memory.store.root / (action['context_segment_id'] + '.txt')).unlink()
    with pytest.raises(FileNotFoundError):
        runtime.execute_decision_action(episode, action)


def test_outside_campaign_read_binding_is_not_a_model_query_error(tmp_path):
    runtime, _, episode, read, action = fixture(tmp_path)
    malicious = deepcopy(episode)
    selected = next(row for row in malicious['payload']['action_bindings'] if row['work_id'] == read['work_id'])
    selected['memory_workspace'] = str(tmp_path / 'outside-campaign')
    with pytest.raises(ValueError, match='outside the campaign'):
        runtime.execute_decision_action(malicious, action)


def test_original_failed_pointer_is_reproduced_read_only_when_explicitly_selected():
    database = os.environ.get('FOREST_CONTEXT_QUERY_REQUEST_DB')
    if not database:
        pytest.skip('Set FOREST_CONTEXT_QUERY_REQUEST_DB to inspect the preserved incident read-only')
    request_id = 'acb1ae27-844f-4741-850d-85f57b91d34c'
    with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
        original = db.execute('SELECT request_json,response_json,work_id FROM forest_study_model_calls WHERE request_id=?',
            (request_id,)).fetchone()
        assert original is not None
    raw_response = json.loads(original[1])
    action = json.loads(raw_response['text'])['actions'][0]
    request = json.loads(original[0])
    allowed = request['decision_schema']['properties']['actions']['items']['properties']['context_segment_id']['enum']
    assert action['context_pointer'] == '/' and action['context_segment_id'] in allowed
    workspace = Path(database).parent / 'runs' / 'episodes' / quote(original[2], safe='._-') / 'public_memory'
    assert workspace.is_dir()
    memory = StudyMemory(workspace)
    source = memory.store.root / (action['context_segment_id'] + '.txt')
    original_source = source.read_bytes()
    assert '' not in json.loads(original_source)
    with pytest.raises(ContextQueryError, match='absent from the original evidence'):
        memory.read_json(action['context_segment_id'], pointer=action['context_pointer'],
            offset=action['context_offset'], limit=action['context_limit'])
    valid = memory.read_json(action['context_segment_id'], pointer='', offset=0, limit=action['context_limit'])
    assert valid['content'].startswith('{') and valid['returned_characters'] > 0
    assert source.read_bytes() == original_source
    with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT request_json,response_json,work_id FROM forest_study_model_calls WHERE request_id=?',
            (request_id,)).fetchone() == original
