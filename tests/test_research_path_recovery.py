"""Graph lineage, scientific validity and durable same-state recovery decisions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

from research.agents.research_path import build_recovery_context, recovery_decision, task_signature
from research.planning.route_review import route_health


def fixture():
    project={'id':'project','goal':'Measure the prescribed system','revision':8,
        'budget':{'seconds':900,'allow_paid':False},
        'config':{'goal_contract':{'seed':17},'controller':{'status':'running','autonomous':True,'branch_id':'main'}}}
    def node(ident,status='idle',revision=0):
        return {'id':ident,'branch_id':'main','type':'experiment','title':ident,
            'instructions':'Measure '+ident,'revision':revision,'config':{'kind':'command','command':['python',ident+'.py']},
            'inputs':[],'execution_status':status,'deliverable_status':'ready','results_current':True}
    graph={'project_id':'project','revision':8,'nodes':[node('parent','completed',1),node('failed','failed'),node('alternative')],
        'branches':[{'id':'main','status':'active'}],
        'edges':[{'source':'parent','target':'failed','relation':'depends_on'},
                 {'source':'parent','target':'alternative','relation':'depends_on'}]}
    runs=[{'id':'old','node_id':'parent','node_revision':0,'status':'completed','created_at':'1','kind':'command'},
          {'id':'current','node_id':'parent','node_revision':1,'status':'completed','created_at':'2','kind':'command',
           'verification':{'verification_status':'accepted'},'goal_applicability':{'ready':True},
           'metrics':{'observed_metrics':{'score':0.75},'artifact_references':{'metrics.json':{'path':'metrics.json','revision':1}},'usage':{'total_tokens':42}}},
          {'id':'failed-run','node_id':'failed','node_revision':0,'created_at':'3','kind':'command','status':'failed',
           'error':'Missing executable dependency','config':{'research_task_identity':task_signature(graph['nodes'][1])}}]
    return project,graph,runs


def test_recovery_backtracks_ancestors_without_promoting_historical_results():
    project,graph,runs=fixture()
    context=build_recovery_context(project,graph,runs,node_id='failed',trigger={'category':'environment_error'})
    assert context['ancestor_node_ids']==['parent']
    assert context['alternative_ready_node_ids']==['alternative']
    assert context['evidence'][0]['run_id']=='current'
    assert context['evidence'][0]['scientifically_verified']
    assert context['evidence'][0]['observations']=={'score':0.75}
    assert context['historical_evidence'][0]['run_id']=='old'
    assert not context['historical_evidence'][0]['scientifically_verified']
    assert context['checkpoint_evidence']['historical'][0]['run_id']=='old'
    assert context['goal_contract']['contract']=={'seed':17}
    assert context['failures'][0]['scientific_disposition'].startswith('Unresolved execution')
    assert 'usage' not in json.dumps(context['checkpoint_evidence'])


def test_upstream_edit_blocks_dependent_route_until_revalidation():
    project,graph,runs=fixture()
    graph['nodes'][0]['results_current']=False
    context=build_recovery_context(project,graph,runs,node_id='failed')
    assert context['alternative_ready_node_ids']==['parent']
    assert {'node_id':'alternative','reason':'dependency_not_current'} in context['unavailable_paths']
    assert not context['evidence']


def test_permission_block_chooses_only_active_user_scoped_existing_routes():
    project,graph,runs=fixture()
    denied=deepcopy(graph['nodes'][1]);denied.update(id='copy-of-failure',execution_status='idle',title='Renamed attempt')
    pruned=deepcopy(graph['nodes'][2]);pruned.update(id='pruned-method',branch_id='pruned')
    elsewhere=deepcopy(graph['nodes'][2]);elsewhere.update(id='out-of-scope',branch_id='other')
    graph['nodes'].extend([denied,pruned,elsewhere]);graph['branches'].extend([{'id':'pruned','status':'pruned'},{'id':'other','status':'active'}])
    context=build_recovery_context(project,graph,runs,node_id='failed',trigger={'category':'permission_error'})
    assert context['alternative_ready_node_ids']==['alternative']
    assert {'node_id':'copy-of-failure','reason':'unchanged_failed_task'} in context['unavailable_paths']
    decision=recovery_decision({},context,autonomous=False)
    assert decision['action']=='execute_alternative'
    assert decision['node_ids']==['alternative']


def test_same_state_diagnoses_are_finite_and_preserved_across_serialized_restart():
    project,graph,runs=fixture();graph['nodes']=graph['nodes'][:2]
    context=build_recovery_context(project,graph,runs,node_id='failed',trigger={'category':'environment_error'})
    first=recovery_decision({},context)
    assert first['action']=='diagnose'
    checkpoint=json.loads(json.dumps({'recovery':first['recovery']}))
    second=recovery_decision(checkpoint,context)
    assert second['action']=='diagnose'
    checkpoint=json.loads(json.dumps({'recovery':second['recovery']}))
    exhausted=recovery_decision(checkpoint,context)
    assert exhausted['action']=='pause'
    assert 'changed evidence' in exhausted['reason']
    graph['nodes'][1]['revision']+=1
    graph['nodes'][1]['config']['command']=['python','repaired.py']
    revised=build_recovery_context(project,graph,runs,node_id='failed',trigger={'category':'environment_error'})
    assert recovery_decision(checkpoint,revised)['action']=='diagnose'


def test_control_retries_and_round_status_do_not_reset_recovery_state():
    project,graph,runs=fixture();graph['nodes'][1]['execution_status']='running'
    runs[-1]['status']='running';runs[-1]['metrics']={'steps':150}
    first=build_recovery_context(project,graph,runs,node_id='failed')
    graph['nodes'][1]['execution_status']='queued';runs[-1]['status']='queued';runs[-1]['metrics']={'steps':9000}
    runs.append({'id':'diagnostic','created_at':'4','kind':'research_plan','status':'failed'})
    second=build_recovery_context(project,graph,runs,node_id='failed')
    assert first['state']==second['state']
    assert first['evidence_state']==second['evidence_state']


def test_alternating_error_categories_and_round_diagnostics_do_not_reset_fuse():
    project,graph,runs=fixture()
    graph['nodes'][2]['execution_status']='failed'
    control={}
    keys=[]
    for category,expected in [('permission_error','diagnose'),
                              ('environment_error','diagnose'),
                              ('no_progress','pause')]:
        context=build_recovery_context(project,graph,runs,node_id='failed',
            trigger={'category':category,'message':'Unchanged scientific state'})
        keys.append(context['state'])
        decision=recovery_decision(control,context)
        assert decision['action']==expected
        control={'recovery':decision['recovery']}
    assert all(value==keys[0] for value in keys)


def test_round_hints_and_renamed_ids_do_not_erase_failed_task_identity():
    project,graph,runs=fixture()
    original=graph['nodes'][1];renamed=deepcopy(original)
    renamed.update(id='renamed',title='A fresh title',type='implementation')
    renamed['config'].update(agent_budget={'steps':400},step_budget_policy={'max_steps':150})
    assert task_signature(original)==task_signature(renamed)
    repaired=deepcopy(renamed);repaired['config']['command']=['python','fixed_method.py']
    assert task_signature(repaired)!=task_signature(original)


def test_clone_revision_and_repeated_failure_receipts_do_not_renew_diagnosis():
    project,graph,runs=fixture()
    graph['nodes']=graph['nodes'][:2]
    context=build_recovery_context(project,graph,runs,node_id='failed')
    first=recovery_decision({},context)
    second=recovery_decision({'recovery':first['recovery']},context)
    control={'recovery':second['recovery']}
    original_key=context['state']
    original_evidence=context['evidence_state']
    original_audit=context['checkpoint_evidence']
    for index in range(4):
        clone=deepcopy(graph['nodes'][1])
        clone.update(id='failed-copy-'+str(index),title='Renamed row',revision=index+1)
        graph['nodes'].append(clone)
        graph['edges'].append({'source':'parent','target':clone['id'],'relation':'depends_on'})
        failed=deepcopy(runs[-1])
        failed.update(id='repeated-failure-'+str(index),node_id=clone['id'],node_revision=clone['revision'])
        runs.append(failed)
        graph['nodes'][1]['revision']+=1
        graph['revision']+=1
        project['revision']+=1
        updated=build_recovery_context(project,graph,runs,node_id='failed')
        assert updated['state']==original_key
        assert updated['evidence_state']==original_evidence
        assert updated['checkpoint_evidence']!=original_audit
        assert updated['semantic_tasks']==context['semantic_tasks']
        assert recovery_decision(control,updated)['action']=='pause'
    assert len(updated['checkpoint_evidence']['failures'])==5


def test_meaningful_work_dependency_and_verified_observation_changes_renew_state():
    project,graph,runs=fixture()
    context=build_recovery_context(project,graph,runs,node_id='failed')
    clone=deepcopy(graph['nodes'][2]);clone['id']='same-method-with-another-dependency'
    graph['nodes'].append(clone)
    graph['edges'].append({'source':'failed','target':clone['id'],'relation':'depends_on'})
    changed_dependency=build_recovery_context(project,graph,runs,node_id='failed')
    assert changed_dependency['semantic_tasks']!=context['semantic_tasks']
    assert changed_dependency['state']!=context['state']
    graph['nodes'].pop();graph['edges'].pop()
    runs[1]['metrics']['observed_metrics']['score']=0.875
    changed_evidence=build_recovery_context(project,graph,runs,node_id='failed')
    assert changed_evidence['evidence_state']!=context['evidence_state']
    assert changed_evidence['state']!=context['state']


def test_progressful_counterexamples_and_graph_planning_have_no_count_limit():
    _,graph,_=fixture()
    runs=[{'id':str(n),'created_at':str(n).zfill(3),'node_id':'failed','node_revision':0,
        'kind':'command','status':'completed','config':{'research_activity':'counterexample','command':['python',str(n)]},
        'metrics':{'observed_metrics':{'score':n}}} for n in range(40)]
    assert not route_health(graph,runs)['replan_required']
    for n in range(40,80):
        runs.append({'id':str(n),'created_at':str(n).zfill(3),'kind':'research_plan','status':'completed',
            'metrics':{'graph_revision':n,'applied_commands':1}})
    assert not route_health(graph,runs)['replan_required']
    identical=[{'id':str(n),'created_at':str(n),'node_id':'failed','node_revision':0,'kind':'command','status':'completed',
        'config':{'research_activity':'counterexample','command':['python','same.py']},
        'metrics':{'observed_metrics':{'score':1}}} for n in range(3)]
    assert route_health(graph,identical)['signals'][0]['kind']=='excessive_counterexample_checks'


def test_reordering_graph_rows_cannot_reset_same_state_recovery():
    project,graph,runs=fixture()
    before=build_recovery_context(project,graph,runs,node_id='failed')
    graph['nodes'].reverse();graph['edges'].reverse();runs.reverse()
    after=build_recovery_context(project,graph,runs,node_id='failed')
    assert before['state']==after['state'] and before['evidence_state']==after['evidence_state']


def test_recorded_refutation_invalidates_downstream_assumptions_but_preserves_negative_evidence():
    project,graph,runs=fixture()
    graph['nodes'][0]['research_status']='refuted'
    context=build_recovery_context(project,graph,runs,node_id='failed')
    assert context['refuted_node_ids']==['parent']
    assert context['alternative_ready_node_ids']==[]
    assert not context['evidence'][0]['scientifically_verified']
    assert {'node_id':'alternative','reason':'recorded_hypothesis_refuted'} in context['unavailable_paths']
    graph['nodes'][2]['config']['research_activity']='negative_result_analysis'
    revised=build_recovery_context(project,graph,runs,node_id='failed')
    assert revised['alternative_ready_node_ids']==['alternative']
    assert revised['evidence'][0]['observations']=={'score':0.75}


def test_structured_refuted_claim_binding_excludes_route_and_changed_producer_repairs_failure():
    project,graph,runs=fixture()
    graph['nodes'][2]['inputs']=[{'kind':'claim','id':'observed-claim'}]
    records=[{'id':'observed-claim','kind':'research_claims','status':'refuted','data':{'evidence_run_ids':['current']}}]
    context=build_recovery_context(project,graph,runs,node_id='failed',records=records)
    assert context['alternative_ready_node_ids']==[]
    assert context['refuted_node_ids']==['alternative']
    graph['nodes'][2]['inputs']=[]
    copy=deepcopy(graph['nodes'][1]);copy.update(id='corrected-input-route',execution_status='idle')
    graph['nodes'].append(copy)
    runs[-1]['dependencies']=['old'];graph['nodes'][0]['latest_run_id']='current'
    fixed=build_recovery_context(project,graph,runs,node_id='failed')
    assert 'corrected-input-route' in fixed['alternative_ready_node_ids']


def test_unverified_required_input_is_not_a_feasible_alternative_but_verifier_can_execute():
    project,graph,runs=fixture()
    project['config']['verification_policy']='required'
    graph['nodes'][2]['inputs']=[{'node_id':'parent','path':'metrics.json'}]
    runs[1]['verification']={'verification_status':'unverified'}
    verifier=deepcopy(graph['nodes'][2]);verifier.update(id='source-verifier',type='verification')
    # Legacy graphs infer execution kind from node.type, just like scheduler.
    verifier['config']={'verification':{'producer_node_id':'parent','checks':[]}}
    graph['nodes'].append(verifier)
    context=build_recovery_context(project,graph,runs,node_id='failed')
    assert context['alternative_ready_node_ids']==['source-verifier']
    assert {'node_id':'alternative','reason':'dependency_not_verified'} in context['unavailable_paths']


def test_inferred_legacy_agent_without_authorized_tools_is_not_a_recovery_route():
    project,graph,runs=fixture()
    graph['nodes'][2].update(type='implementation',config={'tools':[]})
    context=build_recovery_context(project,graph,runs,node_id='failed')
    assert context['alternative_ready_node_ids']==[]
    assert {'node_id':'alternative','reason':'no_authorized_tools'} in context['unavailable_paths']


def test_controller_persisted_round_recovery_and_admission_are_real_database_operations(tmp_path):
    root=Path(__file__).resolve().parents[1]
    environment={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'recovery.db'),
        'FOREST_DATA_DIR':str(tmp_path/'data'),'FOREST_MODEL':'','PYTHONPATH':str(root)}
    result=subprocess.run([sys.executable,__file__,'database'],cwd=root,env=environment,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from services.api.main import app
    from services.api.db import Project,Node,TaskRun,Session,uid
    from services.worker.controller import advance_projects
    from research.agents.research_path import research_recovery_context,enqueue_recovery_alternatives
    with TestClient(app) as client:
        # Only lifecycle transitions are supplied here; no synthetic model
        # output is treated as scientific execution or evidence.
        empty=client.post('/api/projects',json={'name':'Legacy cycle metadata','mode':'auto',
            'goal':'Resolve the specified research goal','config':{'route_review':{'enabled':False}}}).json()
        with Session.begin() as session:
            p=session.get(Project,empty['id'])
            p.config={**p.config,'controller':{'status':'running','autonomous':True,'max_cycles':1,'cycles':9000}}
        advance_projects()
        with Session.begin() as session:
            p=session.get(Project,empty['id']);control=p.config['controller']
            assert control['status']=='running' and control['phase']=='PLAN'
            run=session.get(TaskRun,control['last_run']);assert run.kind=='research_plan'
            run.status='failed';run.error='Provider process exited before returning a plan'
        advance_projects()
        with Session.begin() as session:
            p=session.get(Project,empty['id']);control=p.config['controller']
            assert control['status']=='running' and control['phase']=='DIAGNOSE'
            assert control['recovery']['diagnostic_attempts']==1
            run=session.get(TaskRun,control['last_run']);run.status='failed';run.error='Provider process exited before returning a plan'
        advance_projects()
        with Session.begin() as session:
            p=session.get(Project,empty['id']);control=p.config['controller']
            assert control['status']=='running' and control['recovery']['diagnostic_attempts']==2
            run=session.get(TaskRun,control['last_run']);run.status='failed';run.error='Provider process exited before returning a plan'
        advance_projects()
        with Session() as session:
            control=session.get(Project,empty['id']).config['controller']
            assert control['status']=='blocked' and control['phase']=='PAUSED'
            assert control['cycles']==9000
            assert len(list(session.scalars(select(TaskRun).where(TaskRun.project_id==empty['id']))))==3
        project=client.post('/api/projects',json={'name':'Durable controller recovery','mode':'auto',
            'goal':'Calculate actual sequence evidence','budget':{'seconds':300,'max_runs':1},
            'config':{'publication_profile':'operational','route_review':{'enabled':False}}}).json()
        pid=project['id'];graph=client.get(f'/api/projects/{pid}/graph').json();bid=graph['branches'][0]['id']
        for ident in ('affected','existing-alternative'):
            graph=client.get(f'/api/projects/{pid}/graph').json()
            response=client.post(f'/api/projects/{pid}/graph/commands',json={'request_id':uid(),
                'expected_revision':graph['revision'],'operation':'add_node','params':{'id':ident,
                    'branch_id':bid,'type':'implementation','title':ident,'instructions':'Compute '+ident,
                    'config':{'kind':'command','command':[sys.executable,'-c',"from pathlib import Path;Path('answer.txt').write_text(str(sum(range(17))))"]}}})
            response.raise_for_status()
        with Session.begin() as session:
            p=session.get(Project,pid)
            p.config={**p.config,'controller':{'status':'running','autonomous':True,'max_cycles':1,'cycles':9000}}
            source=TaskRun(project_id=pid,node_id='affected',branch_id=bid,request_id=uid(),kind='command',
                status='waiting_input',node_revision=0,error='Permission denied for a declared unavailable source',
                resource={'blocked_reason':'permission_denied'},config={},output_path='runs/source')
            session.add(source);session.flush();sid=source.id
            node=session.get(Node,'affected');node.execution_status='waiting_input';node.extra={'latest_run_id':sid}
            p.config={**p.config,'controller':{**p.config['controller'],'last_run':sid}}
            context=research_recovery_context(session,p,node_id='affected',trigger={'category':'permission_error'})
            rows=enqueue_recovery_alternatives(session,p,context,source_run_id=sid)
            assert len(rows)==1 and rows[0].node_id=='existing-alternative';target=rows[0].id
            repeated=enqueue_recovery_alternatives(session,p,context,source_run_id=sid)
            assert [r.id for r in repeated]==[target]
        # The new process reloads the same project; no fresh goal or graph is made.
        advance_projects()
        with Session() as session:
            p=session.get(Project,pid)
            assert p.config['controller']['status']=='running'
            assert p.config['controller']['cycles']==9000
            assert len(list(session.scalars(select(TaskRun).where(TaskRun.project_id==pid))))==2
            row=session.get(TaskRun,target)
            assert row.resource['time_budget']['reservation_state']=='held'
        # Real deterministic execution uses the admitted run and persists output.
        executed=subprocess.run([sys.executable,'-m','services.worker.execute','--run-id',target],capture_output=True,text=True,timeout=20)
        assert executed.returncode==0,executed.stdout+executed.stderr
        from services.api.common import project_dir
        with Session() as session:
            row=session.get(TaskRun,target)
            assert (project_dir(pid)/row.output_path/'workspace'/'answer.txt').read_text()=='136'
        print('Graph-backed alternative admission, checkpoint replay and actual execution passed')
