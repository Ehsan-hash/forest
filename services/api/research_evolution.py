"""Live scientific evolution uses existing project transactions and run history."""
from copy import deepcopy
from fastapi import APIRouter, Body
from sqlalchemy import select
from .db import Session, Project, CommandReceipt, asdict
from .common import get, error, emit
from services.worker.scheduler import _lock_project
from services.research_evolution.models import EvaluationProtocolVersion
from services.research_evolution.service import goal_context, current_goal, revise_protocol

router=APIRouter()


def _intent(body):
    return deepcopy({key:value for key,value in body.items()
        if key not in ('request_id','expected_revision','expected_goal_revision','expected_protocol_revision')})


def _replay(session, project, body, operation):
    ident=body.get('request_id')
    if ident is None: return None
    if not isinstance(ident,str) or not ident.strip() or len(ident)>160:
        error('INVALID_REQUEST_ID','Use a nonempty request ID of at most 160 characters',422)
    row=session.scalar(select(CommandReceipt).where(CommandReceipt.project_id==project.id,
        CommandReceipt.request_id==ident))
    if not row: return None
    if row.response.get('operation')!=operation or row.response.get('intent')!=_intent(body):
        error('REQUEST_ID_CONFLICT','Request identity belongs to a different scientific revision',409)
    return deepcopy(row.response['result'])


def _receipt(session, project, body, operation, result):
    if body.get('request_id'):
        session.add(CommandReceipt(project_id=project.id,request_id=body['request_id'],
            response={'operation':operation,'intent':_intent(body),'result':deepcopy(result)}))
    return result


def _validate_goal(body):
    if 'objective' in body and (not isinstance(body['objective'],str) or not body['objective'].strip()):
        error('INVALID_GOAL','Supply a nonempty scientific objective',422)
    if 'contract' in body and not isinstance(body['contract'],dict):
        error('INVALID_GOAL','Goal Contract must be an editable object',422)
    if 'success_criteria' in body and not isinstance(body['success_criteria'],list):
        error('INVALID_GOAL','Success criteria must be an array',422)
    for key in ('evidence_run_ids','affected_branch_ids'):
        if key in body and (not isinstance(body[key],list) or any(not isinstance(v,str) for v in body[key])):
            error('INVALID_GOAL',key+' must contain actual IDs',422)
    if 'reason' in body and (not isinstance(body['reason'],str) or not body['reason'].strip()):
        error('INVALID_GOAL','Record the scientific reason for the revision',422)


@router.get('/api/projects/{project_id}/goal-contract')
def read_goal_contract(project_id:str):
    with Session.begin() as session:
        project=_lock_project(session,project_id)
        return goal_context(session,project)


@router.post('/api/projects/{project_id}/goal-contract/versions')
def revise_goal_contract(project_id:str,body:dict=Body(...)):
    _validate_goal(body)
    with Session.begin() as session:
        project=_lock_project(session,project_id)
        replay=_replay(session,project,body,'goal_contract_revision')
        if replay is not None: return replay
        current=current_goal(session,project)
        if body.get('expected_revision',project.revision)!=project.revision or body.get(
                'expected_goal_revision',current.revision)!=current.revision:
            error('REVISION_CONFLICT','Scientific goal or project changed',409)
        config=deepcopy(project.config)
        contract={**deepcopy(current.contract),**deepcopy(body.get('contract') or {})}
        if 'success_criteria' in body: contract['success_criteria']=deepcopy(body['success_criteria'])
        intent={'goal':body.get('objective',project.goal),'config':{**config,'goal_contract':contract},
            'reason':body.get('reason','Scientific objective revised'),
            'evidence_run_ids':body.get('evidence_run_ids',[]),'affected_branch_ids':body.get('affected_branch_ids',[])}
        observed=project.revision
        project.goal=intent['goal'];project.config=intent['config'];project.revision+=1
        from services.interventions.configuration import record_change
        intervention=record_change(session,project,observed,intent,body.get('request_id'))
        emit(session,project.id,'project_changed',{'revision':project.revision})
        result={**goal_context(session,project),'project_revision':project.revision,'intervention':intervention}
        return _receipt(session,project,body,'goal_contract_revision',result)


@router.get('/api/projects/{project_id}/evaluation-protocols')
def read_protocols(project_id:str):
    with Session() as session:
        get(session,Project,project_id)
        return [asdict(row) for row in session.scalars(select(EvaluationProtocolVersion).where(
            EvaluationProtocolVersion.project_id==project_id).order_by(EvaluationProtocolVersion.created_at))]


@router.post('/api/projects/{project_id}/evaluation-protocols')
def update_protocol(project_id:str,body:dict=Body(...)):
    with Session.begin() as session:
        project=_lock_project(session,project_id)
        replay=_replay(session,project,body,'evaluation_protocol_revision')
        if replay is not None: return replay
        if body.get('expected_revision',project.revision)!=project.revision:
            error('REVISION_CONFLICT','Project changed before evaluation revision',409)
        return _receipt(session,project,body,'evaluation_protocol_revision',asdict(revise_protocol(session,project,body)))
