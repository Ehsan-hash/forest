from copy import deepcopy
import json

from research.agents.context_store import ContextStore
from research.agents.processes import atomic_json
from research.agents.runtime import ToolRuntime


def test_repeated_actual_tool_reads_have_linear_growth_and_exact_originals(tmp_path):
    tool = ToolRuntime.__new__(ToolRuntime)
    tool.workspace = tmp_path
    transcript = [{'step': 1, 'content': 'Original scientific observation',
                   'tool_result': {'content': 'original-value-' * 200, 'verified': True}}]
    sizes = []
    originals = []
    for index in range(16):
        atomic_json(tmp_path/'agent_transcript.json', transcript)
        original = deepcopy(transcript)
        result = tool.dispatch('read_transcript', {'offset': 0, 'limit': 100}, None)
        assert json.loads((tmp_path/'agent_transcript.json').read_text()) == original
        assert result['turns'][0] == transcript[0]
        sizes.append(len(json.dumps(result)))
        for turn, source in zip(result['turns'][1:], originals):
            reference = turn['tool_result']['original_result_reference']
            store = ContextStore(tmp_path)
            text = ''
            offset = 0
            while True:
                page = store.read(reference['segment_id'], offset=offset)
                text += page['content']
                offset = page['next_offset']
                if page['complete']:
                    break
            assert json.loads(text) == source
        originals.append(deepcopy(result))
        transcript.append({'step': len(transcript)+1, 'content': 'Read recorded history',
            'executed_action': {'tool': 'read_transcript', 'arguments': {'offset': 0, 'limit': 100}},
            'tool_result': result})
    increments = [later-earlier for earlier,later in zip(sizes,sizes[1:])]
    assert max(increments) < min(increments) * 1.1
    assert sizes[-1] < sizes[0] + 1000*len(sizes)


def test_legacy_nested_receipt_is_preserved_but_never_reembedded(tmp_path):
    nested = {'turns': [{'step': 1, 'tool_result': {'turns': [{'content': 'actual old observation'}]}}]}
    original = [{'step': 2, 'executed_action': {'tool': 'read_transcript'}, 'tool_result': nested}]
    atomic_json(tmp_path/'agent_transcript.json', original)
    tool = ToolRuntime.__new__(ToolRuntime)
    tool.workspace = tmp_path
    result = tool.dispatch('read_transcript', {'offset': 0, 'limit': 100}, None)
    receipt = result['turns'][0]['tool_result']
    assert 'turns' not in receipt
    identifier = receipt['original_result_reference']['segment_id']
    assert json.loads(ContextStore(tmp_path).read(identifier)['content']) == nested
    assert json.loads((tmp_path/'agent_transcript.json').read_text()) == original
