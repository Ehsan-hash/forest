import json

import pytest

from research.experiments.study_memory import ContextPageCapacityError, StudyMemory


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


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


def test_optional_capacity_does_not_create_an_implicit_global_page_limit(tmp_path):
    memory = StudyMemory(tmp_path)
    value = {'original': 'complete original source ' * 60000}
    metadata = memory.save_json('original', value)
    page = memory.read_json(metadata['segment_id'], limit=10_000_000)
    assert len(page['content']) > 1_048_576
    assert json.loads(page['content']) == value and page['complete']
    assert page['returned_characters'] == len(page['content'])
    assert not page['capacity_limited'] and 'response_capacity' not in page


def test_exact_serialized_page_capacity_preserves_every_character_and_restart(tmp_path):
    memory = StudyMemory(tmp_path)
    value = {'original': ['quotes " and \\ newline\n中文🌲' * 80] * 4}
    metadata = memory.save_json('original', value)
    source_path = memory.store.root / (metadata['segment_id'] + '.txt')
    original_file = source_path.read_bytes()
    expected = compact(value)
    offset, pages = 0, []
    while True:
        # This fixture capacity covers this response JSON only. The production
        # caller supplies a measure covering its complete model request.
        reader = StudyMemory(tmp_path)
        page = reader.read_json(metadata['segment_id'], offset=offset, limit=10_000_000,
                                response_capacity=1100)
        assert len(compact(page)) <= 1100
        assert page['content'] == expected[offset:page['next_offset']]
        assert page['returned_characters'] == len(page['content']) > 0
        assert page['response_capacity'] == 1100
        assert page['next_offset'] > offset
        assert page['capacity_limited'] == (not page['complete'])
        pages.append(page['content'])
        offset = page['next_offset']
        if page['complete']:
            break
    assert ''.join(pages) == expected and offset == len(expected)
    assert source_path.read_bytes() == original_file
    assert len(reader.catalog()['segments']) == 1
    assert metadata['segment_id'] == reader.save_json('original', value)['segment_id']


def test_large_retrieval_fits_actual_codex_request_escaping_and_continues(tmp_path):
    """Reproduce the 2.4M source page edge with no physical inference."""
    from research.agents.provider import ModelClient
    from research.experiments.study_runtime import _intern_delivered_messages, _treatment_history_view

    source = {'before_conditions': {f'config-{index}': {
        'ordered_sample_ids': [f'nonformal-infrastructure-row-{row:05d}' for row in range(10000)]}
        for index in range(6)}}
    original_messages = [{'role': 'user', 'content': json.dumps(source, ensure_ascii=False)}]
    delivered, encoding = _intern_delivered_messages(original_messages)
    assert encoding  # Same lossless public treatment renderer as production.
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original-public-messages', {'messages': original_messages})
    client = ModelClient({'id': 'nonformal-capacity-fixture', 'kind': 'codex_cli',
        'base_url': 'http://127.0.0.1', 'model': 'gpt-6-luna',
        'config': {'reasoning_effort': 'medium'}}, request_guard=lambda _: None)

    def physical_characters(page):
        action = {'action_work_id': 'nonformal-observational-read', 'kind': 'analysis',
            'state': 'completed', 'result': {'operation': 'read_context_segment', **page}}
        history = _treatment_history_view('FULL', [{'actions': [action]}])
        messages = delivered + [{'role': 'user', 'content':
            'Observed results from completed source-bound operations in this episode:\n' + compact(history)}]
        # Real ModelClient construction includes common controls and the actual
        # CLI JSON envelope. No complete()/transport/process call is made.
        endpoint, request = client.build_request(messages, json_mode=True, max_output_tokens=1024)
        assert endpoint == 'codex_cli'
        return len(compact(request))

    requested = memory.read_json(metadata['segment_id'], pointer='/messages/0/content', limit=10_000_000)
    assert len(requested['content']) > 2_400_000
    assert physical_characters(requested) > 3_000_000
    # This is the interface capability observed in the actual CLI rejection,
    # supplied by the caller rather than embedded as a memory/module default.
    capability = 1_048_576
    offset, reconstructed, reads = 0, [], 0
    while True:
        page = memory.read_json(metadata['segment_id'], pointer='/messages/0/content',
            offset=offset, limit=10_000_000, response_capacity=capability,
            measure_response=physical_characters)
        assert physical_characters(page) <= capability
        assert page['content'] == requested['content'][offset:page['next_offset']]
        assert page['complete'] == (page['next_offset'] == requested['total_characters'])
        assert page['next_offset'] > offset
        reconstructed.append(page['content'])
        offset = page['next_offset']
        reads += 1
        if page['complete']:
            break
    assert reads > 1 and ''.join(reconstructed) == requested['content']
    assert json.loads(''.join(reconstructed)) == original_messages[0]['content']


def test_capacity_covers_caller_wrappers_not_just_raw_content(tmp_path):
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original', {'text': '"\\\n中文🌲' * 1000})

    def nested_characters(page):
        return len(compact({'messages': [{'role': 'user', 'content': compact({
            'decision_history': {'operation': 'read_context_segment', 'result': page}})}],
            'schema': {'description': 'Original controls and exact source receipt'}}))

    page = memory.read_json(metadata['segment_id'], limit=10_000_000,
        response_capacity=1800, measure_response=nested_characters)
    assert nested_characters(page) <= 1800
    assert page['capacity_limited'] and not page['complete']
    assert page['content'] == compact({'text': '"\\\n中文🌲' * 1000})[:page['next_offset']]


def test_unfit_minimum_page_reports_capacity_without_advancing_or_modifying_source(tmp_path):
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original', {'source': list(range(100))})
    before = memory.catalog()
    with pytest.raises(ContextPageCapacityError) as failure:
        memory.read_json(metadata['segment_id'], response_capacity=10,
                         measure_response=lambda page: 1000 + len(compact(page)))
    assert failure.value.response_capacity == 10
    assert failure.value.minimum_response_characters > 1000
    assert memory.catalog() == before
    assert json.loads(memory.read_json(metadata['segment_id'])['content']) == {'source': list(range(100))}


def test_final_empty_page_is_measured_and_cannot_create_an_incomplete_empty_loop(tmp_path):
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original', {'source': 'original'})
    total = len(compact({'source': 'original'}))
    page = memory.read_json(metadata['segment_id'], offset=total,
        response_capacity=1000, measure_response=lambda page: len(compact(page)))
    assert page['content'] == '' and page['complete'] and page['next_offset'] == total
    with pytest.raises(ContextPageCapacityError):
        memory.read_json(metadata['segment_id'], offset=total,
                        response_capacity=1, measure_response=lambda page: len(compact(page)))


@pytest.mark.parametrize('params', [
    {'response_capacity': True}, {'response_capacity': 0}, {'response_capacity': -1},
    {'response_capacity': 1.5}, {'response_capacity': '1000'}, {'measure_response': 1},
])
def test_invalid_response_capacity_parameters_fail_explicitly(tmp_path, params):
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original', {'source': 'original'})
    with pytest.raises(ValueError):
        memory.read_json(metadata['segment_id'], **params)


@pytest.mark.parametrize('size', [True, -1, 1.5, '1000', None])
def test_response_measurement_requires_an_exact_character_count(tmp_path, size):
    memory = StudyMemory(tmp_path)
    metadata = memory.save_json('original', {'source': 'original'})
    with pytest.raises(ValueError, match='character count'):
        memory.read_json(metadata['segment_id'], response_capacity=1000,
                         measure_response=lambda page: size)
