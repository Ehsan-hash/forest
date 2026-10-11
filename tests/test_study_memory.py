import json

import pytest

from research.experiments.study_memory import StudyMemory


def test_exact_complete_pages_and_restart(tmp_path):
    memory = StudyMemory(tmp_path)
    value = {'manifest': {'ids': list(range(12000))}, 'artifacts': ['中文', 'original']}
    metadata = memory.save_json('source', value)
    reader = StudyMemory(tmp_path)
    text = ''
    offset = 0
    while True:
        page = reader.read_json(metadata['segment_id'], offset=offset, limit=711)
        text += page['content']
        offset = page['next_offset']
        assert page['scientific_progress_eligible'] is False
        if page['complete']:
            break
    assert json.loads(text) == value
    assert metadata['segment_id'] == reader.save_json('source', value)['segment_id']


def test_versions_never_destroy_original_or_leak_other_episode(tmp_path):
    memory = StudyMemory(tmp_path/'episode-one')
    original = memory.save_json('same-key', {'value': 'old'})
    revision = memory.save_json('same-key', {'value': 'new'})
    assert original['segment_id'] != revision['segment_id']
    assert json.loads(memory.read_json(original['segment_id'])['content']) == {'value': 'old'}
    assert len(memory.catalog()['segments']) == 2
    other = StudyMemory(tmp_path/'episode-two')
    with pytest.raises(ValueError, match='Unknown segment'):
        other.read_json(original['segment_id'])
    with pytest.raises(ValueError):
        memory.read_json('../../hidden-test')


def test_json_pointer_preserves_arrays_and_escaped_keys(tmp_path):
    memory = StudyMemory(tmp_path)
    meta = memory.save_json('original', {'a/b': {'~key': ['first', 'second', 'third']}})
    page = memory.read_json(meta['segment_id'], pointer='/a~1b/~0key')
    assert json.loads(page['content']) == ['first', 'second', 'third']
    assert json.loads(memory.read_json(meta['segment_id'], pointer='/a~1b/~0key/1')['content']) == 'second'
    for pointer in ('/a~1b/~0key/01', '/a~1b/~0key/5', '/missing', 'invalid'):
        with pytest.raises(ValueError):
            memory.read_json(meta['segment_id'], pointer=pointer)


def test_public_scope_only_and_invalid_page_parameters(tmp_path):
    memory = StudyMemory(tmp_path)
    with pytest.raises(ValueError, match='arm-public'):
        memory.save_json('hidden', {'truth': True}, audience='raw-evaluator')
    metadata = memory.save_json('public', {'metric': 1.0})
    assert json.loads(memory.read_json('catalog')['content'])['audience'] == 'arm-public'
    for params in ({'offset': True}, {'offset': -1}, {'limit': False}, {'limit': 0}):
        with pytest.raises(ValueError):
            memory.read_json(metadata['segment_id'], **params)
