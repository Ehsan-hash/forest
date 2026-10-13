"""Explicit, source-bound AI advice beside the research graph."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select

from .db import Session, Project, TaskRun, Provider, Agent, Preference, asdict
from .common import get, error, project_dir, safe_path
from research.planning.workspace_analysis import build_analysis_context, digest

router = APIRouter()


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=0)
    expected_fingerprint: str | None = None
    scope: Literal['project', 'node', 'branch'] = 'project'
    node_id: str | None = None
    branch_id: str | None = None
    question: str = Field(default='', max_length=16000)
    language: Literal['zh', 'en'] = 'en'
    literature_query: str = Field(default='', max_length=1000)
    literature_sources: list[Literal['crossref', 'arxiv']] = Field(default_factory=lambda: ['crossref'], max_length=2)
    literature_limit: int = Field(default=6, ge=1, le=12)
    timeout: float = Field(default=180, gt=0, le=3600)
    provider_id: str | None = None


def analysis_provider(session, project):
    if project.config.get('provider_selection_required') and not project.config.get('provider_id'):
        return {'available': False, 'reason': 'Select a model provider in the project settings'}
    agent = session.scalar(select(Agent).where(Agent.role == 'Researcher'))
    preference = session.get(Preference, 'settings')
    ident = (agent.provider_id if agent else None) or project.config.get('provider_id') or (
        preference.value.get('default_provider_id') if preference else None)
    provider = session.get(Provider, ident) if ident else session.scalar(select(Provider).where(
        Provider.status != 'retired').order_by(Provider.created_at))
    if not provider or provider.status == 'retired':
        return {'available': False, 'id': ident, 'reason': 'Configure an active model provider in Settings'}
    return {'available': True, 'id': provider.id, 'name': provider.name, 'model': provider.model,
            'kind': provider.kind, 'allow_paid': bool(provider.allow_paid and project.budget.get('allow_paid', False))}


def public_analysis_run(run):
    value = asdict(run)
    value['config'] = {k: v for k, v in value['config'].items() if k not in ('provider_snapshot', 'env')}
    return value


@router.get('/api/projects/{ident}/research/analysis/context')
def analysis_context(ident: str, scope: Literal['project', 'node', 'branch'] = 'project',
                     node_id: str | None = None, branch_id: str | None = None):
    with Session() as session:
        project = get(session, Project, ident)
        context = build_analysis_context(session, project, scope, node_id, branch_id)
        context['provider'] = analysis_provider(session, project)
        return context


@router.post('/api/projects/{ident}/research/analysis')
def submit_analysis(ident: str, body: AnalysisRequest):
    from services.worker.scheduler import enqueue, _lock_project
    from research.agents.processes import atomic_json
    intent = body.model_dump(exclude_none=True)
    # Reordering source choices does not turn the same logical request into a new action.
    intent['literature_sources'] = sorted(set(intent['literature_sources']))
    fingerprint = digest(intent)
    with Session.begin() as session:
        project = _lock_project(session, ident)
        previous = session.scalar(select(TaskRun).where(TaskRun.project_id == ident,
                                                       TaskRun.request_id == body.request_id))
        if previous:
            if previous.kind != 'research_analysis' or previous.config.get('analysis_intent') != fingerprint:
                error('REQUEST_ID_CONFLICT', 'This request ID belongs to a different analysis', 409)
            return public_analysis_run(previous)
        if project.revision != body.expected_revision:
            error('REVISION_CONFLICT', 'The research path changed. Review the current context before analysis.', 409,
                  suggestion='Refresh the analysis context and submit the retained question against the reviewed revision.')
        context = build_analysis_context(session, project, body.scope, body.node_id, body.branch_id,
                                         include_materials=True)
        if body.expected_fingerprint and body.expected_fingerprint != context['snapshot']['fingerprint']:
            error('CONTEXT_CHANGED', 'Run results or source records changed. Review the refreshed analysis context.', 409)
        config = {key: value for key, value in intent.items() if key not in (
            'request_id', 'expected_revision', 'expected_fingerprint', 'scope', 'node_id', 'branch_id')}
        config.update(analysis_intent=fingerprint, analysis_scope=body.scope,
                      analysis_node_id=context['focus']['node_id'], analysis_branch_id=context['focus']['branch_id'],
                      analysis_project_revision=project.revision)
        # No Node/Branch binding: advice cannot overwrite execution status or latest_run_id.
        run = enqueue(session, ident, 'research_analysis', config, body.request_id)
        if not run.config.get('provider_snapshot'):
            error('PROVIDER_REQUIRED', 'Configure a model provider in Settings before AI analysis', 422,
                  suggestion='The actual graph and run context remain available without a provider.')
        path = f'runs/{run.id}/analysis_input.json'
        destination = safe_path(project_dir(ident), path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(destination, context)
        run.config = {**run.config, 'analysis_snapshot': {**context['snapshot'], 'path': path,
                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}}
        return public_analysis_run(run)


def analysis_run_summary(run):
    return {'id': run.id, 'status': run.status, 'created_at': run.created_at, 'error': run.error,
            'scope': run.config.get('analysis_scope'), 'node_id': run.config.get('analysis_node_id'),
            'branch_id': run.config.get('analysis_branch_id'), 'question': run.config.get('question', ''),
            'project_revision': run.config.get('analysis_project_revision'),
            'report_available': safe_path(project_dir(run.project_id), run.output_path + '/research_analysis.json').is_file()}


@router.get('/api/projects/{ident}/research/analysis')
def analysis_history(ident: str):
    with Session() as session:
        get(session, Project, ident)
        return {'runs': [analysis_run_summary(run) for run in session.scalars(select(TaskRun).where(
            TaskRun.project_id == ident, TaskRun.kind == 'research_analysis').order_by(TaskRun.created_at.desc()))]}


@router.get('/api/runs/{ident}/research-analysis')
def analysis_result(ident: str):
    with Session() as session:
        run = get(session, TaskRun, ident)
        if run.kind != 'research_analysis': error('INVALID_RUN_KIND', 'This run is not a workspace AI analysis', 422)
        project = get(session, Project, run.project_id)
        path = safe_path(project_dir(run.project_id), run.output_path + '/research_analysis.json')
        value = {}
        artifact_error = None
        if run.status == 'completed':
            if not path.is_file(): artifact_error = 'The saved analysis report is unavailable'
            elif hashlib.sha256(path.read_bytes()).hexdigest() != run.metrics.get('report_sha256'):
                artifact_error = 'The report file changed after analysis; inspect the original history or submit a new analysis'
            else:
                try: value = json.loads(path.read_text())
                except (OSError, ValueError): artifact_error = 'The saved analysis report cannot be read'
        snapshot = run.config.get('analysis_snapshot', {})
        reasons = []
        if snapshot:
            # Freshness does not require the historical focus node/path to still exist.
            current = build_analysis_context(session, project, 'project')
            if project.revision != snapshot['project_revision']: reasons.append('The project or research graph revision changed')
            if current['snapshot']['fingerprint'] != snapshot['fingerprint']: reasons.append('Run state, evidence, or source material changed')
        return {**value, 'run_id': run.id, 'status': run.status, 'error': run.error,
                'report': value.get('report'), 'artifact_error': artifact_error, 'citations': value.get('citations', []),
                'literature_searches': value.get('literature_searches', []), 'snapshot': snapshot,
                'current': bool(snapshot and not reasons), 'stale_reasons': reasons,
                'artifacts': value.get('artifacts', {'context': snapshot.get('path'),
                    'report': run.output_path + '/research_analysis.json' if value else None})}
