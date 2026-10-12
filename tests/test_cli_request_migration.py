import json
import sqlite3
import pytest

from forest_cli.config import Context, remember_graph_request


def test_one_corrupt_legacy_record_does_not_disable_other_replays(tmp_path):
    folder = tmp_path / 'requests'
    folder.mkdir()
    context = Context('http://127.0.0.1:8000')
    original = {'expected_revision': 3, 'operation': 'edit_node', 'params': {'title': 'Actual intent'}}
    identity = [context.endpoint, 'existing-project', 'existing-request']
    good = {'identity': json.dumps(identity), 'intent': {'operation': 'edit_node'}, 'body': original}
    (folder / 'previous-opaque-name.json').write_text(json.dumps(good))
    corrupt = folder / 'unrelated-incomplete.json'
    corrupt.write_text('{"identity":')
    with pytest.warns(RuntimeWarning, match='Could not migrate original request journal'):
        replay = remember_graph_request(context, identity[1], identity[2], good['intent'],
                                        {**original, 'expected_revision': 99}, path=tmp_path / 'cli.json')
    assert replay == original
    assert corrupt.read_text() == '{"identity":'
    new = {'expected_revision': 4, 'operation': 'add_node'}
    assert remember_graph_request(context, 'project', 'new-request', new, new,
                                  path=tmp_path / 'cli.json') == new
    with sqlite3.connect(tmp_path / 'requests.sqlite') as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
