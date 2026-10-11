"""Graph-backed recovery decisions, shared by the controller and Agent rounds.

The graph is the durable decision record, not a new execution or resource ledger.
This module never launches work, changes permissions, reads datasets, or invents
scientific evidence. The caller persists the returned decision in its checkpoint
or project transaction and uses the ordinary scheduler for admission.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from research.kernel.graph import execution_edges, _closure, PROPAGATION
from research.planning.route_review import CONTROL_KINDS, review_context_value


LOCAL_BLOCKS = frozenset({
    'input_missing', 'dependency_failed', 'research_route_replan',
    'repeated_error_after_diagnosis', 'no_enabled_tools', 'permission_denied',
    'authorization', 'environment_error', 'experiment_failure',
    'verification_required','verification_rejected','verification_inconclusive',
    'verification_stale','verification_scope_missing','verification_input_changed',
    'verification_scope','verification_source','verification_source_changed',
    'verification_configuration',
})
HARD_RESOURCE_BLOCKS = frozenset({
    'resource_budget_exhausted', 'time_budget_exhausted', 'model_budget_exhausted',
    'gpu_budget_exhausted', 'project_budget_exhausted',
})
REFUTED_STATUSES = frozenset({'refuted','falsified','disproven'})


def _execution_kind(node):
    """Use the scheduler's legacy node-kind inference for feasible routes."""
    config=node.get('config') or {}
    if config.get('kind'): return config['kind']
    if node.get('type')=='verification': return 'verification'
    if config.get('command'): return 'command'
    if node.get('type') in ('experiment','baseline'): return 'experiment'
    if node.get('type')=='analysis': return 'analysis'
    return 'agent'


def refuted_paths(graph, records=()):
    """Honor saved scientific dispositions without inferring them from crashes."""
    roots={node['id'] for node in graph.get('nodes',[])
           if node.get('research_status') in REFUTED_STATUSES}
    rejected_records={record['id'] for record in records if record.get('status') in REFUTED_STATUSES}
    for record in records:
        if record.get('status') in REFUTED_STATUSES and record.get('node_id'):
            roots.add(record['node_id'])
    def references(value):
        if isinstance(value,list):
            return {ident for item in value for ident in references(item)}
        if not isinstance(value,dict): return set()
        result=set()
        if value.get('source_kind') in ('claim','hypothesis','idea') and isinstance(value.get('source_id'),str):
            result.add(value['source_id'])
        if value.get('kind') in ('claim','hypothesis','idea') and isinstance(value.get('id'),str):
            result.add(value['id'])
        for key,item in value.items():
            if key in ('claim_id','hypothesis_id','adopted_idea_id') and isinstance(item,str): result.add(item)
            elif key in ('claim_ids','hypothesis_ids') and isinstance(item,list): result.update(x for x in item if isinstance(x,str))
            else: result.update(references(item))
        return result
    for node in graph.get('nodes',[]):
        cfg=node.get('config') or {}
        claims=cfg.get('claim_ids') or []
        identifiers=[node.get('adopted_idea_id'),cfg.get('hypothesis_id'),cfg.get('claim_id'),
                     *(claims if isinstance(claims,list) else [])]
        identifiers={ident for ident in identifiers if isinstance(ident,str)} | references(cfg) | references(node.get('inputs',[]))
        if rejected_records.intersection(identifiers):
            roots.add(node['id'])
    return roots,_closure(graph,roots,relations=PROPAGATION)


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def research_observations(metrics):
    observations=(metrics or {}).get('observed_metrics',metrics or {})
    return {key:deepcopy(value) for key,value in observations.items() if key not in
        ('usage','elapsed_seconds','elapsed','execution_context','processes','steps',
         'total_tokens','completion_tokens','prompt_tokens','cost_microusd','gpu_seconds')}


def task_signature(node):
    """Task identity survives renamed/cloned nodes and excludes round counters."""
    cfg = node.get('config') or {}
    ignored = {'agent_budget', 'step_budget_policy', 'execution_round',
               'execution_attempt', 'resume_from', 'provider_snapshot',
               'task_context', 'dependency_runs', 'node_id', 'node_revision',
               'instructions', 'node_type', 'node_title', 'inputs', 'project_goal',
               'allow_paid', 'goal_scope', 'execution_context', 'host_snapshot',
               'branch_id', 'project_id', 'source_revision', 'research_recovery', 'input_references',
               '_workspace_source_snapshot', '_recovery_failures', '_next_attempt', 'context_overrides'}
    ignored.add('research_task_signature')
    return _digest(review_context_value({
        'instructions': node.get('instructions', ''),
        'config': {key: value for key, value in cfg.items() if key not in ignored},
        'inputs': node.get('inputs', []),
    }))


def _run_task_signature(run):
    config = run.get('config') or {}
    if config.get('research_task_signature'):
        return config['research_task_signature']
    return task_signature({'type': config.get('node_type'),
        'instructions': config.get('instructions', ''), 'config': config,
        'inputs': config.get('input_references', config.get('inputs', []))})


def semantic_graph_tasks(graph, *, branch_id=None):
    """Identify actual work and dependency bindings, not Graph row churn.

    The full graph retains every node/revision for provenance. A diagnosis key
    must not grant more recovery for a renamed row, revision-only edit, or an
    exact clone of an existing task and its dependencies.
    """
    nodes = {node['id']: node for node in graph.get('nodes', [])}
    signatures = {ident: task_signature(node) for ident, node in nodes.items()}
    active = {branch['id'] for branch in graph.get('branches', [])
              if branch.get('status', 'active') == 'active' and not branch.get('workspace_intervention')}
    tasks = {}
    for ident, node in nodes.items():
        if (node.get('archived') or node.get('branch_id') not in active or
                (branch_id and node.get('branch_id') != branch_id)):
            continue
        dependencies = {_digest({'task_signature': signatures[edge['source']],
            'relation': edge.get('relation', 'depends_on')}): {
            'task_signature': signatures[edge['source']],
            'relation': edge.get('relation', 'depends_on')}
            for edge in graph.get('edges', [])
            if edge.get('target') == ident and edge.get('source') in signatures}
        card = {'task_signature': signatures[ident],
            'dependency_bindings': [dependencies[key] for key in sorted(dependencies)]}
        tasks[_digest(card)] = card
    return [tasks[key] for key in sorted(tasks)]


def _semantic_evidence(cards, nodes):
    """Deduplicate successful observations, preserving their scientific validity.

    Failed execution receipts remain in the complete audit/history; adding a
    failure ID does not establish progress or reset same-state diagnosis.
    """
    observations = {}
    for card in cards:
        node = nodes.get(card['node_id'], {})
        value = {'task_signature': task_signature(node),
            'current_execution': card['current_execution'],
            'scientifically_verified': card['scientifically_verified'],
            'verification_status': card['verification'].get('verification_status'),
            'goal_applicability': {key: card['goal_applicability'].get(key)
                for key in ('status', 'ready')},
            'artifact_hashes': card.get('artifact_hashes', {}),
            'observations': card.get('observations', {})}
        observations[_digest(value)] = value
    return [observations[key] for key in sorted(observations)]


def _semantic_records(records, nodes):
    values = {}
    for record in records:
        value = {key: deepcopy(record.get(key)) for key in ('kind', 'title', 'status', 'data')}
        if isinstance(value['data'], dict):
            value['data'] = {key: item for key, item in value['data'].items()
                             if key not in {'node_id', 'created_at', 'updated_at', 'revision'}}
        if record.get('node_id') in nodes:
            value['task_signature'] = task_signature(nodes[record['node_id']])
        values[_digest(value)] = value
    return [values[key] for key in sorted(values)]


def failed_dependencies_changed(run, runs, nodes):
    """A repaired producer may unblock the same scientific method legitimately."""
    by_run={item['id']:item for item in runs}
    for ident in run.get('dependencies',[]):
        old=by_run.get(ident)
        if not old or old.get('node_id') not in nodes: continue
        node=nodes[old['node_id']]
        current=node.get('latest_run_id') or node.get('last_run_id')
        if current and current!=ident:
            return True
        if old.get('node_revision',0)!=node.get('revision',0):
            return True
    return False


def build_recovery_context(project, graph, runs, *, node_id=None, trigger=None,
                           records=(), working_set_nodes=24):
    """Return compact current decision state plus locators for full history.

    ``runs`` may carry server-resolved ``verification`` and ``goal_applicability``.
    Without those fields, execution is recorded but scientific validity remains
    unverified. ``working_set_nodes`` limits context presentation only; it never
    limits research steps, alternatives, or retained graph/history.
    """
    trigger = deepcopy(trigger or {})
    records=sorted(records,key=lambda record:record.get('id',''))
    config = project.get('config') or {}
    control = config.get('controller') or {}
    nodes = {node['id']: node for node in graph.get('nodes', [])}
    refuted,invalidated=refuted_paths(graph,records)
    ordered = sorted(runs, key=lambda run: (run.get('created_at', ''), run['id']))
    by_node = {}
    for run in ordered:
        if run.get('node_id'):
            by_node.setdefault(run['node_id'], []).append(run)
    focus_ids = {ident for ident in [node_id, *trigger.get('node_ids', [])] if ident in nodes}
    ancestors = _closure(graph, focus_ids, reverse=True, relations=PROPAGATION) if focus_ids else set()
    active = {branch['id'] for branch in graph.get('branches', [])
              if branch.get('status', 'active') == 'active' and not branch.get('workspace_intervention')}
    edges = execution_edges(graph)
    failed_tasks, failures, completed, valid_evidence, stale_evidence = {}, [], set(), [], []
    accepted_nodes=set()
    for ident, node in nodes.items():
        attempts = by_node.get(ident, [])
        latest = next((run for run in reversed(attempts)
                       if run.get('node_revision', 0) == node.get('revision', 0)), None)
        authoritative = node.get('latest_run_id') or node.get('last_run_id')
        if authoritative:
            latest = next((run for run in attempts if run['id'] == authoritative), None)
        current = bool(latest and latest.get('node_revision', 0) == node.get('revision', 0)
                       and node.get('results_current', True)
                       and node.get('deliverable_status') != 'needs_update'
                       and not node.get('needs_rerun') and not node.get('requires_revalidation'))
        if latest and current and latest.get('status') == 'completed' and node.get('execution_status') == 'completed':
            verdict=(latest.get('verification') or {}).get('verification_status')
            if verdict=='accepted': accepted_nodes.add(ident)
            if _execution_kind(node)!='verification' or verdict=='accepted':
                completed.add(ident)
            elif verdict in ('rejected','inconclusive'):
                failed_tasks.setdefault(task_signature(node),[]).append(latest['id'])
        signature = task_signature(node)
        for run in attempts:
            card = {'run_id': run['id'], 'node_id': ident,
                    'node_revision': run.get('node_revision', 0),
                    'output_path': run.get('output_path'), 'status': run.get('status'),
                    'current_execution': bool(current and latest and run['id'] == latest['id'])}
            if run.get('status') == 'completed':
                metrics=run.get('metrics') or {}
                observations=research_observations(metrics)
                card.update(verification=deepcopy(run.get('verification', {'verification_status': 'unverified'})),
                    goal_applicability=deepcopy(run.get('goal_applicability', {'status': 'unknown', 'ready': False})),
                    artifact_hashes=deepcopy(metrics.get('artifact_hashes', {})), observations=observations)
                accepted = card['verification'].get('verification_status') == 'accepted'
                card['scientifically_verified'] = bool(card['current_execution'] and accepted
                    and card['goal_applicability'].get('ready') and ident not in invalidated)
                if ident in invalidated: card['hypothesis_applicability']='Requires revision after recorded scientific refutation'
                (valid_evidence if card['current_execution'] else stale_evidence).append(card)
            if run.get('status') in ('failed', 'interrupted') or (
                    run.get('status') == 'waiting_input' and (run.get('resource') or {}).get('blocked_reason') in LOCAL_BLOCKS):
                card.update(error=run.get('error'), blocked_reason=(run.get('resource') or {}).get('blocked_reason'),
                    classification=deepcopy((run.get('resource') or {}).get('error_recovery')),
                    observations=research_observations(run.get('metrics')),
                    scientific_disposition='Unresolved execution or access failure; does not by itself refute a hypothesis')
                failures.append(card)
                # A meaningful configuration change can repair a path. Renaming
                # or adding a copy cannot erase the failed path's identity.
                if failed_dependencies_changed(run,ordered,nodes):
                    continue
                if run.get('node_revision', 0) == node.get('revision', 0):
                    failed_tasks.setdefault(signature, []).append(run['id'])
                elif (run.get('config') or {}).get('instructions') is not None:
                    failed_tasks.setdefault(_run_task_signature(run), []).append(run['id'])
    blocked = set(focus_ids)
    scoped = [node for node in nodes.values() if not node.get('archived')
              and node.get('branch_id') in active
              and (not control.get('branch_id') or node.get('branch_id') == control['branch_id'])]
    ready, unavailable = [], []
    for node in sorted(scoped, key=lambda n: (n.get('created_at', ''), n['id'])):
        ident = node['id']
        if ident in completed or node.get('execution_status') in ('queued', 'running', 'paused', 'pausing', 'waiting'):
            continue
        reason = None
        if ident in blocked:
            reason = 'affected_path_requires_diagnosis'
        elif (ident in invalidated and (node.get('config') or {}).get('research_activity') not in
                ('negative_result_analysis','hypothesis_revision')):
            reason = 'recorded_hypothesis_refuted'
        elif task_signature(node) in failed_tasks:
            reason = 'unchanged_failed_task'
        elif node.get('execution_status') in ('failed', 'interrupted', 'waiting_input', 'budget_exhausted', 'cancelled'):
            reason = 'path_requires_repair'
        elif any(edge['source'] not in completed for edge in edges if edge['target'] == ident):
            reason = 'dependency_not_current'
        elif (_execution_kind(node)!='verification' and
              (config.get('verification_policy')=='required' or (node.get('config') or {}).get('verification_policy')=='required') and
              any(ref.get('node_id') not in accepted_nodes for ref in node.get('inputs',[])
                  if isinstance(ref,dict) and ref.get('node_id') in nodes)):
            reason = 'dependency_not_verified'
        elif _execution_kind(node) == 'agent' and (node.get('config') or {}).get('tools') == []:
            reason = 'no_authorized_tools'
        if reason:
            unavailable.append({'node_id': ident, 'reason': reason})
        else:
            ready.append(ident)
    relevant = list(dict.fromkeys([*sorted(focus_ids), *sorted(ancestors - focus_ids), *ready]))
    chosen = relevant[:working_set_nodes]
    node_cards = []
    for ident in chosen:
        node = nodes[ident]
        node_cards.append({key: deepcopy(node.get(key)) for key in (
            'id', 'branch_id', 'type', 'title', 'revision', 'instructions', 'config', 'inputs',
            'execution_status', 'research_status', 'deliverable_status', 'results_current')})
    pid = project['id']
    semantic_tasks = semantic_graph_tasks(graph, branch_id=control.get('branch_id'))
    semantic_evidence = _semantic_evidence([*valid_evidence, *stale_evidence], nodes)
    semantic_records = _semantic_records(records, nodes)
    # New diagnostic runs do not reset this key. Only goal, task/evidence,
    # permissions, or actual research state changes permit a fresh recovery.
    key = _digest(review_context_value({
        'goal': project.get('goal'), 'goal_contract': config.get('goal_contract'),
        'scope': control.get('branch_id'),
        'tasks': semantic_tasks,
        'evidence': semantic_evidence,
        'records': semantic_records,
        'refuted_tasks': sorted({task_signature(nodes[ident]) for ident in invalidated if ident in nodes}),
        'permissions': {key: config.get(key) for key in ('tools', 'agent_execution')},
        # Diagnostics describe the same stalled scientific state. Changing an
        # error category (or reaching a work-round boundary) must not grant a
        # fresh recovery allowance without changed goals, evidence or paths.
        'focus_tasks': sorted({task_signature(nodes[ident]) for ident in focus_ids}),
    }))
    evidence_fingerprint = _digest({'observations': semantic_evidence, 'records': semantic_records})
    audit_evidence_fingerprint = _digest({'current': valid_evidence, 'historical': stale_evidence,
                                         'failures': failures, 'records': list(records)})
    return review_context_value({
        'schema_version': 1, 'state_key': key, 'evidence_fingerprint': evidence_fingerprint,
        'audit_evidence_fingerprint': audit_evidence_fingerprint, 'semantic_tasks': semantic_tasks,
        'goal_contract': {'goal': project.get('goal', ''), 'revision': project.get('revision', graph.get('revision')),
            'contract': deepcopy(config.get('goal_contract', {}))},
        'trigger': trigger, 'focus_node_ids': sorted(focus_ids), 'ancestor_node_ids': sorted(ancestors - focus_ids),
        'graph_revision': graph.get('revision'), 'nodes': node_cards,
        'edges': [edge for edge in graph.get('edges', []) if edge['source'] in chosen or edge['target'] in chosen],
        'selected_node_ids': sorted(focus_ids), 'alternative_ready_node_ids': ready,
        'refuted_node_ids': sorted(refuted), 'invalidated_path_node_ids': sorted(invalidated),
        'failed_task_signatures': failed_tasks, 'unavailable_paths': unavailable,
        'evidence': [card for card in valid_evidence if card['node_id'] in relevant],
        'historical_evidence': [card for card in stale_evidence if card['node_id'] in ancestors],
        'failures': [card for card in failures if card['node_id'] in ancestors or card['node_id'] in {item['node_id'] for item in unavailable}],
        'research_records': [record for record in records if not record.get('node_id') or record['node_id'] in ancestors],
        'checkpoint_evidence': {'current': valid_evidence, 'historical': stale_evidence,
            'failures': failures, 'records': list(records)},
        'shared_resource_ledger': {'project_id': pid, 'budget': deepcopy(project.get('budget', {})),
            'run_count': len(runs), 'usage_locator': f'/api/projects/{pid}/usage',
            'rule': 'All existing branches and recovery work use the same project admission and model accounting.'},
        'history_access': {'graph': f'/api/projects/{pid}/graph', 'runs': f'/api/projects/{pid}/runs',
            'research': f'/api/projects/{pid}/research', 'omitted_node_ids': relevant[working_set_nodes:],
            'total_nodes': len(nodes), 'total_runs': len(runs)},
        'decision_constraints': [
            'Use current task-bound evidence; historical success remains historical.',
            'Preserve the goal, protocol, raw observations, negative evidence and resource ledger.',
            'Diagnose a concrete cause and materially repair or select a feasible existing path.',
            'Do not repeat a rejected operation or grant permissions during recovery.',
            'Unchanged validated files retain hashes and locators; reread only for a decision or changed content.',
            'Do not manufacture progress with summaries, duplicate nodes or unused files.',
        ],
    })


def research_recovery_context(session, project, *, node_id=None, trigger=None):
    """Load authoritative graph and evidence using the caller's DB transaction."""
    from sqlalchemy import select
    from services.api.db import TaskRun, Hypothesis, ResearchClaim, DatasetAsset, ExperimentSpec, asdict
    from services.api.common import graph_from_db
    from services.api.verification import verification_for_run
    from services.interventions.applicability import goal_applicability
    rows = list(session.scalars(select(TaskRun).where(TaskRun.project_id == project.id)))
    graph=graph_from_db(session,project)
    nodes={node['id']:node for node in graph.get('nodes',[])}
    latest={}
    for row in sorted(rows,key=lambda run:(run.created_at,run.id)):
        if row.node_id in nodes and row.node_revision==nodes[row.node_id].get('revision',0):
            latest[row.node_id]=row.id
    for node in nodes.values():
        authoritative=node.get('latest_run_id') or node.get('last_run_id')
        if authoritative: latest[node['id']]=authoritative
    checked_producers={row.resource.get('verification_binding',{}).get('producer_run_id')
                       for row in rows if row.kind=='verification'}
    runs = []
    for row in rows:
        run = asdict(row)
        if row.status == 'completed':
            current=latest.get(row.node_id)==row.id
            if current:
                run['verification'] = verification_for_run(session,row) if (
                    row.kind=='verification' or row.id in checked_producers) else {'verification_status':'unverified'}
                run['goal_applicability'] = goal_applicability(session,row)
            else:
                # Historical data stays addressable and can be reviewed on
                # demand. It cannot regain current acceptance merely because
                # a recovery context was rebuilt. Avoid rereading every old
                # artifact/check for every bounded execution round.
                run['verification']={'verification_status':'historical',
                    'recorded_status':row.resource.get('verification_status','unverified')}
                run['goal_applicability']={'status':'historical','ready':False}
        runs.append(run)
    records = []
    for model in (Hypothesis, ResearchClaim, DatasetAsset, ExperimentSpec):
        for row in session.scalars(select(model).where(model.project_id == project.id)):
            if row.data.get('origin') == 'research_controller':
                continue  # Diagnostic attempts are history, not new evidence.
            records.append({'id': row.id, 'kind': model.__tablename__, 'title': row.title,
                'status': row.status, 'revision': row.revision, 'data': row.data,
                'node_id': row.data.get('node_id')})
    return build_recovery_context(asdict(project), graph, runs,
        node_id=node_id, trigger=trigger, records=records)


def recovery_decision(control, context, *, autonomous=True, max_diagnostics=2):
    """Choose another route or a finite diagnosis for this unchanged state.

    The fault fuse applies to repeated recovery of one identical observed state,
    never to total project/Agent steps. Progress creates a new state key. A
    blocked decision is a saved pause, not scientific failure or completion.
    """
    previous = control.get('recovery') or {}
    attempts = previous.get('diagnostic_attempts', 0) if previous.get('state_key') == context['state_key'] else 0
    saved_context={key:deepcopy(value) for key,value in context.items() if key!='shared_resource_ledger'}
    recovery = {'state_key': context['state_key'], 'diagnostic_attempts': attempts,
        'trigger': context.get('trigger'), 'context': saved_context}
    category = (context.get('trigger') or {}).get('category')
    if category in HARD_RESOURCE_BLOCKS:
        return {'action': 'pause', 'reason': 'Actual resource hard budget prevents admission.', 'recovery': recovery}
    if context['alternative_ready_node_ids']:
        return {'action': 'execute_alternative', 'node_ids': context['alternative_ready_node_ids'],
            'reason': 'Continue an existing feasible route using the preserved graph and project ledger.', 'recovery': recovery}
    if not autonomous:
        return {'action': 'pause', 'reason': 'No executable authorized alternative; autonomous replanning is disabled.', 'recovery': recovery}
    if attempts >= max_diagnostics:
        return {'action': 'pause', 'reason': 'Repeated diagnosis found no changed evidence or feasible path; saved state requires a concrete repair or external input.', 'recovery': recovery}
    recovery['diagnostic_attempts'] += 1
    return {'action': 'diagnose', 'reason': 'Diagnose and replan from the current graph and preserved evidence.', 'recovery': recovery}


def enqueue_recovery_alternatives(session, project, context, *, source_run_id):
    """Admit preexisting alternatives once, without enabling the controller.

    An Agent or controller can recover to work already approved in the selected
    graph. New graph edits still require the normal planning/owner path. The
    caller saves its source checkpoint and this result in the same transaction.
    Replaying a checkpoint reuses admission request identities and the ledger.
    """
    from sqlalchemy import select
    from services.api.db import TaskRun
    from services.worker.scheduler import enqueue_nodes, _lock_project
    project = _lock_project(session, project.id)
    source = session.get(TaskRun, source_run_id)
    if source is None or source.project_id != project.id:
        raise ValueError('Recovery source must belong to the same project')
    if source.status in ('cancelled', 'completed'):
        return []
    def request_for(ident):
        return 'path-recovery:'+_digest({'source':source_run_id,'state':context['state_key'],'node':ident})[:40]
    existing = []
    for ident in context.get('alternative_ready_node_ids', []):
        request = request_for(ident)
        old = session.scalar(select(TaskRun).where(TaskRun.project_id == project.id,
            TaskRun.request_id == request+':'+ident))
        if old is not None:
            existing.append(old)
    if existing:
        return existing
    current = research_recovery_context(session, project,
        node_id=source.node_id, trigger=context.get('trigger'))
    eligible = set(current['alternative_ready_node_ids'])
    rows = []
    for ident in context.get('alternative_ready_node_ids', []):
        if ident not in eligible:
            continue  # User edits/permissions/dependencies can supersede advice.
        request = request_for(ident)
        rows.extend(enqueue_nodes(session, ident, 'single', request))
        break  # Sequential decision; independent batches remain controller policy.
    return rows
