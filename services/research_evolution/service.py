"""Scientific revisions preserve runs and never restart project accounting."""
from copy import deepcopy
from sqlalchemy import select
from services.api.db import Branch, Node, TaskRun, asdict, uid
from services.api.common import error, emit
from .models import GoalContractVersion, EvaluationProtocolVersion


def contract_value(project):
    original=deepcopy(project.config.get('goal_contract') or {})
    # Older project configurations were open JSON. Preserve an unusual saved
    # value for inspection instead of making the whole migration fail.
    return original if isinstance(original,dict) else {'legacy_contract_value':original}


def _refs(session, project, run_ids, branch_ids=None):
    if branch_ids is None: branch_ids=[]
    if not isinstance(run_ids,list) or any(not isinstance(value,str) for value in run_ids):
        error('INVALID_EVIDENCE', 'Evidence must contain actual run IDs', 422)
    for ident in run_ids:
        row=session.get(TaskRun,ident)
        if row is None or row.project_id!=project.id:
            error('INVALID_EVIDENCE', 'Evidence run belongs to another project or is missing: '+ident, 422)
    if not isinstance(branch_ids,list) or any(not isinstance(value,str) for value in branch_ids):
        error('INVALID_BRANCHES','Affected branches must contain actual branch IDs',422)
    for ident in branch_ids:
        row=session.get(Branch,ident)
        if row is None or row.project_id!=project.id:
            error('INVALID_BRANCHES','Affected branch belongs to another project or is missing: '+ident,422)


def current_goal(session, project, *, initialize=True):
    current=session.scalar(select(GoalContractVersion).where(GoalContractVersion.project_id==project.id)
        .order_by(GoalContractVersion.revision.desc()).limit(1))
    if current is not None or not initialize: return current
    contract=contract_value(project)
    criteria=contract.get('success_criteria',contract.get('success_conditions',[]))
    if not isinstance(criteria,list): criteria=[criteria]
    current=GoalContractVersion(project_id=project.id,revision=1,project_revision=project.revision,
        objective=project.goal,success_criteria=criteria,contract=contract,
        reason='Initial scientific objective retained from the existing project',changes={},
        evidence_run_ids=[],affected_branch_ids=[])
    session.add(current);session.flush()
    return current


def goal_context(session, project):
    current=current_goal(session,project)
    versions=list(session.scalars(select(GoalContractVersion).where(GoalContractVersion.project_id==project.id)
        .order_by(GoalContractVersion.revision)))
    return {'current':asdict(current),'versions':[asdict(row) for row in versions],
        'history_endpoint':'/api/projects/'+project.id+'/goal-contract',
        'rule':'The current objective is editable; historical results retain their original execution conditions.'}


def record_goal_transition(session, project, *, reason, evidence_run_ids=None, affected_branch_ids=None):
    """Caller holds the project writer lock, including the existing PATCH path."""
    previous=current_goal(session,project)
    contract=contract_value(project)
    criteria=contract.get('success_criteria',contract.get('success_conditions',[]))
    if not isinstance(criteria,list): criteria=[criteria]
    if previous.objective==project.goal and previous.success_criteria==criteria and previous.contract==contract:
        return previous
    evidence=evidence_run_ids or [];branches=affected_branch_ids or []
    _refs(session,project,evidence,branches)
    evidence=list(dict.fromkeys(evidence));branches=list(dict.fromkeys(branches))
    row=GoalContractVersion(project_id=project.id,revision=previous.revision+1,parent_version_id=previous.id,
        project_revision=project.revision,objective=project.goal,success_criteria=criteria,contract=contract,
        reason=str(reason or 'Scientific objective or success criteria revised'),
        changes={'objective':{'before':previous.objective,'after':project.goal},
            'success_criteria':{'before':previous.success_criteria,'after':criteria},
            'contract':{'before':previous.contract,'after':contract}},
        evidence_run_ids=evidence,affected_branch_ids=branches)
    session.add(row);session.flush()
    control=deepcopy(project.config.get('controller') or {})
    if control.get('status')=='completed':
        control.update(status='running',phase='PLAN',reason=None,
            reopened_by_goal_version=row.id,last_rationale=row.reason)
        project.config={**project.config,'controller':control}
    for node in session.scalars(select(Node).where(Node.project_id==project.id)):
        if not branches or node.branch_id in branches:
            node.context_overrides={**node.context_overrides,'needs_refresh':True}
    emit(session,project.id,'goal_contract_revised',{'version_id':row.id,'revision':row.revision,
        'parent_version_id':previous.id,'reason':row.reason,'evidence_run_ids':evidence,'affected_branch_ids':branches})
    return row


def bind_goal_contract(session, project, config):
    """Bind new work to a version without altering previous run configurations."""
    row=current_goal(session,project)
    return {**config,'goal_contract_version_id':row.id,'goal_contract_revision':row.revision,
        'goal_contract':{'objective':row.objective,'success_criteria':deepcopy(row.success_criteria),
            'contract':deepcopy(row.contract)}}


def validate_graph_evolution(session, project, graph):
    for branch in graph.get('branches',[]):
        history=branch.get('evolution_history') or []
        original=session.get(Branch,branch['id'])
        previous=(original.extra or {}).get('evolution_history',[]) if original else []
        additions=history[len(previous):]
        if not additions: continue
        for entry in additions: _refs(session,project,entry.get('evidence_run_ids',[]),[])
        entry=additions[-1]
        control=deepcopy(project.config.get('controller') or {})
        if control.get('status')=='completed' and entry.get('status')=='active':
            control.update(status='running',phase='PLAN',reason=None,reopened_branch_id=branch['id'],
                last_rationale=entry.get('reason'))
            project.config={**project.config,'controller':control}


def revise_protocol(session, project, body):
    protocol_id=body.get('protocol_id') or uid()
    previous=session.scalar(select(EvaluationProtocolVersion).where(
        EvaluationProtocolVersion.project_id==project.id,EvaluationProtocolVersion.protocol_id==protocol_id)
        .order_by(EvaluationProtocolVersion.revision.desc()).limit(1))
    expected=body.get('expected_protocol_revision',previous.revision if previous else 0)
    if expected!=(previous.revision if previous else 0): error('REVISION_CONFLICT','Evaluation protocol changed',409)
    definition=body.get('definition')
    if not isinstance(definition,dict) or not definition: error('INVALID_PROTOCOL','Supply an editable scientific protocol definition',422)
    feedback=body.get('feedback_run_ids') or []
    _refs(session,project,feedback,[])
    feedback=list(dict.fromkeys(feedback))
    # Prior feedback is never erased by a later empty field or revision.
    feedback=list(dict.fromkeys([*(previous.feedback_run_ids if previous else []),*feedback]))
    # Giving reused evaluation data a new protocol ID does not make it unseen.
    # A distinct declared data/version/split can still be a fresh confirmation.
    scope_keys=('dataset','dataset_version','split')
    scope={key:definition[key] for key in scope_keys if key in definition}
    if 'dataset' in scope and 'split' in scope:
        for prior in session.scalars(select(EvaluationProtocolVersion).where(
                EvaluationProtocolVersion.project_id==project.id)):
            prior_scope={key:prior.definition[key] for key in scope_keys if key in prior.definition}
            if prior_scope==scope:
                feedback=list(dict.fromkeys([*feedback,*prior.feedback_run_ids]))
    classification='adaptive' if feedback else body.get('classification','exploratory')
    if classification not in ('adaptive','exploratory','independent_confirmation'):
        error('INVALID_CLASSIFICATION','Use adaptive, exploratory or independent_confirmation',422)
    row=EvaluationProtocolVersion(project_id=project.id,protocol_id=protocol_id,
        revision=previous.revision+1 if previous else 1,parent_version_id=previous.id if previous else None,
        name=body.get('name') or (previous.name if previous else 'Evaluation protocol'),definition=deepcopy(definition),
        reason=body.get('reason') or 'Scientific evaluation revised',feedback_run_ids=feedback,classification=classification)
    session.add(row);session.flush()
    project.revision+=1
    emit(session,project.id,'evaluation_protocol_revised',{'version_id':row.id,'protocol_id':protocol_id,
        'revision':row.revision,'classification':classification,'feedback_run_ids':feedback})
    return row


def bind_evaluation_context(session, project, config):
    version_id=config.get('evaluation_protocol_version_id')
    if not version_id: return config
    row=session.get(EvaluationProtocolVersion,version_id)
    if row is None or row.project_id!=project.id: error('INVALID_PROTOCOL','Evaluation protocol version belongs to another project or is missing',422)
    declaration=deepcopy(config.get('comparison_signature') or {})
    declaration['evaluation_protocol']={'id':row.protocol_id,'revision':row.revision,'definition':deepcopy(row.definition)}
    return {**config,'comparison_signature':declaration,'evaluation_context':{
        'protocol_version_id':row.id,'protocol_id':row.protocol_id,'protocol_revision':row.revision,
        'classification':row.classification,'feedback_run_ids':deepcopy(row.feedback_run_ids)}}
