"""Isolated API/context/executor checks for read-only workspace analysis.

FastAPI, SQLite, snapshot files and the executor dispatcher are real. Authored
model/search replies are explicit protocol fixtures at their transport seams;
these tests do not establish live model quality or observed literature findings.
No external provider, network search, production project or worker is started.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:5000]}"
    return response.json()


def create(client, name="Read-only workspace analysis"):
    return ok(client.post('/api/projects', json={
        'name': name, 'mode': 'manual', 'goal': 'Diagnose the editable recorded research route.',
        'budget': {'seconds': 1800, 'allow_paid': False},
        'config': {'publication_profile': 'operational',
                   'controller': {'status': 'stopped', 'phase': 'USER_REVIEW',
                                  'route_review': {'consumed': True, 'custom': 'retained'}}}}))


def graph(client, project):
    return ok(client.get(f"/api/projects/{project['id']}/graph"))


def command(client, project, operation, targets=None, **params):
    return ok(client.post(f"/api/projects/{project['id']}/graph/commands", json={
        'operation': operation, 'request_id': str(uuid4()),
        'expected_revision': graph(client, project)['revision'], 'targets': targets or [], 'params': params}))


def add_node(client, project, title, **fields):
    identifier = str(uuid4())
    value = command(client, project, 'add_node', id=identifier,
                    branch_id=graph(client, project)['branches'][0]['id'],
                    type='experiment', title=title, **fields)
    return next(row for row in value['graph']['nodes'] if row['id'] == identifier)


def endpoint(project):
    return f"/api/projects/{project['id']}/research/analysis"


def request_body(client, project, **fields):
    return {'request_id': str(uuid4()), 'expected_revision': graph(client, project)['revision'],
            'scope': 'project', 'question': 'Which recorded failure should be diagnosed first?',
            'language': 'en', 'timeout': 30, **fields}


def provider(client):
    # No request is sent to this deliberately unused local endpoint.
    return ok(client.post('/api/providers', json={
        'name': 'Offline analysis protocol fixture', 'kind': 'ollama',
        'base_url': 'http://127.0.0.1:9', 'model': 'offline-analysis-protocol',
        'allow_paid': False}))


def recorded_context(client, project):
    """Create explicit authored records, not purported execution observations."""
    from services.api.common import project_dir, safe_path
    from services.api.db import (Session, TaskRun, ToolExecution, Hypothesis, ResearchClaim,
                                 DatasetAsset, ExperimentSpec, Analysis, Figure, Review, Derivation)

    source = add_node(client, project, 'Recorded source method', instructions='Original method instruction',
        config={'kind': 'command', 'command': ['true'], 'extension': {'retained': True},
                'api_key': 'EXCLUDED_PROTOCOL_KEY', 'env': {'DEMO_NAME': 'EXCLUDED_ENV_VALUE'}})
    focus = add_node(client, project, 'Recorded selected method')
    command(client, project, 'add_dependency', source=source['id'], target=focus['id'])
    dependent = add_node(client, project, 'Implicit dependent method',
        inputs=[{'node_id': focus['id'], 'path': 'result.txt', 'destination': 'input.txt'}])
    saved = ok(client.post('/api/library/import', json={'project_id': project['id'], 'paper': {
        'title': 'Authored citation protocol fixture', 'authors': ['Protocol Author'], 'year': 2020,
        'doi': '10.5555/authored-protocol-fixture', 'url': 'https://example.org/protocol-fixture',
        'read_scope': 'abstract', 'abstract': 'Authored bibliographic fixture, not a retrieved finding.',
        'passages': [{'id': 'abstract', 'page': None, 'text': 'Authored abstract fixture.'}]}}))
    identifiers = [str(uuid4()), str(uuid4())]
    with Session.begin() as session:
        for identifier, node, status in zip(identifiers, (source, focus), ('completed', 'failed')):
            run = TaskRun(id=identifier, project_id=project['id'], node_id=node['id'],
                branch_id=node['branch_id'], request_id='authored-' + identifier,
                kind='command', status=status, config={'kind': 'command', 'command': ['true']},
                node_revision=node['revision'], output_path='runs/' + identifier,
                metrics={'protocol_fixture': True}, error='Authored failure fixture' if status == 'failed' else None)
            session.add(run)
        session.flush()
        session.add(ToolExecution(run_id=identifiers[1], tool='read_file', status='failed',
            arguments={'path': 'missing.txt'}, result={'error': 'Authored missing-file receipt'}))
        for model in (Hypothesis, ResearchClaim, DatasetAsset, ExperimentSpec, Analysis, Figure, Review, Derivation):
            session.add(model(project_id=project['id'], title='Authored ' + model.__tablename__,
                              data={'protocol_fixture': True}, status='proposed'))
    root = project_dir(project['id'])
    for identifier in identifiers:
        output = root / 'runs' / identifier
        output.mkdir(parents=True, exist_ok=True)
        (output / 'metrics.json').write_text('{"protocol_fixture": true}')
    branch = graph(client, project)['branches'][0]
    folder = safe_path(root, branch['workspace'])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'method.py').write_text('PROTOCOL_METHOD_SENTINEL = True\n')
    (folder / 'secrets.json').write_text('{"password":"EXCLUDED_FILE_SECRET"}')
    return {'source': source, 'focus': focus, 'dependent': dependent, 'library': saved,
            'run_ids': identifiers, 'method': str((folder / 'method.py').relative_to(root))}


def recorded_version_and_intervention_history(client, project, recorded):
    """Persist authored history with real IDs/relations, not live human delivery."""
    from sqlalchemy import select
    from services.api.common import emit
    from services.api.db import Session, Event, now
    from services.interventions.models import Intervention, InterventionEffect, ActionDecision
    from services.research_evolution.models import GoalContractVersion, EvaluationProtocolVersion

    revision = graph(client, project)['revision']
    with Session.begin() as session:
        previous = session.scalar(select(GoalContractVersion).where(
            GoalContractVersion.project_id == project['id']).order_by(GoalContractVersion.revision.desc()))
        goal = GoalContractVersion(id=str(uuid4()), project_id=project['id'],
            revision=previous.revision + 1, parent_version_id=previous.id, project_revision=revision,
            objective=project['goal'], success_criteria=['Authored retained success condition'],
            contract={'protocol_fixture': True}, reason='Authored goal history fixture',
            changes={'protocol_fixture': True}, evidence_run_ids=[recorded['run_ids'][0]],
            affected_branch_ids=[recorded['focus']['branch_id']])
        protocol = EvaluationProtocolVersion(id=str(uuid4()), project_id=project['id'],
            protocol_id=str(uuid4()), revision=1, name='Authored evaluation protocol history',
            definition={'metric': 'protocol_fixture', 'source_run_id': recorded['run_ids'][0]},
            reason='Authored protocol history fixture', feedback_run_ids=[recorded['run_ids'][1]],
            classification='exploratory')
        intervention = Intervention(id=str(uuid4()), project_id=project['id'], request_id=str(uuid4()),
            actor='user', kind='instruction', observed_revision=revision, applied_revision=revision,
            intent={'text': 'Authored accepted instruction receipt'}, impact={'protocol_fixture': True},
            status='accepted', accepted_at=now())
        session.add_all([goal, protocol, intervention]); session.flush()
        effect = InterventionEffect(id=str(uuid4()), intervention_id=intervention.id,
            project_id=project['id'], run_id=recorded['run_ids'][1], action='instruction',
            status='failed', target={'run_id': recorded['run_ids'][1]},
            observation={'protocol_fixture': True}, error='Authored delivery failure; no live delivery claimed')
        decision = ActionDecision(id=str(uuid4()), project_id=project['id'],
            run_id=recorded['run_ids'][1], action_id=str(uuid4()), observed_revision=revision,
            proposed={'action': 'Authored proposal only'}, status='answered',
            answer={'action': 'Authored answer only'}, answered_at=now(), consumed_at=None)
        session.add_all([effect, decision])
        emit(session, project['id'], 'authored_scientific_receipt',
             {'run_id': recorded['run_ids'][1], 'protocol_fixture': True})
        session.flush()
        event = session.scalar(select(Event).where(Event.project_id == project['id'],
            Event.type == 'authored_scientific_receipt'))
        return {'resources': {row.__tablename__: row.id for row in (goal, protocol, intervention, effect, decision)},
                'event_id': event.id, 'event_sequence': event.sequence,
                'previous_goal_id': previous.id}


def authored_report(context, citations=()):
    """Author a structural fixture explicitly; never claim it is model output."""
    runs = context['runs']
    return {'summary': 'Authored protocol fixture: inspect recorded method and failures.',
        'experiments': [{'node_id': run.get('node_id'), 'run_ids': [run['id']],
                         'what_was_done': 'Authored record only.',
                         'result': 'No scientific result is asserted by this fixture.',
                         'problems': run.get('error') or 'No failure recorded.'} for run in runs],
        'findings': [{'title': 'Review the saved receipt', 'detail': 'This is authored diagnostic advice.',
                      'severity': 'warning', 'evidence_label': 'INFERRED',
                      'node_ids': context['focus']['node_ids'], 'run_ids': [],
                      'citation_ids': [citations[0]['id']] if citations else []}],
        'recommendations': [{'title': 'Read the saved method', 'action': 'Inspect the recorded file.',
                             'rationale': 'Resolve the recorded uncertainty before execution.',
                             'priority': 'high', 'kind': 'diagnostic',
                             'node_ids': context['focus']['node_ids'], 'run_ids': [], 'citation_ids': []}],
        'limitations': ['Offline protocol fixture; no live provider or literature finding.'],
        'coverage': {key: list(context['coverage'][key])
                     for key in ('node_ids', 'run_ids', 'resource_ids')}}


def case_complete_context_and_scoped_dependency_locators_preserve_real_record_identities(client, app):
    from services.api.db import Session, Project, TaskRun, Provider
    from services.api.common import project_dir
    from research.planning.workspace_analysis import build_analysis_context

    project = create(client)
    recorded = recorded_context(client, project)
    history = recorded_version_and_intervention_history(client, project, recorded)
    other = create(client, 'Foreign context history boundary')
    foreign_recorded = recorded_context(client, other)
    foreign_history = recorded_version_and_intervention_history(client, other, foreign_recorded)
    configured = ok(client.post('/api/providers', json={
        'name': 'Stored credential redaction protocol fixture', 'kind': 'ollama',
        'base_url': 'http://127.0.0.1:9', 'model': 'unused-offline-fixture',
        'api_key': 'AUTHORED_STORED_CREDENTIAL_SENTINEL', 'allow_paid': False}))
    with Session.begin() as session:
        row = session.get(TaskRun, recorded['run_ids'][1])
        connection = session.get(Provider, configured['id'])
        row.config = {**row.config, 'password': 'AUTHORED_CONFIG_SECRET_SENTINEL',
                      'provider_snapshot': {'id': connection.id, 'credential_ref': connection.credential_ref}}
        row.error = 'Authored failure fixture: AUTHORED_STORED_CREDENTIAL_SENTINEL'
    output = project_dir(project['id']) / 'runs' / recorded['run_ids'][1]
    (output / 'stdout.txt').write_text('Safe diagnostic before unread remainder.\n' + 'x' * 262144)
    (output / 'stderr.txt').write_text(
        'Safe failure diagnostic retained.\n'
        'Reported credential AUTHORED_STORED_CREDENTIAL_SENTINEL\n'
        'Reported config AUTHORED_CONFIG_SECRET_SENTINEL\n'
        'Authorization: Bearer AUTHORED_HEADER_SECRET_SENTINEL\n'
        'password=AUTHORED_ASSIGNMENT_SECRET_SENTINEL\n')
    before = graph(client, project)
    with Session() as session:
        project_row = session.get(Project, project['id'])
        context = build_analysis_context(session, project_row, 'node', recorded['focus']['id'])
        full = build_analysis_context(session, project_row, 'node', recorded['focus']['id'], include_materials=True)
    assert context['focus']['node_ids'] == [recorded['focus']['id']]
    assert context['focus']['upstream_node_ids'] == [recorded['source']['id']]
    assert context['focus']['downstream_node_ids'] == [recorded['dependent']['id']]
    assert set(context['coverage']['node_ids']) == {row['id'] for row in before['nodes']}
    assert set(context['coverage']['run_ids']) == set(recorded['run_ids'])
    assert len(context['resources']['source_passages']) == 1
    assert {row['id'] for rows in context['resources'].values() for row in rows} == set(context['coverage']['resource_ids'])
    for value in (context, full):
        for kind, identifier in history['resources'].items():
            rows = {row['id']: row for row in value['resources'][kind]}
            assert identifier in rows and rows[identifier]['project_id'] == project['id']
            assert identifier in value['coverage']['resource_ids']
            assert foreign_history['resources'][kind] not in rows
        assert not set(foreign_history['resources'].values()).intersection(value['coverage']['resource_ids'])
        goals = {row['id']: row for row in value['resources']['goal_contract_versions']}
        assert goals[history['resources']['goal_contract_versions']]['parent_version_id'] == history['previous_goal_id']
        assert goals[history['resources']['goal_contract_versions']]['evidence_run_ids'] == [recorded['run_ids'][0]]
        effects = {row['id']: row for row in value['resources']['intervention_effects']}
        effect = effects[history['resources']['intervention_effects']]
        assert effect['intervention_id'] == history['resources']['interventions']
        assert effect['run_id'] == recorded['run_ids'][1] and effect['status'] == 'failed'
        assert effect['error'] == 'Authored delivery failure; no live delivery claimed'
        decisions = {row['id']: row for row in value['resources']['action_decisions']}
        decision = decisions[history['resources']['action_decisions']]
        assert decision['run_id'] == recorded['run_ids'][1]
        assert decision['status'] == 'answered' and decision['consumed_at'] is None
        assert [event['sequence'] for event in value['events']] == sorted(event['sequence'] for event in value['events'])
        event = next(event for event in value['events'] if event['id'] == history['event_id'])
        assert event['sequence'] == history['event_sequence'] and event['data']['run_id'] == recorded['run_ids'][1]
        assert all(event['project_id'] == project['id'] for event in value['events'])
        assert foreign_history['event_id'] not in {event['id'] for event in value['events']}
    assert context['tool_executions'][0]['run_id'] == recorded['run_ids'][1]
    assert next(run for run in context['runs'] if run['id'] == recorded['run_ids'][1])['error'].startswith('Authored failure fixture')
    assert all('content' not in material for material in context['materials'])
    assert next(material for material in full['materials'] if material['path'] == recorded['method'])['content'].startswith('PROTOCOL_METHOD_SENTINEL')
    serialized = json.dumps(full)
    assert 'EXCLUDED_PROTOCOL_KEY' not in serialized
    assert 'EXCLUDED_ENV_VALUE' not in serialized
    assert 'EXCLUDED_FILE_SECRET' not in serialized
    for sentinel in ('AUTHORED_STORED_CREDENTIAL_SENTINEL', 'AUTHORED_CONFIG_SECRET_SENTINEL',
                     'AUTHORED_HEADER_SECRET_SENTINEL', 'AUTHORED_ASSIGNMENT_SECRET_SENTINEL'):
        assert sentinel not in serialized
    assert 'variable_names' in serialized and 'DEMO_NAME' in serialized
    logs = {Path(material['path']).name: material for material in full['materials']
            if material.get('run_id') == recorded['run_ids'][1]}
    assert 'Safe failure diagnostic retained.' in logs['stderr.txt']['content']
    assert '[credential omitted]' in logs['stderr.txt']['content']
    assert logs['stderr.txt']['complete'] is True
    assert logs['stdout.txt']['complete'] is False
    assert logs['stdout.txt']['content'].startswith('Safe diagnostic before unread remainder.')
    assert logs['stderr.txt']['trust'] == 'untrusted_execution_output'
    assert logs['stderr.txt']['redaction'] == 'credentials excluded'
    assert logs['stderr.txt']['bytes'] == (output / 'stderr.txt').stat().st_size
    preview = next(material for material in context['materials'] if material['path'] == logs['stderr.txt']['path'])
    assert preview['content_loaded'] is False and 'content' not in preview
    assert preview['run_id'] == recorded['run_ids'][1] and preview['modified_ns']
    assert context['snapshot']['fingerprint'] == full['snapshot']['fingerprint']
    assert graph(client, project) == before


def case_analysis_transport_does_not_change_scientific_route_health_windows(client, app):
    from research.planning.route_review import route_health, CONTROL_KINDS

    node_id = str(uuid4())
    recorded_graph = {'nodes': [{'id': node_id, 'revision': 0}], 'edges': [], 'branches': []}
    failures = [{'id': str(uuid4()), 'node_id': node_id, 'node_revision': 0, 'kind': 'command',
                 'status': 'failed', 'created_at': f'2026-01-01T00:00:0{i}'} for i in range(3)]
    advice = [{'id': str(uuid4()), 'kind': 'research_analysis', 'status': 'completed',
               'created_at': f'2026-01-01T00:01:{i:02d}'} for i in range(16)]
    expected = route_health(recorded_graph, failures)
    observed = route_health(recorded_graph, failures + advice)
    assert 'research_analysis' in CONTROL_KINDS
    assert observed['signals'] == expected['signals']
    assert observed['reviewed_run_ids'] == expected['reviewed_run_ids']
    plans = [{'id': str(uuid4()), 'kind': 'research_plan', 'status': 'completed',
              'created_at': f'2026-01-01T00:00:0{i}', 'metrics': {'graph_revision': 1, 'applied_commands': []}}
             for i in range(4)]
    assert route_health(recorded_graph, plans + advice)['signals'] == route_health(recorded_graph, plans)['signals']


def case_report_validation_rejects_foreign_and_misattributed_references_and_underspecified_research(client, app):
    from services.api.db import Session, Project
    from research.planning.workspace_analysis import build_analysis_context, validate_analysis_report
    from test_evidence_workflow import idea

    project = create(client)
    recorded_context(client, project)
    with Session() as session:
        context = build_analysis_context(session, session.get(Project, project['id']))
    citations = [{'id': 'library:authored-protocol-source'}]
    original = authored_report(context, citations)
    assert validate_analysis_report(original, context, citations) == original
    invalid = []
    for name, key in [('findings', 'node_ids'), ('findings', 'run_ids'), ('findings', 'citation_ids')]:
        value = deepcopy(original)
        value[name][0][key] = ['foreign-or-unobserved-id']
        invalid.append(value)
    for key in ('node_ids', 'run_ids', 'resource_ids'):
        value = deepcopy(original)
        value['coverage'][key] = []
        invalid.append(value)
        value = deepcopy(original)
        value['coverage'][key].append(value['coverage'][key][0])
        invalid.append(value)
    value = deepcopy(original)
    value['experiments'][0]['node_id'] = None
    invalid.append(value)
    value = deepcopy(original)
    value['experiments'][0]['node_id'] = original['experiments'][1]['node_id']
    invalid.append(value)
    value = deepcopy(original)
    value['findings'][0]['severity'] = 'invented-success'
    invalid.append(value)
    value = deepcopy(original)
    value['recommendations'][0]['priority'] = 'automatic'
    invalid.append(value)
    valid_research = deepcopy(original)
    valid_research['recommendations'][0].update(kind='research', judgment={**idea(),
        'research_question': 'Does the declared method reduce absolute error?',
        'success_event': 'Observed absolute error is lower on the named smooth test function.'})
    assert validate_analysis_report(valid_research, context, citations)['recommendations'][0]['judgment']['evidence_label'] == 'ESTIMATED'
    for key in ('research_question', 'success_event', 'base_case'):
        value = deepcopy(valid_research)
        value['recommendations'][0]['judgment'].pop(key)
        invalid.append(value)
    for value in invalid:
        with pytest.raises(ValueError):
            validate_analysis_report(value, context, citations)
    assert original == authored_report(context, citations)


def case_citation_catalog_is_server_owned_and_search_failure_never_creates_a_reference(client, app):
    from services.api.db import Session, Project, SourcePaper
    from research.planning.workspace_analysis import build_analysis_context, retrieve_citations
    from research.literature import sources
    from sqlalchemy import select

    project = create(client)
    recorded = recorded_context(client, project)
    with Session() as session:
        context = build_analysis_context(session, session.get(Project, project['id']))
        before = [row.id for row in session.scalars(select(SourcePaper).where(SourcePaper.project_id == project['id']))]
    calls = []
    def search_fixture(query, source, limit):
        calls.append((query, source, limit))
        if source == 'arxiv': raise RuntimeError('PRIVATE_TRANSPORT_BODY_SENTINEL')
        item = {'title': 'Authored search protocol fixture', 'doi': '10.5555/search-protocol-fixture',
                'authors': ['Search Fixture Author'], 'year': 2021, 'url': 'https://example.org/search-fixture',
                'read_scope': 'metadata', 'passages': [], 'source': source}
        return [deepcopy(item), deepcopy(item)]
    patch = pytest.MonkeyPatch()
    try:
        patch.setattr(sources, 'search', search_fixture)
        citations, receipts = retrieve_citations(context, {
            'literature_query': 'Exact authored search intent', 'literature_sources': ['crossref', 'arxiv'],
            'literature_limit': 4})
    finally:
        patch.undo()
    assert calls == [('Exact authored search intent', 'crossref', 4), ('Exact authored search intent', 'arxiv', 4)]
    assert len(citations) == 2
    saved = next(row for row in citations if row['library_id'] == recorded['library']['id'])
    retrieved = next(row for row in citations if row['source'] == 'crossref')
    assert saved['id'] == 'library:' + recorded['library']['id']
    assert saved['read_scope'] == 'abstract' and saved['passages']
    assert retrieved['read_scope'] == 'metadata' and retrieved['passages'] == []
    assert retrieved['doi'] == '10.5555/search-protocol-fixture'
    assert [row['status'] for row in receipts] == ['completed', 'failed']
    assert receipts[1]['count'] == 0
    assert 'PRIVATE_TRANSPORT_BODY_SENTINEL' not in json.dumps(receipts)
    with Session() as session:
        assert [row.id for row in session.scalars(select(SourcePaper).where(SourcePaper.project_id == project['id']))] == before


def case_provider_requirement_rolls_back_and_public_submit_rejects_unowned_configuration(client, app):
    from services.api.db import Session, TaskRun
    from sqlalchemy import select

    project = create(client)
    before = graph(client, project)
    context = ok(client.get(endpoint(project) + '/context'))
    assert context['provider']['available'] is False
    denied = client.post(endpoint(project), json=request_body(client, project))
    assert denied.status_code == 422 and denied.json()['detail']['code'] == 'PROVIDER_REQUIRED'
    assert graph(client, project) == before
    assert ok(client.get(endpoint(project))) == {'runs': []}
    with Session() as session:
        assert not list(session.scalars(select(TaskRun).where(TaskRun.project_id == project['id'])))
    configured = provider(client)
    for fields in ({'provider_snapshot': {'id': configured['id']}}, {'env': {'UNOWNED': 'value'}},
                   {'literature_sources': ['unsupported']}, {'literature_limit': 0}, {'timeout': -1}):
        response = client.post(endpoint(project), json=request_body(client, project,
            provider_id=configured['id'], **fields))
        assert response.status_code == 422, response.text
    assert ok(client.get(endpoint(project))) == {'runs': []}


def executor_fixture(client, project, *, report_override=None, provider_error=None):
    """Real dispatcher and persistence, with an explicitly authored provider seam."""
    from research.planning import model
    from research.literature import sources
    from services.worker.execute import execute
    from services.api.db import Session, TaskRun

    configured = provider(client)
    body = request_body(client, project, provider_id=configured['id'], language='zh', literature_sources=[])
    run = ok(client.post(endpoint(project), json=body))
    submitted = []
    def model_fixture(driver, messages, workspace, **kwargs):
        task = json.loads(messages[-1]['content'])
        submitted.append(task)
        if provider_error: raise provider_error
        value = report_override(task) if report_override else authored_report(task['context'], task['citations'])
        return value, {'model': 'authored-offline-protocol-fixture', 'usage': {'cost': 0}}
    def forbidden_network_search(*args, **kwargs):
        raise AssertionError('Explicit empty literature_sources must avoid external searches')
    patch = pytest.MonkeyPatch()
    try:
        patch.setattr(model, 'context_model_json', model_fixture)
        patch.setattr(sources, 'search', forbidden_network_search)
        result = execute(run['id'])
    finally:
        patch.undo()
    assert submitted and submitted[0]['language'] == 'zh'
    assert submitted[0]['question'] == body['question']
    # Model transport is a fixture. This explicit receipt promotion stands in
    # for supervisor collection, and is not claimed as a live worker result.
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status = 'completed'
        row.metrics = result
    return run, result, submitted[0]


def case_executor_persists_advice_without_changing_source_state_and_reports_exact_freshness(client, app):
    from services.api.db import Session, Project, Analysis
    from sqlalchemy import select

    project = create(client)
    recorded = recorded_context(client, project)
    before_graph = graph(client, project)
    before_project = ok(client.get(f"/api/projects/{project['id']}"))
    with Session() as session:
        prior_analysis_ids = {row.id for row in session.scalars(select(Analysis).where(Analysis.project_id == project['id']))}
    run, result, task = executor_fixture(client, project)
    assert 'summary' not in result and 'report' not in result
    assert result['report_sha256']
    assert graph(client, project) == before_graph
    assert ok(client.get(f"/api/projects/{project['id']}")) == before_project
    readback = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert readback['status'] == 'completed'
    assert readback['report'] == authored_report(task['context'], task['citations'])
    assert readback['current'] is True and readback['stale_reasons'] == []
    assert readback['artifact_error'] is None
    with Session() as session:
        analyses = list(session.scalars(select(Analysis).where(Analysis.project_id == project['id'])))
        created = [row for row in analyses if row.id not in prior_analysis_ids]
        assert len(created) == 1 and created[0].status == 'proposed'
        assert created[0].data['run_id'] == run['id']
        assert session.get(Project, project['id']).config['controller'] == before_project['config']['controller']
    command(client, project, 'edit_node', targets=[recorded['focus']['id']], title='Source changed after advice')
    stale = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert stale['current'] is False and stale['stale_reasons']
    assert stale['report'] == readback['report']
    assert stale['snapshot'] == readback['snapshot']


def case_saved_intent_snapshot_and_report_cannot_be_silently_rewritten(client, app):
    from services.api.common import project_dir, safe_path
    from research.planning.workspace_analysis import load_analysis_snapshot

    project = create(client)
    recorded_context(client, project)
    run, result, task = executor_fixture(client, project)
    before = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    for body in ({'analysis_snapshot': {}}, {'analysis_intent': 'replacement'},
                 {'question': 'Rewritten accepted intent'}, {'language': 'en'},
                 {'literature_sources': ['arxiv']}, {'provider_id': provider(client)['id']}):
        response = client.patch(f"/api/runs/{run['id']}/configuration", json=body)
        assert response.status_code in (409, 422), response.text
    snapshot = safe_path(project_dir(project['id']), run['config']['analysis_snapshot']['path'])
    snapshot.write_text(snapshot.read_text() + '\n')
    with pytest.raises(ValueError, match='snapshot changed'):
        load_analysis_snapshot(run['config'], project['id'])
    report = safe_path(project_dir(project['id']), result['report_path'])
    report.write_text(json.dumps({'report': {'summary': 'Unaccepted replacement'}}))
    after = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert after['report'] is None and after['artifact_error']
    assert before['report']['summary'] != 'Unaccepted replacement'


def case_run_and_material_changes_without_graph_revision_require_fresh_review(client, app):
    from services.api.db import Session, TaskRun, ToolExecution
    from services.api.common import project_dir, safe_path
    from sqlalchemy import select
    from services.interventions.models import InterventionEffect

    project = create(client)
    recorded = recorded_context(client, project)
    history = recorded_version_and_intervention_history(client, project, recorded)
    reviewed = ok(client.get(endpoint(project) + '/context'))
    original_revision = graph(client, project)['revision']
    configured = provider(client)
    # A real row update represents an independently arriving execution receipt;
    # the record is authored and no numerical execution is claimed here.
    with Session.begin() as session:
        row = session.get(TaskRun, recorded['run_ids'][1])
        row.error = 'A newly recorded diagnostic after context review'
    assert graph(client, project)['revision'] == original_revision
    response = client.post(endpoint(project), json=request_body(client, project,
        provider_id=configured['id'], expected_fingerprint=reviewed['snapshot']['fingerprint']))
    assert response.status_code == 409 and response.json()['detail']['code'] == 'CONTEXT_CHANGED'
    assert ok(client.get(endpoint(project))) == {'runs': []}
    reviewed_tools = ok(client.get(endpoint(project) + '/context'))
    with Session.begin() as session:
        tool = session.scalar(select(ToolExecution).where(ToolExecution.run_id == recorded['run_ids'][1]))
        tool.result = {'error': 'A newer independent saved tool receipt'}
    assert graph(client, project)['revision'] == original_revision
    tools_changed = client.post(endpoint(project), json=request_body(client, project,
        provider_id=configured['id'], expected_fingerprint=reviewed_tools['snapshot']['fingerprint']))
    assert tools_changed.status_code == 409 and tools_changed.json()['detail']['code'] == 'CONTEXT_CHANGED'
    assert ok(client.get(endpoint(project))) == {'runs': []}
    reviewed_effect = ok(client.get(endpoint(project) + '/context'))
    with Session.begin() as session:
        effect = session.get(InterventionEffect, history['resources']['intervention_effects'])
        effect.status = 'failed'
        effect.observation = {'error': 'Authored asynchronous effect failure after context review'}
    assert graph(client, project)['revision'] == original_revision
    effect_changed = client.post(endpoint(project), json=request_body(client, project,
        provider_id=configured['id'], expected_fingerprint=reviewed_effect['snapshot']['fingerprint']))
    assert effect_changed.status_code == 409 and effect_changed.json()['detail']['code'] == 'CONTEXT_CHANGED'
    assert ok(client.get(endpoint(project))) == {'runs': []}
    run, _, _ = executor_fixture(client, project)
    baseline = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert baseline['current'] is True
    method = safe_path(project_dir(project['id']), recorded['method'])
    previous = method.stat()
    # Same-sized external edits bypass FileRevision; modified_ns must still
    # invalidate advice that used the original source contents.
    method.write_text(method.read_text().replace('True', 'None'))
    os.utime(method, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    assert method.stat().st_size == previous.st_size
    assert graph(client, project)['revision'] == original_revision
    stale = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert stale['current'] is False
    assert any('source material' in reason for reason in stale['stale_reasons'])
    assert stale['report'] == baseline['report']


def case_historical_node_focus_remains_readable_after_the_node_is_deleted(client, app):
    from services.api.db import Session, TaskRun
    from research.planning import model
    from services.worker.execute import execute

    project = create(client)
    selected = add_node(client, project, 'Focus that may later be removed')
    configured = provider(client)
    body = request_body(client, project, scope='node', node_id=selected['id'],
                        provider_id=configured['id'], literature_sources=[])
    run = ok(client.post(endpoint(project), json=body))
    patch = pytest.MonkeyPatch()
    try:
        patch.setattr(model, 'context_model_json', lambda driver, messages, workspace, **kwargs: (
            authored_report(json.loads(messages[-1]['content'])['context']),
            {'model': 'authored-offline-protocol-fixture', 'usage': {'cost': 0}}))
        result = execute(run['id'])
    finally:
        patch.undo()
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status, row.metrics = 'completed', result
    before = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert before['current'] is True and before['focus']['node_id'] == selected['id']
    command(client, project, 'delete_node', targets=[selected['id']])
    after = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
    assert after['current'] is False and after['report'] == before['report']
    assert after['focus'] == before['focus']
    assert selected['id'] in after['report']['coverage']['node_ids']


def case_invalid_provider_report_and_transport_failure_never_publish_advice(client, app):
    from research.agents.provider import ProviderError
    from services.api.db import Session, Analysis, TaskRun
    from services.api.common import project_dir
    from sqlalchemy import select

    for failure in ('invalid-reference', 'provider-error'):
        project = create(client, 'Failed analysis ' + failure)
        def invalid(task):
            value = authored_report(task['context'], task['citations'])
            value['findings'][0]['citation_ids'] = ['invented-citation']
            return value
        with pytest.raises((ValueError, ProviderError)):
            executor_fixture(client, project,
                report_override=invalid if failure == 'invalid-reference' else None,
                provider_error=ProviderError('Authored provider outage', code='provider_error', retryable=True)
                               if failure == 'provider-error' else None)
        with Session() as session:
            assert not list(session.scalars(select(Analysis).where(Analysis.project_id == project['id'])))
            rows = list(session.scalars(select(TaskRun).where(TaskRun.project_id == project['id'])))
            assert len(rows) == 1
            assert not (project_dir(project['id']) / rows[0].output_path / 'research_analysis.json').exists()
        readback = ok(client.get(f"/api/runs/{rows[0].id}/research-analysis"))
        assert readback['report'] is None


def case_real_context_protocol_and_worker_receipt_collection_with_authored_transport(client, app):
    """Exercise real JSON context protocol and supervisor receipt collection.

    ModelClient.complete is an authored transport fixture. The executor entry
    point and WorkerLoop.monitor are real; no OS worker/model process is started.
    """
    from research.agents.provider import ModelClient, ProviderError
    from services.api.db import Session, TaskRun, Analysis, asdict, now
    from services.api.common import project_dir
    from services.worker import execute as executor
    from services.worker.main import WorkerLoop
    from sqlalchemy import select

    worker = WorkerLoop()
    configured = provider(client)
    for outcome in ('completed', 'invalid-reference', 'provider-error'):
        project = create(client, 'Receipt collection ' + outcome)
        selected = add_node(client, project, 'Retain execution state while advising')
        before = graph(client, project)
        run = ok(client.post(endpoint(project), json=request_body(client, project,
            scope='node', node_id=selected['id'], provider_id=configured['id'], literature_sources=[])))
        with Session.begin() as session:
            row = session.get(TaskRun, run['id'])
            row.status, row.worker_id, row.started_at = 'running', worker.id, now()
        requests = []
        def transport_fixture(driver, messages, json_mode=True, tools=None, **kwargs):
            assert tools is None, 'This bounded fixture should use the real direct context protocol'
            task = json.loads(messages[-1]['content'])
            requests.append(task)
            if outcome == 'provider-error': raise ProviderError('Authored transport failure', retryable=True)
            value = authored_report(task['context'], task['citations'])
            if outcome == 'invalid-reference': value['findings'][0]['run_ids'] = ['fabricated-run']
            return {'text': json.dumps(value), 'model': 'authored-offline-transport-fixture',
                    'usage': {'input_tokens': 1, 'output_tokens': 1, 'cost': 0}, 'elapsed': 0}
        patch = pytest.MonkeyPatch()
        previous_argv = sys.argv
        try:
            patch.setattr(ModelClient, 'complete', transport_fixture)
            patch.delenv('FOREST_ATTEMPT_ID', raising=False)
            sys.argv = ['isolated-executor', '--run-id', run['id']]
            with pytest.raises(SystemExit) as finished:
                executor.main()
            assert finished.value.code == (0 if outcome == 'completed' else 1)
        finally:
            sys.argv = previous_argv
            patch.undo()
        assert len(requests) == 1
        receipt = json.loads((project_dir(project['id']) / run['output_path'] / 'result.json').read_text())
        assert receipt['status'] == ('completed' if outcome == 'completed' else 'failed')
        with Session() as session:
            value = asdict(session.get(TaskRun, run['id']))
        worker.monitor(value)
        readback = ok(client.get(f"/api/runs/{run['id']}/research-analysis"))
        assert readback['status'] == receipt['status']
        assert graph(client, project) == before
        if outcome == 'completed':
            assert readback['report'] == authored_report(requests[0]['context'], requests[0]['citations'])
            assert readback['current'] is True
            assert list((project_dir(project['id']) / run['output_path'] / 'analysis_context').rglob('direct_response.json'))
        else:
            assert readback['report'] is None and readback['error']
            with Session() as session:
                assert not list(session.scalars(select(Analysis).where(Analysis.project_id == project['id'])))


def case_request_replay_precedes_revision_guard_and_changed_intent_conflicts(client, app):
    from services.api.db import Session, TaskRun
    from sqlalchemy import select

    project = create(client)
    selected = add_node(client, project, 'Retain saved execution intent',
                        config={'kind': 'command', 'command': ['true'], 'extension': {'kept': True}})
    configured = provider(client)
    before_graph = graph(client, project)
    before_project = ok(client.get(f"/api/projects/{project['id']}"))
    body = request_body(client, project, scope='node', node_id=selected['id'],
                        provider_id=configured['id'])
    run = ok(client.post(endpoint(project), json=body))
    assert run['kind'] == 'research_analysis'
    assert run['node_id'] is None and run['branch_id'] is None
    assert graph(client, project) == before_graph
    assert ok(client.get(f"/api/projects/{project['id']}")) == before_project

    # The saved request remains recoverable after its reviewed graph changes.
    command(client, project, 'edit_node', targets=[selected['id']], title='New title after analysis submission')
    replay = ok(client.post(endpoint(project), json=body))
    assert replay['id'] == run['id']
    assert replay['config'] == run['config']
    for field, value in [('question', 'A different question'), ('language', 'zh'),
                         ('scope', 'project'), ('literature_query', 'A new search intent')]:
        changed = client.post(endpoint(project), json={**body, field: value})
        assert changed.status_code == 409, changed.text
    stale = client.post(endpoint(project), json={**body, 'request_id': str(uuid4())})
    assert stale.status_code == 409, stale.text
    assert stale.json()['detail']['code'] == 'REVISION_CONFLICT'
    with Session() as session:
        rows = list(session.scalars(select(TaskRun).where(TaskRun.project_id == project['id'])))
        assert [row.id for row in rows] == [run['id']]


def case_scope_rejects_foreign_entities_and_branch_analysis_has_no_scheduler_binding(client, app):
    from services.api.db import Session, TaskRun
    from sqlalchemy import select

    project, other = create(client), create(client, 'Separate project boundary')
    selected = add_node(client, project, 'Own project node')
    foreign = add_node(client, other, 'Other project node')
    own_branch = graph(client, project)['branches'][0]['id']
    foreign_branch = graph(client, other)['branches'][0]['id']
    configured = provider(client)
    for fields in ({'scope': 'node', 'node_id': foreign['id']},
                   {'scope': 'branch', 'branch_id': foreign_branch},
                   {'scope': 'node'}, {'scope': 'branch'}, {'scope': 'unsupported'}):
        context = client.get(endpoint(project) + '/context', params=fields)
        assert context.status_code in (400, 404, 422), context.text
        submit = client.post(endpoint(project), json=request_body(client, project,
            provider_id=configured['id'], **fields))
        assert submit.status_code in (400, 404, 422), submit.text
    before = graph(client, project)
    branch_run = ok(client.post(endpoint(project), json=request_body(client, project,
        scope='branch', branch_id=own_branch, provider_id=configured['id'])))
    assert branch_run['node_id'] is None and branch_run['branch_id'] is None
    assert branch_run['config']['analysis_branch_id'] == own_branch
    assert graph(client, project) == before
    with Session() as session:
        assert len(list(session.scalars(select(TaskRun).where(TaskRun.project_id == project['id'])))) == 1
        assert not list(session.scalars(select(TaskRun).where(TaskRun.project_id == other['id'])))


CASES = [name.removeprefix('case_') for name in list(globals()) if name.startswith('case_')]


@pytest.mark.parametrize('case', CASES)
def test_isolated_workspace_analysis_scenario(tmp_path, case):
    env = {**os.environ, 'FOREST_DATA_DIR': str(tmp_path / 'data'),
           'FOREST_DATABASE_URL': 'sqlite:///' + str(tmp_path / 'isolated.sqlite'),
           'FOREST_OWNER_TOKEN': 'isolated-analysis-test-owner', 'FOREST_MODEL': '',
           'PYTHONPATH': str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--case', case],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SCENARIO PASSED' in result.stdout


if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()['case_' + selected](client, app)
    print('SCENARIO PASSED:', selected)
