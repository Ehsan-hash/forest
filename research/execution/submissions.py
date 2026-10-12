"""Durable submissions relinquish the caller before reserving child compute."""
from copy import deepcopy

from services.api.db import TaskRun, ToolExecution, now
from services.api.common import get, emit


def request_submission(session, run, action_id, tool, arguments, configuration=None):
    intent = {'action_id': action_id, 'tool': tool, 'arguments': deepcopy(arguments),
              'configuration': deepcopy(configuration or {}), 'accepted_at': now()}
    resource = dict(run.resource or {})
    previous = resource.get('pending_submission')
    if previous and any(previous.get(key) != intent[key] for key in ('action_id', 'tool', 'arguments', 'configuration')):
        raise ValueError('Another accepted submission awaits reconciliation')
    if action_id not in resource.get('submission_receipts', {}):
        resource.update(pending_submission=previous or intent)
        run.resource = resource
        emit(session, run.project_id, 'research_submission_pending',
             {'run_id': run.id, 'action_id': action_id, 'tool': tool})
    return {'status': 'waiting', 'wait_for': {'type': 'research_submission', 'action_id': action_id},
            'resume_after': None, 'exit_code': 0,
            'effect': 'Submission intent retained; the worker admits it after the caller relinquishes its allocation.'}


def admit_submission(session, project, run, pending):
    """Caller owns project -> source locks and has settled actual source usage."""
    from services.worker.scheduler import enqueue, enqueue_nodes
    from services.interventions.application import apply_in_session
    action_id, tool, args = pending['action_id'], pending['tool'], pending['arguments']
    node_id = None
    if tool == 'delegate_research':
        configuration = deepcopy(pending['configuration'])
        branch_id = configuration.pop('_submission_branch_id')
        parent_node_id = configuration.get('parent_node_id')
        command = {'operation': 'add_node', 'params': {'id': action_id, 'branch_id': branch_id,
                   'type': 'task', 'title': args['title'], 'instructions': args['instructions'],
                   'config': configuration}}
        commands = [command]
        if parent_node_id:
            commands.append({'operation': 'add_dependency', 'params': {
                'source': parent_node_id, 'target': action_id, 'relation': 'derived_from'}})
        apply_in_session(session, project, action_id, args['expected_revision'], commands,
                         actor='agent', batch=True)
        node_id = action_id
        rows = enqueue_nodes(session, node_id, 'single', action_id)
    elif tool == 'experiment_run':
        rows = [enqueue(session, project.id, 'experiment',
                        {'branch_id':run.branch_id, **deepcopy(args), **deepcopy(pending.get('configuration',{})),
                         'parent_run_id': run.id}, action_id)]
    elif tool == 'verification_run':
        from services.api.db import Node
        node = get(session, Node, args.get('node_id'))
        if node.project_id != project.id or node.config.get('kind') != 'verification':
            raise ValueError('Select a verification node in this project')
        node_id = node.id
        from research.agents.path_access import _paths
        inherited=deepcopy(pending.get('configuration',{}))
        inherited['denied_readonly_paths']=list(dict.fromkeys(str(path) for path in (
            *_paths(node.config,'denied_readonly_paths'),*_paths(inherited,'denied_readonly_paths'))))
        rows = enqueue_nodes(session, node.id, 'single', args.get('request_id') or action_id,
                             overrides=inherited)
    else:
        raise ValueError('Unknown durable submission tool')
    return {'action_id': action_id, 'tool': tool, 'node_id': node_id,
            'run_ids': [row.id for row in rows], 'handoff_status': 'admitted', 'admitted_at': now()}


def submission_result(session, receipt):
    outcomes = []
    for ident in receipt.get('run_ids', []):
        row = session.get(TaskRun, ident)
        if row is None:
            raise ValueError('Submitted run is missing; reconcile its original receipt')
        outcomes.append({'run_id': row.id, 'status': row.status, 'error': row.error,
                         'exit_code': row.exit_code, 'metrics': deepcopy(row.metrics),
                         'verification_status': (row.resource or {}).get('verification_status', 'unverified')})
    return {**deepcopy(receipt), 'status': 'submission_settled', 'outcomes': outcomes,
            'run_id': receipt.get('run_ids', [None])[0] if receipt.get('run_ids') else None,
            'exit_code': 0, 'effect': 'Actual submitted task states; execution completion is separate from scientific verification.'}


def settle_tool_receipt(session, receipt):
    result = submission_result(session, receipt)
    row = get(session, ToolExecution, receipt['action_id'])
    row.result = {**result, 'control_acceptance': deepcopy(row.result.get('control_acceptance'))}
    row.status = 'completed'
    return result
