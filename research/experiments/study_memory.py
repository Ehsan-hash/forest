"""Episode-scoped retrieval of complete researcher-visible scientific memory.

Only the caller's already filtered public treatment view enters this store.
No raw experiment path, hidden evaluator record or neighbouring arm is read.
"""
from __future__ import annotations

import json
from pathlib import Path

from research.agents.context_store import ContextStore


class ContextPageCapacityError(ValueError):
    """The selected working request cannot deliver even one source character."""

    def __init__(self, response_capacity, minimum_response_characters):
        self.response_capacity = response_capacity
        self.minimum_response_characters = minimum_response_characters
        super().__init__(
            'The provider interface capacity cannot deliver this context page; '
            'select a smaller working request while retaining the original segment '
            f'(capacity={response_capacity}, minimum={minimum_response_characters})')


class StudyMemory:
    def __init__(self, workspace: Path):
        self.store = ContextStore(workspace)

    def save_json(self, key, value, *, audience='arm-public', origin=None):
        if audience != 'arm-public':
            raise ValueError('Study memory accepts only an already filtered arm-public view')
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        identifier = self.store.put('study-public:' + str(key), text,
            origin=origin or str(key), authority='untrusted_evidence')
        metadata = self.store.index['segments'][identifier]
        return {**metadata, 'segment_id': identifier, 'audience': audience,
                'read_operation': 'read_context_segment'}

    def catalog(self):
        return {'segments': [dict(value) for value in self.store.index['segments'].values()],
                'current': dict(self.store.index['current']), 'audience': 'arm-public'}

    def read_json(self, segment_id, *, pointer='', offset=0, limit=6000,
                  response_capacity=None, measure_response=None):
        """Read exact JSON text pages, optionally from a JSON Pointer subtree.

        Paging controls one response size; all original characters and versions
        remain addressable, with no cumulative read or memory quota.

        ``response_capacity`` is an optional observed/configured provider
        interface character capacity, never a task quota. ``measure_response``
        receives the complete candidate page mapping and returns the exact
        serialized character count of the prospective physical request carrying
        it. The caller must include its schema, history, native prompt wrappers
        and JSON escaping. Without a callback, only this response JSON is
        measured. No universal provider capacity is assumed here.

        A binary search chooses a fitting source prefix for ordinary monotone
        serialization measures. Every returned candidate is measured exactly;
        a nonmonotone custom measure may yield a shorter fitting page. If even
        one source character cannot fit (or the empty final page cannot fit),
        ContextPageCapacityError asks the caller to select a smaller working
        request. Reading never changes the durable source or its versions.
        """
        if segment_id == 'catalog':
            value = self.catalog()
            metadata = {'origin': 'Episode public memory catalog', 'authority': 'metadata'}
        else:
            if segment_id not in self.store.index['segments']:
                raise ValueError('Unknown segment in this episode public memory')
            metadata = self.store.index['segments'][segment_id]
            path = self.store.root / (segment_id + '.txt')
            value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(pointer, str) or pointer and not pointer.startswith('/'):
            raise ValueError('A JSON Pointer must be empty or start with /')
        if pointer:
            for token in pointer.split('/')[1:]:
                token = token.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list):
                    if not token.isdigit() or str(int(token)) != token:
                        raise ValueError('Array JSON Pointer needs a canonical nonnegative index')
                    try:
                        value = value[int(token)]
                    except IndexError:
                        raise ValueError('JSON Pointer index is outside the original evidence') from None
                elif isinstance(value, dict) and token in value:
                    value = value[token]
                else:
                    raise ValueError('JSON Pointer is absent from the original evidence')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('Page offset must be a nonnegative integer')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('Page limit must be a positive integer')
        if response_capacity is not None and (type(response_capacity) is not int or response_capacity < 1):
            raise ValueError('Response interface capacity must be a positive integer')
        if measure_response is not None and not callable(measure_response):
            raise ValueError('Response measurement must be callable')
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        requested = content[offset:offset + limit]

        def candidate(length):
            end = offset + length
            return {**metadata, 'segment_id': segment_id, 'audience': 'arm-public',
                    'pointer': pointer, 'offset': offset, 'content': requested[:length],
                    'next_offset': end, 'total_characters': len(content),
                    'complete': end >= len(content), 'observational': True,
                    'scientific_progress_eligible': False,
                    'requested_limit': limit, 'returned_characters': length,
                    'capacity_limited': length < len(requested),
                    **({'response_capacity': response_capacity} if response_capacity is not None else {})}

        page = candidate(len(requested))
        if response_capacity is None:
            return page

        def measure(page):
            size = measure_response(page) if measure_response is not None else len(
                json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
            if type(size) is not int or size < 0:
                raise ValueError('Response measurement must return a nonnegative integer character count')
            return size

        if measure(page) <= response_capacity:
            return page
        # Never return an empty incomplete page: its unchanged continuation
        # offset would make retrieval spin without delivering original content.
        minimum_length = min(1, len(requested))
        best = candidate(minimum_length)
        minimum_size = measure(best)
        if minimum_size > response_capacity:
            raise ContextPageCapacityError(response_capacity, minimum_size)
        low, high = minimum_length, len(requested) - 1
        while low < high:
            length = (low + high + 1) // 2
            trial = candidate(length)
            if measure(trial) <= response_capacity:
                best, low = trial, length
            else:
                high = length - 1
        return best
