"""Portable scientific versions, retaining the conditions of historical runs."""
from sqlalchemy import delete, select
from services.api.db import asdict
from .models import GoalContractVersion, EvaluationProtocolVersion


MODELS={'goal_contract_versions':GoalContractVersion,'evaluation_protocol_versions':EvaluationProtocolVersion}


def export_evolution(session, project_id):
    return {key:[asdict(row) for row in session.scalars(select(model).where(model.project_id==project_id)
        .order_by(model.revision))] for key,model in MODELS.items()}


def evolution_ids(history):
    # protocol_id is a project-scoped scientific name, not an entity identity.
    return [row['id'] for key in MODELS for row in history.get(key,[])]


def validate_evolution(history):
    project_id=history['project']['id']
    runs={row['id'] for row in history.get('runs',[])}
    branches={row['id'] for row in history['graph'].get('branches',[])}
    all_ids={row['id'] for key in ('nodes','edges','branches') for row in history['graph'][key]}
    all_ids.update(runs)
    for rows in [*history.get('resources',{}).values(),history.get('papers',[])]:
        all_ids.update(row['id'] for row in rows)
    version_ids={}
    for key in MODELS:
        rows=history.get(key,[])
        if not isinstance(rows,list): raise ValueError(key+' must be an array')
        ids={}; revisions=set()
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in all_ids or row['id'] in ids:
                raise ValueError('Scientific version identities must be globally unique')
            if row.get('project_id')!=project_id: raise ValueError('Scientific version belongs to another project')
            if not isinstance(row.get('revision'),int) or row['revision']<1:
                raise ValueError('Scientific version revision must be positive')
            group=row.get('protocol_id') if key=='evaluation_protocol_versions' else project_id
            if not isinstance(group,str) or (group,row['revision']) in revisions:
                raise ValueError('Scientific version counters must be unique')
            revisions.add((group,row['revision']));ids[row['id']]=row
            if key=='goal_contract_versions':
                if not isinstance(row.get('objective'),str) or not isinstance(row.get('contract'),dict) or not isinstance(row.get('success_criteria'),list):
                    raise ValueError('Goal Contract fields are invalid')
                evidence=row.get('evidence_run_ids',[])
                if not isinstance(row.get('affected_branch_ids',[]),list) or not set(row.get('affected_branch_ids',[]))<=branches:
                    raise ValueError('Goal Contract refers to a missing historical branch')
            else:
                if not isinstance(row.get('definition'),dict) or row.get('classification') not in ('adaptive','exploratory','independent_confirmation'):
                    raise ValueError('Evaluation protocol fields are invalid')
                evidence=row.get('feedback_run_ids',[])
            if not isinstance(evidence,list) or not set(evidence)<=runs:
                raise ValueError('Scientific version refers to missing historical evidence')
        for row in rows:
            parent=ids.get(row.get('parent_version_id'))
            if row['revision']>1 and not row.get('parent_version_id'):
                raise ValueError('Scientific version history is incomplete')
            if row['revision']==1 and row.get('parent_version_id'):
                raise ValueError('An initial scientific version cannot have a parent')
            if row.get('parent_version_id') and (not parent or parent['revision']!=row['revision']-1 or (
                    key=='evaluation_protocol_versions' and parent['protocol_id']!=row['protocol_id'])):
                raise ValueError('Scientific version parent binding is invalid')
        version_ids[key]=set(ids);all_ids.update(ids)
    for row in history.get('runs',[]):
        config=row.get('config') or {}
        for field,key in (('goal_contract_version_id','goal_contract_versions'),
                ('evaluation_protocol_version_id','evaluation_protocol_versions')):
            if config.get(field) and config[field] not in version_ids[key]:
                raise ValueError('Historical run is missing its original scientific version')


def import_evolution(session, project_id, history, remap):
    # make_project installs a legacy baseline. A backup with real version
    # history replaces that new baseline without synthesizing a newer version.
    if history.get('goal_contract_versions'):
        session.execute(delete(GoalContractVersion).where(GoalContractVersion.project_id==project_id))
    for key,model in MODELS.items():
        for original in history.get(key,[]):
            fields={name:remap(value) for name,value in original.items() if name in model.__table__.columns.keys()}
            if key=='evaluation_protocol_versions':fields['protocol_id']=original['protocol_id']
            fields['project_id']=project_id
            session.add(model(**fields))
    session.flush()
