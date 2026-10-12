"""Actual API/worker experiments continue under evolving scientific contracts."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import pytest

from test_worker import Harness
from research.agents.research_path import build_recovery_context, task_signature
from research.validation.comparability import assess_comparability


SCIENCE_SOURCE = '''
from pathlib import Path
import json, random, statistics

def run(parameters, output):
    generator=random.Random(parameters['seed'])
    count=parameters['training_samples']
    x=[generator.uniform(-1,1) for _ in range(count)]
    y=[2*value+1+generator.gauss(0,0.1) for value in x]
    center=statistics.mean(x); target=statistics.mean(y)
    slope=sum((a-center)*(b-target) for a,b in zip(x,y))/sum((a-center)**2 for a in x)
    intercept=target-slope*center
    validation=[generator.uniform(-1,1) for _ in range(parameters['validation_samples'])]
    mse=statistics.mean((slope*value+intercept-(2*value+1))**2 for value in validation)
    result={'mse':mse,'slope':slope,'intercept':intercept,'training_samples':count,
        'seed':parameters['seed'],'validation_samples':parameters['validation_samples']}
    (Path(output)/'workspace'/'metrics.json').write_text(json.dumps(result))
    return result
'''


@pytest.fixture
def evolving_worker(tmp_path):
    harness=Harness(tmp_path)
    try:
        harness.start_api();harness.start_worker()
        yield harness
    finally: harness.cleanup()


def test_actual_goal_graph_configuration_and_protocol_evolution_preserves_history(evolving_worker):
    h=evolving_worker
    project=h.request('POST','/api/projects',json={'name':'Versioned scientific evolution',
        'goal':'Estimate a linear relation from noisy observations','mode':'manual',
        'budget':{'seconds':90,'allow_paid':False},'config':{'publication_profile':{'mode':'pilot'},
            'goal_contract':{'success_criteria':['Fit and validate the linear relation']}}})
    pid=project['id'];graph=h.request('GET',f'/api/projects/{pid}/graph');branch=graph['branches'][0]
    root=h.directory/'data'/'projects'/pid/branch['workspace'];root.mkdir(parents=True,exist_ok=True)
    (root/'evolving_science.py').write_text(SCIENCE_SOURCE)
    initial=h.request('GET',f'/api/projects/{pid}/goal-contract')
    assert initial['current']['revision']==1
    protocol=h.request('POST',f'/api/projects/{pid}/evaluation-protocols',json={
        'protocol_id':'linear-observations','name':'Linear validation','definition':{'dataset':'linear-observations','split':'validation',
            'method_symbol':'linear-observations',
            'sampling':'uniform','validation_samples':30},'classification':'independent_confirmation'})
    ident=str(uuid.uuid4());graph=h.request('GET',f'/api/projects/{pid}/graph')
    config={'kind':'experiment','entrypoint':'evolving_science:run',
        'parameters':{'seed':17,'training_samples':20,'validation_samples':30},'evaluation_protocol_version_id':protocol['id'],
        'comparison_signature':{'dataset':'linear-observations','dataset_version':1,'split':'validation',
            'metric':'mse','statistical_unit':'validation example','budget':{'training_steps':20}}}
    added=h.request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),
        'expected_revision':graph['revision'],'operation':'add_node','params':{'id':ident,'branch_id':branch['id'],
            'type':'experiment','title':'Estimate the linear relation','instructions':'Fit OLS and measure validation MSE',
            'config':config}})
    node=next(value for value in added['graph']['nodes'] if value['id']==ident)
    first=h.terminal(h.launch(node));assert first['status']=='completed',first
    assert first['metrics']['mse']>=0 and abs(first['metrics']['slope']-2)<0.2
    assert first['config']['goal_contract_version_id']==initial['current']['id']
    assert first['config']['evaluation_context']['protocol_version_id']==protocol['id']
    original_config=deepcopy(first['config']);original_metrics=deepcopy(first['metrics'])

    # Reopen the same branch after real evidence; no copied project or new ledger.
    graph=h.request('GET',f'/api/projects/{pid}/graph')
    h.request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),
        'expected_revision':graph['revision'],'operation':'prune_branch','params':{'branch_id':branch['id']}})
    graph=h.request('GET',f'/api/projects/{pid}/graph')
    restored=h.request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),
        'expected_revision':graph['revision'],'operation':'reopen_branch','params':{'branch_id':branch['id'],
            'reason':'Observed fit warrants a more informative sample-size experiment','evidence_run_ids':[first['id']]}})
    live_branch=next(value for value in restored['graph']['branches'] if value['id']==branch['id'])
    assert live_branch['status']=='active' and live_branch['evolution_history'][-1]['evidence_run_ids']==[first['id']]
    before=h.request('GET',f'/api/projects/{pid}')
    revised=h.request('POST',f'/api/projects/{pid}/goal-contract/versions',json={
        'request_id':str(uuid.uuid4()),'expected_revision':before['revision'],'expected_goal_revision':1,
        'objective':'Estimate how sample size changes validation error',
        'success_criteria':['Compare actual OLS fits using distinct sample counts'],
        'reason':'The completed fit motivates a sample-size question','evidence_run_ids':[first['id']],
        'affected_branch_ids':[branch['id']]})
    assert revised['current']['revision']==2
    assert revised['current']['parent_version_id']==initial['current']['id']
    assert len(revised['versions'])==2
    adaptive=h.request('POST',f'/api/projects/{pid}/evaluation-protocols',json={
        'protocol_id':protocol['protocol_id'],'expected_protocol_revision':1,
        'name':'Linear validation','definition':{**protocol['definition'],'validation_samples':40},
        'reason':'Refine evaluation after observing the initial fit','feedback_run_ids':[first['id']],
        'classification':'independent_confirmation'})
    assert adaptive['classification']=='adaptive'
    config['parameters']['training_samples']=40
    config['parameters']['validation_samples']=40
    config['evaluation_protocol_version_id']=adaptive['id']
    h.request('PATCH','/api/nodes/'+node['id'],json={'config':config})
    second=h.terminal(h.launch(node));assert second['status']=='completed',second
    assert second['metrics']['training_samples']==40
    assert second['metrics']['validation_samples']==40
    assert second['config']['goal_contract_version_id']==revised['current']['id']
    assert second['config']['evaluation_context']['classification']=='adaptive'
    historical=h.request('GET','/api/runs/'+first['id'])
    assert historical['config']==original_config and historical['metrics']==original_metrics
    comparison=assess_comparability([first,second],{'metric':'mse'})
    assert comparison['comparison_status']=='incomparable'
    assert comparison['evaluation_classification']=='adaptive'
    assert comparison['independent_held_out_confirmation'] is False
    assert second['project_id']==first['project_id']==pid
    assert len(h.request('GET',f'/api/projects/{pid}/runs'))==2
    assert h.request('GET',f'/api/projects/{pid}')['budget']==project['budget']
    assert (h.output(first)/'workspace'/'metrics.json').is_file()
    assert (h.output(second)/'workspace'/'metrics.json').is_file()

    clone_id=str(uuid.uuid4());current_graph=h.request('GET',f'/api/projects/{pid}/graph')
    h.request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),
        'expected_revision':current_graph['revision'],'operation':'add_node','params':{'id':clone_id,
            'branch_id':branch['id'],'type':node['type'],'title':'Administrative copy of completed work',
            'instructions':node['instructions'],'config':deepcopy(config)}})
    inspect_ready="""
import sys
from services.api.db import Session,Project
from services.api.common import graph_from_db
from services.worker.controller import ready_work
with Session() as session:
    project=session.get(Project,sys.argv[1])
    ready=ready_work(session,project.id,graph_from_db(session,project),{})[2]
    assert (sys.argv[2] in [node.id for node in ready])==(sys.argv[3]=='ready')
"""
    def check_controller(expected):
        result=subprocess.run([sys.executable,'-c',inspect_ready,pid,clone_id,expected],
            cwd=Path(__file__).resolve().parents[1],env=h.env,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
    check_controller('excluded')
    copied_config=deepcopy(config);copied_config['parameters']['seed']=97
    h.request('PATCH','/api/nodes/'+clone_id,json={'config':copied_config})
    check_controller('ready')
    assert h.request('GET','/api/nodes/'+clone_id)['execution_status']!='completed'

    # Backups and project copies retain both version chains and all evidence
    # bindings. Restored work can evolve without stamping old fits as new ones.
    archive=h.client.post(f'/api/projects/{pid}/export',json={});archive.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        manifest=json.loads(zipped.read('forest-project.json'))
    assert len(manifest['goal_contract_versions'])==2
    assert len(manifest['evaluation_protocol_versions'])==2
    restored=h.request('POST','/api/projects/import',files={'file':('science.zip',archive.content,'application/zip')})
    copied=h.request('POST',f'/api/projects/{pid}/duplicate')
    for destination in (restored,copied):
        dest_id=destination['id'];goals=h.request('GET',f'/api/projects/{dest_id}/goal-contract')
        protocols=h.request('GET',f'/api/projects/{dest_id}/evaluation-protocols')
        dest_runs=h.request('GET',f'/api/projects/{dest_id}/runs')
        assert [row['objective'] for row in goals['versions']]==[row['objective'] for row in revised['versions']]
        assert goals['current']['id']!=revised['current']['id']
        assert goals['current']['parent_version_id']==goals['versions'][0]['id']
        by_original={row['config']['original_run_id']:row for row in dest_runs}
        old_fit=by_original[first['id']];new_fit=by_original[second['id']]
        assert old_fit['config']['goal_contract_version_id']==goals['versions'][0]['id']
        assert old_fit['config']['goal_contract_revision']==1
        assert old_fit['metrics']==original_metrics
        assert goals['current']['evidence_run_ids']==[old_fit['id']]
        adaptive_copy=next(row for row in protocols if row['revision']==2)
        assert adaptive_copy['protocol_id']==adaptive['protocol_id']=='linear-observations'
        assert adaptive_copy['definition']==adaptive['definition']
        assert adaptive_copy['definition']['method_symbol']=='linear-observations'
        assert adaptive_copy['feedback_run_ids']==[old_fit['id']]
        assert adaptive_copy['parent_version_id']==next(row['id'] for row in protocols if row['revision']==1)
        assert new_fit['config']['evaluation_protocol_version_id']==adaptive_copy['id']
        assert new_fit['config']['evaluation_context']['protocol_version_id']==adaptive_copy['id']
        assert goals['current']['affected_branch_ids']==[new_fit['branch_id']]
        dest_graph=h.request('GET',f'/api/projects/{dest_id}/graph')
        dest_branch=next(row for row in dest_graph['branches'] if row['id']==new_fit['branch_id'])
        assert dest_branch['evolution_history'][-1]['evidence_run_ids']==[old_fit['id']]
        assert len(dest_runs)==2  # Restoring scientific history did not replay a fit.
    dest_graph=h.request('GET',f'/api/projects/{restored["id"]}/graph')
    restored_node=next(row for row in dest_graph['nodes'] if row['title']==node['title'])
    changed=deepcopy(restored_node['config']);changed['parameters']['seed']=29
    changed['parameters']['training_samples']=60
    h.request('PATCH','/api/nodes/'+restored_node['id'],json={'config':changed})
    third=h.terminal(h.launch(restored_node));assert third['status']=='completed',third
    assert third['metrics']['seed']==29 and third['metrics']['training_samples']==60
    assert third['config']['goal_contract_version_id']==h.request('GET',
        f'/api/projects/{restored["id"]}/goal-contract')['current']['id']


def test_completed_equivalent_work_requires_new_configuration_or_real_new_evidence():
    from test_research_path_recovery import fixture
    project,graph,runs=fixture()
    clone=deepcopy(graph['nodes'][0]);clone.update(id='same-completed-work',execution_status='idle')
    graph['nodes'].append(clone)
    context=build_recovery_context(project,graph,runs,node_id='failed')
    assert clone['id'] not in context['alternative_ready_node_ids']
    assert {'node_id':clone['id'],'reason':'unchanged_completed_task'} in context['unavailable_paths']
    assert clone['execution_status']=='idle'
    clone['config']['new_evidence_run_ids']=['invented-evidence']
    assert clone['id'] not in build_recovery_context(project,graph,runs,node_id='failed')['alternative_ready_node_ids']
    runs.append({'id':'new-measurement','node_id':'alternative','node_revision':0,'created_at':'4',
        'kind':'command','status':'completed','metrics':{'observed_metrics':{'score':0.9}}})
    clone['config']['new_evidence_run_ids']=['new-measurement']
    assert clone['id'] in build_recovery_context(project,graph,runs,node_id='failed')['alternative_ready_node_ids']
    clone['config'].pop('new_evidence_run_ids');clone['config']['parameters']={'seed':23}
    assert clone['id'] in build_recovery_context(project,graph,runs,node_id='failed')['alternative_ready_node_ids']


def test_goal_version_migration_is_idempotent_for_legacy_project(tmp_path):
    root=Path(__file__).resolve().parents[1]
    environment={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'legacy.db'),
        'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_MODEL':'','PYTHONPATH':str(root)}
    seeded=subprocess.run([sys.executable,str(root/'tests'/'fixtures'/'seed_legacy_v3_schema.py')],
        cwd=root,env=environment,capture_output=True,text=True,timeout=30)
    assert seeded.returncode==0,seeded.stdout+seeded.stderr
    source="""
from services.api.db import *
from services.research_evolution.service import current_goal,goal_context
from sqlalchemy import inspect
assert 'goal_contract_versions' not in inspect(engine).get_table_names()
with Session.begin() as session:
    project=session.get(Project,'legacy-retained-project')
    project.goal='Existing scientific objective';project.config={'goal_contract':{'seed':17}};ident=project.id
    session.add(Project(id='legacy-open-json',name='Earlier open configuration',goal='Retain original value',
        config={'goal_contract':'An older free-form contract'}))
migrate();migrate()
with Session() as session:
    context=goal_context(session,session.get(Project,ident))
    assert len(context['versions'])==1
    assert context['current']['objective']=='Existing scientific objective'
    assert context['current']['contract']=={'seed':17}
    preserved=goal_context(session,session.get(Project,'legacy-open-json'))
    assert preserved['current']['contract']=={'legacy_contract_value':'An older free-form contract'}
"""
    result=subprocess.run([sys.executable,'-c',source],cwd=root,env=environment,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def test_revision_replay_is_idempotent_and_feedback_cannot_be_renamed_as_unseen(evolving_worker):
    h=evolving_worker;project,node=h.project_node(seconds=1)
    first=h.terminal(h.launch(node));assert first['status']=='completed'
    pid=project['id'];path=f'/api/projects/{pid}/evaluation-protocols'
    body={'request_id':'one-protocol-request','expected_revision':h.request('GET',f'/api/projects/{pid}')['revision'],
        'definition':{'dataset':'same-observations','dataset_version':1,'split':'validation'},
        'feedback_run_ids':[first['id']],'classification':'independent_confirmation'}
    original=h.request('POST',path,json=body)
    assert original['classification']=='adaptive'
    assert h.request('POST',path,json=body)==original
    changed=h.client.post(path,json={**body,'definition':{'dataset':'changed-observations','split':'validation'}})
    assert changed.status_code==409 and changed.json()['detail']['code']=='REQUEST_ID_CONFLICT'
    renamed=h.request('POST',path,json={'definition':body['definition'],'classification':'independent_confirmation'})
    assert renamed['protocol_id']!=original['protocol_id'] and renamed['classification']=='adaptive'
    assert renamed['feedback_run_ids']==[first['id']]
    unseen=h.request('POST',path,json={'definition':{**body['definition'],'dataset':'fresh-confirmation-data'},
        'classification':'independent_confirmation'})
    assert unseen['classification']=='independent_confirmation'
    goal_path=f'/api/projects/{pid}/goal-contract/versions'
    goal={'request_id':'one-goal-request','expected_goal_revision':1,
        'objective':'A revised scientific question','success_criteria':['Actual execution evidence'],
        'reason':'Observed execution motivates a revised question','evidence_run_ids':[first['id']]}
    original_goal=h.request('POST',goal_path,json=goal)
    assert h.request('POST',goal_path,json=goal)==original_goal
    conflict=h.client.post(goal_path,json={**goal,'objective':'A different question'})
    assert conflict.status_code==409 and conflict.json()['detail']['code']=='REQUEST_ID_CONFLICT'
    invalid=h.client.post(goal_path,json={'contract':'not-an-object'})
    assert invalid.status_code==422
    legacy=h.request('GET',f'/api/projects/{pid}')
    h.request('PATCH',f'/api/projects/{pid}',json={'expected_revision':legacy['revision'],
        'config':{**legacy['config'],'goal_contract':{'success_conditions':['Retain legacy criteria']}}})
    next_goal=h.request('POST',goal_path,json={'objective':'Refine the objective alone'})
    assert next_goal['current']['success_criteria']==['Retain legacy criteria']


def test_criteria_only_evolution_requires_a_current_evidence_decision(evolving_worker):
    h=evolving_worker;project,node=h.project_node(seconds=1);pid=project['id']
    run=h.terminal(h.launch(node));assert run['status']=='completed'
    before=h.request('GET',f'/api/projects/{pid}')
    h.request('POST',f'/api/runs/{run["id"]}/applicability/decisions',json={'request_id':'adopt-under-v1',
        'expected_revision':before['revision'],'choice':'reuse','reason':'Execution answers the current question'})
    assert h.request('GET',f'/api/runs/{run["id"]}/applicability')['status']=='approved'
    goal=h.request('POST',f'/api/projects/{pid}/goal-contract/versions',json={
        'success_criteria':['Require additional observations'],'reason':'A stronger success criterion is scientifically useful'})
    assert goal['current']['objective']==project['goal'] and goal['current']['revision']==2
    applicable=h.request('GET',f'/api/runs/{run["id"]}/applicability')
    assert applicable['status']=='needs_review' and not applicable['ready']
    h.request('POST',f'/api/runs/{run["id"]}/applicability/decisions',json={'request_id':'adopt-under-v2',
        'expected_revision':goal['project_revision'],'choice':'reuse','reason':'This observation remains relevant as supporting evidence'})
    assert h.request('GET',f'/api/runs/{run["id"]}/applicability')['status']=='approved'
    historical=h.request('GET','/api/runs/'+run['id'])
    assert historical['config']['goal_contract_revision']==1
    # Graph goal edits change relevance, not the executed numerical revision.
    graph=h.request('GET',f'/api/projects/{pid}/graph')
    original_node=next(row for row in graph['nodes'] if row['id']==node['id'])
    revised=h.request('POST',f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),
        'expected_revision':graph['revision'],'operation':'revise_goal','params':{
            'success_criteria':['Investigate additional conditions'],'evidence_run_ids':[run['id']],
            'reason':'The observation motivates a broader question'}})
    current_node=next(row for row in revised['graph']['nodes'] if row['id']==node['id'])
    assert current_node['revision']==original_node['revision']
    assert current_node['latest_run_id']==original_node['latest_run_id']
    assert current_node['execution_status']=='completed'
    assert h.request('GET',f'/api/runs/{run["id"]}/applicability')['status']=='needs_review'
    assert h.request('GET','/api/runs/'+run['id'])['config']==historical['config']


@pytest.mark.parametrize('previous_branch_status',['pruned','completed'])
def test_planner_adopts_versioned_goals_and_reopens_existing_routes(tmp_path,previous_branch_status):
    """Exercise real planning adoption and persistence, without claiming science output."""
    root=Path(__file__).resolve().parents[1]
    source="""
import sys
from fastapi.testclient import TestClient
from services.api.main import app
from services.api.db import Session,Project,Branch,TaskRun,uid
from research.planning.loop import apply_plan,planning_context,compact_planning_context
with TestClient(app) as client:
    project=client.post('/api/projects',json={'name':'Editable planning contract','goal':'Initial theoretical direction',
        'mode':'manual','config':{'goal_contract':{'success_criteria':['Explore the initial direction']}}}).json()
    pid=project['id'];graph=client.get('/api/projects/'+pid+'/graph').json();bid=graph['branches'][0]['id']
    added=client.post('/api/projects/'+pid+'/graph/commands',json={'request_id':uid(),'expected_revision':graph['revision'],
        'operation':'add_node','params':{'branch_id':bid,'title':'Theoretical analysis','type':'analysis',
        'instructions':'Analyze the initial theoretical assumption','config':{'kind':'agent','tools':['read_file','finish']}}})
    added.raise_for_status();node=added.json()['graph']['nodes'][0]
    with Session.begin() as session:
        branch=session.get(Branch,bid);branch.status=sys.argv[1]
        p=session.get(Project,pid);p.config={**p.config,'controller':{'status':'running','autonomous':True,
            'replan_required':True,'route_replan_node_ids':[node['id']]}}
        plan_run=TaskRun(project_id=pid,request_id=uid(),kind='research_plan',status='running',config={},output_path='runs/planning')
        session.add(plan_run);session.flush();rid=plan_run.id
    graph=client.get('/api/projects/'+pid+'/graph').json()
    proposal={'action':'continue','rationale':'A revised theoretical question changes the analysis task',
        'evidence_run_ids':[], 'replanning':{'reason':'The theoretical assumption can be generalized',
            'new_direction':'Analyze a less restrictive assumption','goal_alignment':'Adopt the revised scientific objective',
            'stop_node_ids':[node['id']],'evidence_run_ids':[]},
        'commands':[{'operation':'reopen_branch','params':{'branch_id':bid,'reason':'Revisit the existing theoretical route','evidence_run_ids':[]}},
            {'operation':'revise_goal','params':{'objective':'Analyze the generalized theoretical relation',
                'success_criteria':['Derive and evaluate the generalized relation'],'reason':'Generalize the original assumption',
                'affected_branch_ids':[bid],'evidence_run_ids':[]}},
            {'operation':'edit_node','targets':[node['id']],'params':{'instructions':'Analyze the generalized theoretical assumption'}}]}
    result=apply_plan(pid,rid,proposal,graph['revision'])
    assert result['action']=='continue' and result['applied_commands']==3
    goal=client.get('/api/projects/'+pid+'/goal-contract').json()
    assert [row['objective'] for row in goal['versions']]==['Initial theoretical direction','Analyze the generalized theoretical relation']
    assert goal['current']['affected_branch_ids']==[bid]
    after=client.get('/api/projects/'+pid+'/graph').json();branch=next(row for row in after['branches'] if row['id']==bid)
    assert branch['status']=='active' and branch['evolution_history'][-1]['previous_status']==sys.argv[1]
    assert after['nodes'][0]['id']==node['id'] and after['nodes'][0]['execution_status']!='completed'
    context=planning_context(pid);view=compact_planning_context(context,500)
    assert view['goal_contract']['current']['objective']==goal['current']['objective']
    assert view['goal_contract']['history_endpoint'].endswith('/goal-contract')
    assert len(view['goal_contract']['version_refs'])==2
"""
    result=subprocess.run([sys.executable,'-c',source,previous_branch_status],cwd=root,env={**os.environ,
        'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'planning.db'),'FOREST_DATA_DIR':str(tmp_path/'data'),
        'FOREST_MODEL':'','PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
