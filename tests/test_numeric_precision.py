"""Actual JSON artifacts retain their integer precision during verification."""
import json

import pytest

from research.validation.handoff import check_contract
from research.validation.statistics import compare_result_files


CASES = [
    (2**53, 2**53 + 1, 0, 0, False),
    (2**63 - 1, 2**63 - 2, 0, 0, False),
    (-(2**53), -(2**53) - 1, 0, 0, False),
    (2**53 + 1, float(2**53), 0, 0, False),
    (2**53, 2**53, 0, 0, True),
    (2**53, 2**53 + 1, .5, 0, False),
    (2**53, 2**53 + 1, 1, 0, True),
    (2**53, 2**53 + 1, 0, 1e-16, False),
    (2**53, 2**53 + 1, 0, 2e-16, True),
    (1.0, 1.0002, .001, 0, True),
    (1.0, 1.0002, 0, 0, False),
]


@pytest.mark.parametrize('first,second,absolute,relative,matches', CASES)
@pytest.mark.parametrize('engine', ['standalone', 'handoff'])
def test_numeric_artifacts_preserve_integer_precision(
    tmp_path, first, second, absolute, relative, matches, engine
):
    producer, verifier = tmp_path / 'producer', tmp_path / 'verifier'
    producer.mkdir()
    verifier.mkdir()
    source, repeated = producer / 'metrics.json', verifier / 'metrics.json'
    source.write_text(json.dumps({'measurement': first}))
    repeated.write_text(json.dumps({'measurement': second}))
    if engine == 'standalone':
        result = compare_result_files(source, repeated, absolute_tolerance=absolute,
                                      relative_tolerance=relative)
        assert result['status'] == ('NUMERIC_MATCH' if matches else 'NUMERIC_DIFFERENCE')
        comparison = result['comparisons'][0]
    else:
        result = check_contract({'producer_node_id': 'producer', 'checks': [{
            'id': 'comparison', 'kind': 'numeric_compare',
            'source': 'metrics.json', 'repeat': 'metrics.json',
            'absolute_tolerance': absolute, 'relative_tolerance': relative,
        }]}, producer, verifier)
        assert result['verification_status'] == ('accepted' if matches else 'rejected')
        comparison = result['checks'][0]['comparisons'][0]
    assert comparison['matches'] is matches
    assert comparison['original'] == first
    assert comparison['repeated'] == second
    if first != second:
        assert comparison['absolute_difference'] > 0
    if isinstance(second, float) and first == 2**53 + 1:
        assert comparison['absolute_difference'] == 1

