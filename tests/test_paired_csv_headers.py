"""Regression checks for paired CSV header validation."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.validation.statistics import paired_csv


def paired_options(**overrides):
    options = {
        'unit_column': 'unit',
        'baseline_column': 'baseline',
        'candidate_column': 'candidate',
        'bootstrap_samples': 100,
    }
    options.update(overrides)
    return options


@pytest.mark.parametrize(
    'csv_text',
    [
        'unit,baseline,candidate,candidate\nu1,10,9,100\nu2,10,9,100\n',
        'unit,unit,baseline,candidate\nu1,100,10,9\nu2,100,10,9\n',
        'unit,baseline,baseline,candidate\nu1,10,100,9\nu2,10,100,9\n',
        'unit,baseline,candidate,metadata,metadata\nu1,10,9,a,b\nu2,10,9,c,d\n',
        '"unit","baseline","candidate","candidate"\nu1,10,9,100\nu2,10,9,100\n',
    ],
)
def test_duplicate_decoded_headers_fail_before_output(tmp_path, csv_text):
    source = tmp_path / 'paired.csv'
    output = tmp_path / 'metrics.json'
    sentinel = b'preserve this existing result'
    source.write_text(csv_text)
    output.write_bytes(sentinel)

    with pytest.raises(ValueError, match='Duplicate CSV column names'):
        paired_csv(source, output=output, **paired_options())

    assert output.read_bytes() == sentinel


def test_duplicate_headers_fail_for_input_stream_without_closing_it(tmp_path):
    stream = io.StringIO('unit,baseline,candidate,candidate\nu1,10,9,100\nu2,10,9,100\n')
    output = tmp_path / 'metrics.json'
    sentinel = b'preserve this existing result'
    output.write_bytes(sentinel)

    with pytest.raises(ValueError, match='Duplicate CSV column names'):
        paired_csv(tmp_path / 'retained.csv', input_stream=stream, output=output, **paired_options())

    assert not stream.closed
    assert stream.tell() > 0
    assert output.read_bytes() == sentinel


def test_distinct_headers_keep_expected_lower_direction_result(tmp_path):
    source = tmp_path / 'paired.csv'
    source.write_text('unit,baseline,candidate,metadata\nu1,10,9,a\nu2,10,9,b\n')

    result = paired_csv(source, **paired_options())

    assert result['baseline_mean'] == 10.0
    assert result['candidate_mean'] == 9.0
    assert result['improvement'] == 1.0


def test_distinct_headers_keep_expected_higher_direction_result(tmp_path):
    source = tmp_path / 'paired.csv'
    source.write_text('unit,baseline,candidate,metadata\nu1,10,9,a\nu2,10,9,b\n')

    result = paired_csv(source, direction='higher', **paired_options())

    assert result['improvement'] == -1.0


def test_input_stream_is_consumed_but_not_closed_for_valid_csv(tmp_path):
    stream = io.StringIO('unit,baseline,candidate\nu1,10,9\nu2,10,9\n')

    result = paired_csv(tmp_path / 'retained.csv', input_stream=stream, **paired_options())

    assert result['improvement'] == 1.0
    assert stream.tell() == len(stream.getvalue())
    assert not stream.closed


def test_missing_requested_column_boundary_is_unchanged(tmp_path):
    source = tmp_path / 'paired.csv'
    source.write_text('unit,baseline\nu1,10\nu2,10\n')

    with pytest.raises(ValueError, match='missing requested pairing or score columns'):
        paired_csv(source, **paired_options())


def test_nonfinite_score_boundary_is_unchanged(tmp_path):
    source = tmp_path / 'paired.csv'
    source.write_text('unit,baseline,candidate\nu1,10,nan\nu2,10,9\n')

    with pytest.raises(ValueError, match='Nonfinite observed scores'):
        paired_csv(source, **paired_options())


def test_api_enqueue_and_direct_worker_preserve_duplicate_failure_and_valid_provenance(tmp_path):
    """Exercise the actual API queue and paired worker branch in an isolated interpreter."""
    root = Path(__file__).resolve().parents[1]
    layouts = {}
    for scenario in ('duplicate', 'valid'):
        scenario_root = (tmp_path / scenario).resolve()
        layout = {
            'root': scenario_root,
            'database': (scenario_root / 'integration.sqlite').resolve(),
            'data': (scenario_root / 'data').resolve(),
            'tmp': (scenario_root / 'tmp').resolve(),
            'mpl': (scenario_root / 'mpl').resolve(),
        }
        scenario_root.mkdir(parents=True, exist_ok=True)
        layout['tmp'].mkdir(parents=True, exist_ok=True)
        layout['mpl'].mkdir(parents=True, exist_ok=True)
        layouts[scenario] = layout
    assert layouts['duplicate']['root'] != layouts['valid']['root']
    destinations = [
        path
        for layout in layouts.values()
        for key, path in layout.items()
        if key != 'root'
    ]
    assert len(destinations) == len(set(destinations))
    assert all(
        not (left in right.parents or right in left.parents)
        for index, left in enumerate(destinations)
        for right in destinations[index + 1:]
    )
    for scenario in ('duplicate', 'valid'):
        layout = layouts[scenario]
        env = {
            **os.environ,
            'FOREST_DATA_DIR': str(layout['data']),
            'FOREST_DATABASE_URL': 'sqlite:///' + str(layout['database']),
            'FOREST_MODEL': '',
            'FOREST_OWNER_TOKEN': 'integration-test-owner',
            'TMPDIR': str(layout['tmp']),
            'MPLCONFIGDIR': str(layout['mpl']),
            'PYTHONPATH': str(root),
        }
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), 'api-worker-check', scenario],
            cwd=root, env=env, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def _run_api_worker_scenario(scenario):
    """Run from a child interpreter after its isolated settings are configured."""
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from services.api.common import project_dir
    from services.api.db import Analysis, Session, migrate
    from services.api.main import app
    from services.worker.execute import execute

    migrate()
    client = TestClient(app, headers={'Authorization': 'Bearer integration-test-owner'})

    def create_project(name):
        response = client.post('/api/projects', json={
            'name': name,
            'goal': 'Reject ambiguous paired CSV headers before analysis publication.',
            'mode': 'manual',
            'budget': {'allow_paid': False, 'max_runs': 10},
            'config': {
                'publication_profile': {'id': 'operational'},
                'verification_policy': 'optional',
            },
        })
        assert response.status_code == 200, response.text
        return response.json()

    def upload(project, content):
        response = client.post(
            f"/api/projects/{project['id']}/upload",
            params={'directory': 'uploads'},
            files={'file': ('paired.csv', content, 'text/csv')},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def enqueue(project, path, request_id):
        response = client.post(
            f"/api/projects/{project['id']}/statistics/paired",
            json={
                'path': path,
                'unit_column': 'unit',
                'baseline_column': 'baseline',
                'candidate_column': 'candidate',
                'direction': 'lower',
                'bootstrap_samples': 100,
                'seed': 7,
                'request_id': request_id,
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()
        assert run['status'] == 'queued'
        return run

    if scenario == 'duplicate':
        project = create_project('Duplicate paired headers')
        content = (
            b'unit,baseline,candidate,candidate\n'
            b'u1,10,9,100\n'
            b'u2,10,9,100\n'
        )
        upload_result = upload(project, content)
        run = enqueue(project, upload_result['path'], 'duplicate-run')
        output = project_dir(project['id']) / run['output_path']
        with Session() as session:
            assert session.scalar(select(Analysis).where(Analysis.project_id == project['id'])) is None

        with pytest.raises(ValueError, match='Duplicate CSV column names'):
            execute(run['id'])

        assert (output / 'paired-input.csv').read_bytes() == content
        assert not (output / 'metrics.json').exists()
        with Session() as session:
            assert session.scalar(select(Analysis).where(Analysis.project_id == project['id'])) is None
    elif scenario == 'valid':
        project = create_project('Distinct paired headers')
        content = b'unit,baseline,candidate,metadata\nu1,10,9,a\nu2,10,9,b\n'
        upload_result = upload(project, content)
        run = enqueue(project, upload_result['path'], 'valid-run')
        output = project_dir(project['id']) / run['output_path']
        with Session() as session:
            assert not list(session.scalars(select(Analysis).where(Analysis.project_id == project['id'])))

        result = execute(run['id'])

        assert result['candidate_mean'] == 9.0
        assert result['improvement'] == 1.0
        assert (output / 'paired-input.csv').read_bytes() == content
        metrics = json.loads((output / 'metrics.json').read_text())
        assert metrics['candidate_mean'] == 9.0
        assert metrics['improvement'] == 1.0
        with Session() as session:
            analyses = list(session.scalars(select(Analysis).where(Analysis.project_id == project['id'])))
        assert len(analyses) == 1
        analysis = analyses[0]
        assert analysis.data['analysis_run_id'] == run['id']
        assert analysis.data['input_provenance']['input_record_id'] == run['id'] + ':paired-input'
        assert analysis.data['input_provenance']['artifact_path'].endswith('/paired-input.csv')
    else:
        raise AssertionError(f'Unknown API worker scenario: {scenario}')
    client.close()


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == 'api-worker-check':
    _run_api_worker_scenario(sys.argv[2])
