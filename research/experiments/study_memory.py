"""Episode-scoped retrieval of complete researcher-visible scientific memory.

Only the caller's already filtered public treatment view enters this store.
No raw experiment path, hidden evaluator record or neighbouring arm is read.
"""
from __future__ import annotations

import json
from pathlib import Path

from research.agents.context_store import ContextStore


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

    def read_json(self, segment_id, *, pointer='', offset=0, limit=6000):
        """Read exact JSON text pages, optionally from a JSON Pointer subtree.

        Paging controls one response size; all original characters and versions
        remain addressable, with no cumulative read or memory quota.
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
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        page = content[offset:offset + limit]
        end = offset + len(page)
        return {**metadata, 'segment_id': segment_id, 'audience': 'arm-public',
                'pointer': pointer, 'offset': offset, 'content': page,
                'next_offset': end, 'total_characters': len(content),
                'complete': end >= len(content), 'observational': True,
                'scientific_progress_eligible': False}
