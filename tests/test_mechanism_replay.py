"""Infrastructure fixtures, never substituted for actual scientific replay cells."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from research.experiments.mechanism_replay import (
    ARMS, _session, build_controller, execute_replay_action, prepare_replay,
    verify_replay_submission,
)
from research.experiments.study_runtime import StudyRuntime


TASK = {
    'task_id':'generic-task','primary_metric':'loss','primary_direction':'minimize',
    'side_constraints':[], 'effect_threshold':{'kind':'absolute_reduction','value':.01},
    'snapshot_configurations':[{'configuration_id':'anchor','is_anchor':True},
        {'configuration_id':'fast','is_anchor':False},{'configuration_id':'accurate','is_anchor':False}],
    'shared_snapshot_seeds':[11,12],
}
FACTS = {
    'alias':{'data_alias':{'new_alias':'another-name','ordered_sample_ids_unchanged':True,'same_dataset_version':'data-v1'}},
    'contract':{'evaluation_contract':{'revised_primary_metric':'accuracy','revised_direction':'maximize',
        'revised_side_constraints':[]}},
    'input':{'validation_input_config':{'validation_noise_seed':999,'validation_noise_std':.02}},
    'data':{'training_data_revision':{'train_sample_ids':[1,3,4]}},
    'recipe':{'training_recipe_revision':{'weight_decay':.02}},
    'combined':{'evaluation_contract':{'revised_primary_metric':'accuracy','revised_direction':'maximize'},
        'validation_input_config':{'validation_noise_seed':999,'validation_noise_std':.02}},
}


def original_records():
    result=[]
    for configuration_id,loss,accuracy in [('anchor',.5,.7),('fast',.4,.85),('accurate',.2,.8)]:
        for seed in [11,12]:
            result.append({'source_work_id':f'{configuration_id}-seed-{seed}',
                'configuration_id':configuration_id,'seed':seed,
                'configuration':{'work_id':f'{configuration_id}-seed-{seed}','task_id':'generic-task',
                    'configuration_id':configuration_id,'seed':seed,'dataset_version_id':'data-v1',
                    'train_sample_ids':[1,2,3,4],'validation_sample_ids':[5,6],
                    'weight_decay':0,'architecture':configuration_id},
                'observed_metrics':{'loss':loss,'accuracy':accuracy},
                'virtual_training_reference_seconds':2.0})
    return result


def runtime_fixture(tmp_path, *, arm='FULL',facts=None,work_id='replay-fixture'):
    campaign=tmp_path/'campaign';campaign.mkdir(parents=True,exist_ok=True)
    records=original_records();configs=[]
    for record in records:
        output=campaign/'source-fixtures'/record['source_work_id'];output.mkdir(parents=True,exist_ok=True)
        config={**record['configuration'],'output_directory':str(output),'phase':'snapshot'}
        receipt={'status':'SUCCEEDED','hidden_test_opened':False,'effective_config':config,
            'validation_metrics':record['observed_metrics'],'validation_prediction_file':'predictions.fixture',
            'checkpoint_file':'checkpoint.fixture','work_id':config['work_id']}
        (output/'effective_config.json').write_text(json.dumps(config),encoding='utf-8')
        (output/'execution_receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        (output/'checkpoint.fixture').write_bytes(b'explicit unit-test checkpoint fixture')
        (output/'predictions.fixture').write_bytes(b'explicit unit-test predictions fixture')
        configs.append(config)
    episode={'work_id':work_id,'kind':'replay','task_id':'generic-task','arm':arm,
        'candidate_configs':configs,'factual_changes':deepcopy(FACTS['contract'] if facts is None else facts),
        'resource_policy':{'replay_only':True,'active_wall_seconds':300,'actual_gpu_seconds':0}}
    manifest={'campaign_id':'fixture-campaign','manifest_id':'fixture-manifest','manifest_version':1,
        'external_ledger_id':'fixture-ledger','tasks':[deepcopy(TASK)],
        'execution_state':{'work_items':[episode]},'graph':{'edges':[]}}
    manifest_path=campaign/'manifest.json';manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    runtime=StudyRuntime(campaign=campaign,ledger=campaign/'study.sqlite',ledger_id='fixture-ledger',manifest=manifest_path)
    prepared=prepare_replay(runtime,episode)
    return runtime,{'work_id':work_id,'kind':'replay','payload':prepared},prepared


def repair(runtime,episode):
    controller=_session(runtime,episode['work_id'])
    required=set(controller['truth']['required_record_ids'])
    results=[]
    for binding in controller['action_bindings']:
        if binding['record_id'] in required:
            results.append(execute_replay_action(runtime,episode,{'kind':binding['action_name'],'action_work_id':binding['work_id']}))
    submission={key:controller['truth'][key] for key in ['invalidate_record_ids','reuse_record_ids']}
    submission.update(scientific_repair_complete=False,claim='This is a mechanism-only fixture analysis.')
    return [{'actions':results}],submission


@pytest.mark.parametrize('arm',sorted(ARMS))
@pytest.mark.parametrize('case',list(FACTS))
def test_dependency_controller_uses_facts_for_all_treatments(arm,case):
    controller=build_controller(original_records(),TASK,FACTS[case],arm=arm,work_id='arbitrary')
    truth=controller['truth'];invalid=set(truth['invalidate_record_ids'])
    nodes={row['id']:row for row in controller['nodes']}
    expected_facets={'alias':set(),'contract':{'evaluation_contract'},'input':{'validation_input'},
        'data':{'training_data'},'recipe':{'training_recipe'},'combined':{'evaluation_contract','validation_input'}}[case]
    assert set(truth['changed_facets'])==expected_facets
    assert not any(nodes[key]['kind']=='checkpoint' for key in invalid) if case in {'alias','contract','input','combined'} else all(row['id'] in invalid for row in nodes.values())
    if case=='alias':assert invalid==set()
    if case=='contract':assert {nodes[key]['kind'] for key in invalid}=={'metrics','selection','comparison','claim'}
    if case in {'input','combined'}:assert {nodes[key]['kind'] for key in invalid}=={'predictions','metrics','selection','comparison','claim'}
    assert invalid.isdisjoint(truth['reuse_record_ids'])
    assert invalid|set(truth['reuse_record_ids'])==set(nodes)
    expected_required={key for key,row in nodes.items() if row['kind']!='claim' and (key in invalid or arm=='RERUN_ALL')}
    assert set(truth['required_record_ids'])==expected_required


@pytest.mark.parametrize('arm',sorted(ARMS))
def test_preparation_exposes_treatment_view_without_private_answers(tmp_path,arm):
    runtime,episode,prepared=runtime_fixture(tmp_path,arm=arm)
    context=json.loads(prepared['messages'][-1]['content'])
    assert 'truth' not in context and 'controller_mutation_id' not in prepared['messages'][-1]['content']
    view=context['evidence_view']
    assert len(view['observations'])==6
    if arm=='DAG_ID':
        assert all(set(edge)=={'from','to'} for edge in view['edges'])
        assert all('kind' not in row and 'scope_inputs' not in row for row in view['records'])
    elif arm in {'FLAT','ID_ONLY','RERUN_ALL'}:assert 'edges' not in view and view['graph_propagation'] is False
    else:
        assert 'claim' in {row['kind'] for row in view['nodes']}
        assert all('current_scope_status' not in row for row in view['nodes'])
    assert prepared['decision_schema']['properties']['actions']['items']['properties']['action_work_id']['enum']
    assert prepare_replay(runtime,prepared)==prepared


@pytest.mark.parametrize('case',list(FACTS))
def test_actual_sqlite_virtual_operations_never_become_physical_results(tmp_path,case):
    runtime,episode,_=runtime_fixture(tmp_path,facts=FACTS[case])
    history,submission=repair(runtime,episode)
    verified=verify_replay_submission(runtime,episode,history,submission)
    assert verified['valid'] is True and verified['mechanistic_plan_valid'] is True
    assert verified['scientific_repair_complete'] is False
    assert verified['scientific_metric_values_generated'] is False and verified['gpu_actual_seconds']==0
    assert bool(verified['unavailable_action_work_ids'])==(case in {'input','data','recipe','combined'})
    for row in history[0]['actions']:
        assert row['state']=='completed' and row['kind']=='replay_transition'
        assert row['result']['physical_fit_executed'] is False and row['result']['physical_inference_executed'] is False
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_work").fetchone()[0]==1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='forest_compute_work'").fetchone() is None
        assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0]==0


def test_changed_contract_selects_from_observed_original_metrics_only(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path)
    history,submission=repair(runtime,episode)
    selection=next(row['result']['artifact_based_analysis'] for row in history[0]['actions'] if row['result']['operation']=='select')
    assert selection['active_metric']=='accuracy' and selection['selected_configuration_id']=='fast'
    assert selection['original_paired_differences']==[{'seed':11,'selected_minus_anchor':pytest.approx(.15)},
        {'seed':12,'selected_minus_anchor':pytest.approx(.15)}]
    submission['selected_configuration_id']='fast'
    assert verify_replay_submission(runtime,episode,history,submission)['valid'] is True
    submission['selected_configuration_id']='accurate'
    assert verify_replay_submission(runtime,episode,history,submission)['valid'] is False


def test_rerun_all_requires_virtual_training_even_unchanged_alias(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path,arm='RERUN_ALL',facts=FACTS['alias'])
    history,submission=repair(runtime,episode)
    verified=verify_replay_submission(runtime,episode,history,submission)
    assert verified['valid'] and len(history[0]['actions'])==20
    assert not verified['unavailable_action_work_ids']
    assert all(row['result']['status']=='REPLAYED' for row in history[0]['actions'])


def test_advisory_gate_retains_invalid_proposal_without_claiming_validity(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path,arm='FULL_NO_GATE')
    history,submission=repair(runtime,episode)
    submission['invalidate_record_ids']=[]
    verified=verify_replay_submission(runtime,episode,history,submission)
    assert verified['valid'] is False and verified['state']=='failed'
    assert verified['gate_mode']=='advisory' and verified['proposal_accepted_without_gate'] is True


def test_unexecuted_and_forged_operations_cannot_certify_repair(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path)
    controller=_session(runtime,episode['work_id'])
    submission={key:controller['truth'][key] for key in ['invalidate_record_ids','reuse_record_ids']}
    initial=verify_replay_submission(runtime,episode,[],submission)
    assert initial['valid'] is False and initial['missing_record_operations']
    history,submission=repair(runtime,episode)
    forged=deepcopy(history);forged[0]['actions'][0]['result']['status']='FITTED'
    assert verify_replay_submission(runtime,episode,forged,submission)['forged_history_actions']
    submission['scientific_repair_complete']=True
    assert verify_replay_submission(runtime,episode,history,submission)['valid'] is False
    submission['invalidate_record_ids']=[{}]
    assert verify_replay_submission(runtime,episode,history,submission)['valid'] is False


def test_observational_context_reads_do_not_count_as_forged_scientific_actions(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path)
    history,submission=repair(runtime,episode)
    history.append({'actions':[{'kind':'analysis','action_work_id':'episode--replay-fixture--read-context--page-1',
        'state':'completed','result':{'operation':'read_context_segment','segment_id':'public-context',
            'pointer':'','offset':0,'next_offset':32,'content':'observational context only'}}]})
    verified=verify_replay_submission(runtime,episode,history,submission)
    assert verified['forged_history_actions']==[]
    assert verified['valid'] is True
    assert verified['scientific_metric_values_generated'] is False
    assert verified['gpu_actual_seconds']==0


def test_source_artifacts_are_reconciled_after_edit_not_implicitly_reused(tmp_path):
    runtime,episode,prepared=runtime_fixture(tmp_path)
    binding=prepared['action_bindings'][0]
    action={'kind':binding['action_name'],'action_work_id':binding['work_id']}
    assert execute_replay_action(runtime,episode,action)['state']=='completed'
    controller=_session(runtime,episode['work_id'])
    path=controller['records'][0]['checkpoint_reference']
    from pathlib import Path
    Path(path).write_bytes(b'changed checkpoint')
    assert execute_replay_action(runtime,episode,action)['state']=='awaiting_reconciliation'
    assert verify_replay_submission(runtime,episode,[],{})['valid'] is False


def test_replay_restart_and_concurrent_action_identity_return_original_receipt(tmp_path):
    runtime,episode,prepared=runtime_fixture(tmp_path)
    binding=next(row for row in prepared['action_bindings'] if row['action_name']=='score')
    action={'kind':'score','action_work_id':binding['work_id']}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:execute_replay_action(runtime,episode,action),range(8)))
    assert all(row==results[0] for row in results)
    resumed=StudyRuntime(campaign=runtime.campaign,ledger=runtime.ledger_path,ledger_id=runtime.ledger_id,manifest=runtime.manifest_path)
    assert execute_replay_action(resumed,episode,action)==results[0]
    with resumed.ledger.connect() as db:
        assert db.execute('SELECT count(*) FROM forest_replay_actions').fetchone()[0]==1
    with pytest.raises(ValueError,match='exact registered'):
        execute_replay_action(resumed,episode,{**action,'kind':'train'})
    changed=deepcopy(prepared);changed['factual_changes']=FACTS['data']
    with pytest.raises(ValueError,match='different scientific conditions'):
        prepare_replay(resumed,changed)


def test_replay_missing_original_artifact_is_never_prepared(tmp_path):
    runtime,episode,prepared=runtime_fixture(tmp_path)
    from pathlib import Path
    Path(prepared['candidate_configs'][0]['output_directory'],'checkpoint.fixture').unlink()
    with runtime.ledger.connect() as db:db.execute('DELETE FROM forest_replay_sessions')
    with pytest.raises(ValueError,match='artifacts are missing'):
        prepare_replay(runtime,prepared)


def test_large_ordered_data_ids_remain_complete_in_original_and_model_view(tmp_path):
    facts={'training_data_revision':{'train_sample_ids':list(range(10000))}}
    runtime,episode,prepared=runtime_fixture(tmp_path,facts=facts)
    context=json.loads(prepared['messages'][-1]['content'])
    assert context['factual_changes']['training_data_revision']['train_sample_ids']==list(range(10000))
    controller=_session(runtime,episode['work_id'])
    assert controller['facts']==facts
    binding=prepared['action_bindings'][0]
    action=execute_replay_action(runtime,episode,{'kind':binding['action_name'],'action_work_id':binding['work_id']})
    assert action['action_work_id']==binding['work_id']


class GuardedFixtureClient:
    """Explicit scripted provider fixture; sends no network or Codex requests."""
    provider={'id':'infrastructure-fixture','model':'infrastructure-fixture'}
    config={}
    timeout=300

    def __init__(self,decisions,*,physical_requests_per_turn=1):
        self.decisions=decisions
        self.counter=[0]
        self.physical_requests_per_turn=physical_requests_per_turn
        self.request_guard=lambda event:None

    def complete(self,messages,*,max_output_tokens,**kwargs):
        # Use the same before/after callback boundary as the real Provider.
        for attempt in range(self.physical_requests_per_turn):
            reservation=self.request_guard({'phase':'before','max_output_tokens':max_output_tokens,
                'input_bytes':len(json.dumps(messages).encode()),'attempt':attempt,'api':'fixture','model':'fixture'})
            self.request_guard({'phase':'after','reservation':reservation,
                'usage':{'input_tokens':100,'output_tokens':20,'cost':None}})
        decision=self.decisions[self.counter[0]];self.counter[0]+=1
        return {'text':json.dumps(decision),'model':'infrastructure-fixture',
            'response_id':f'fixture-{self.counter[0]}','usage':{'input_tokens':100,'output_tokens':20,'cost':None}}


def test_common_runtime_records_virtual_actions_and_physical_request_accounting(tmp_path):
    runtime,episode,prepared=runtime_fixture(tmp_path)
    controller=_session(runtime,episode['work_id'])
    required=set(controller['truth']['required_record_ids'])
    actions=[{'kind':binding['action_name'],'action_work_id':binding['work_id']}
        for binding in controller['action_bindings'] if binding['record_id'] in required]
    submission={key:controller['truth'][key] for key in ['invalidate_record_ids','reuse_record_ids']}
    submission.update(selected_configuration_id='fast',scientific_repair_complete=False,claim='Fixture mechanism evaluation only.')
    client=GuardedFixtureClient([
        {'mode':'action','actions':actions,'submission':{},'rationale':'Execute source-bound virtual plan.'},
        {'mode':'submit','actions':[],'submission':submission,'rationale':'Submit the recorded virtual plan.'},
    ],physical_requests_per_turn=2)
    first=runtime.run_one(owner='fixture',client=client)
    assert first['action_result']['state']=='completed'
    assert runtime.ledger.counts(runtime.campaign_id)=={'retryable':1}
    # A new Runtime reconstructs the same scientific context and action receipts.
    resumed=StudyRuntime(campaign=runtime.campaign,ledger=runtime.ledger_path,ledger_id=runtime.ledger_id,manifest=runtime.manifest_path)
    final=resumed.run_one(owner='fixture-after-restart',client=client)
    assert final['action_result']['valid'] is True
    assert final['action_result']['scientific_repair_complete'] is False
    assert resumed.ledger.counts(runtime.campaign_id)=={'completed':1}
    with resumed.ledger.connect() as db:
        calls=db.execute('SELECT request_json,response_json,usage_json,status,started_at,finished_at,elapsed_seconds FROM forest_study_model_calls').fetchall()
        assert len(calls)==2 and all(row['status']=='completed' for row in calls)
        assert all(json.loads(row['request_json'])['messages'] and json.loads(row['response_json'])['text'] for row in calls)
        assert all(row['started_at']<=row['finished_at'] and row['elapsed_seconds']>=0 for row in calls)
        physical=db.execute('SELECT status,usage_json FROM forest_study_replay_requests').fetchall()
        assert len(physical)==4 and all(row['status']=='completed' for row in physical)
        assert sum(json.loads(row['usage_json'])['input_tokens'] for row in physical)==400
        assert sum(json.loads(row['usage_json'])['output_tokens'] for row in physical)==80
        assert db.execute('SELECT count(*) FROM forest_replay_actions').fetchone()[0]==len(actions)
        interval=db.execute("SELECT sum(elapsed_seconds) FROM forest_study_active_intervals WHERE parent_work_id=?",(episode['work_id'],)).fetchone()[0]
        assert interval>0
        assert db.execute("SELECT count(*) FROM forest_study_work").fetchone()[0]==1


def test_common_runtime_permits_one_feedback_revision_and_retains_invalid_submit(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path,facts=FACTS['alias'])
    controller=_session(runtime,episode['work_id'])
    correct={key:controller['truth'][key] for key in ['invalidate_record_ids','reuse_record_ids']}
    client=GuardedFixtureClient([
        {'mode':'submit','actions':[],'submission':{'invalidate_record_ids':[],'reuse_record_ids':[]},'rationale':'An intentionally invalid fixture proposal.'},
        {'mode':'submit','actions':[],'submission':correct,'rationale':'One fixture revision from independent validation.'},
    ])
    first=runtime.run_one(owner='fixture',client=client)
    assert first['action_result']['valid'] is False
    final=runtime.run_one(owner='fixture',client=client)
    assert final['action_result']['valid'] is True
    assert final['action_history'][0]['submission_validation']['valid'] is False
    assert client.counter==[2]
    with runtime.ledger.connect() as db:
        assert db.execute('SELECT count(*) FROM forest_study_model_calls').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM forest_study_replay_requests').fetchone()[0]==2


def test_identical_invalid_submission_pauses_only_after_diagnostic(tmp_path):
    runtime,episode,_=runtime_fixture(tmp_path,facts=FACTS['alias'])
    invalid={'invalidate_record_ids':[],'reuse_record_ids':[]}
    proposal={'mode':'submit','actions':[],'submission':invalid,
        'rationale':'The same semantic proposal with harmless prose variation.'}
    client=GuardedFixtureClient([proposal,proposal,proposal])
    first=runtime.run_one(owner='fixture',client=client)
    assert first['action_result']['valid'] is False
    second=runtime.run_one(owner='fixture',client=client)
    assert second['state']=='retryable'
    assert second['action_result']['no_progress_observation']['diagnostic_previously_delivered'] is False
    third=runtime.run_one(owner='fixture',client=client)
    assert third['state']=='paused_no_progress', str(third)
    assert third['action_result']['no_progress_observation']['diagnostic_previously_delivered'] is True
    assert client.counter==[3]


def test_secondary_model_replay_uses_same_source_contract_and_independent_context(tmp_path):
    runtime,episode,prepared=runtime_fixture(tmp_path)
    second={**prepared,'work_id':'second-replay-fixture','kind':'replay',
        'model_provider':{'id':'second-fixture','model':'gpt-6.1-sol','config':{'reasoning_effort':'low'}},
        'episode':{'category':'SECOND_MODEL_REPLAY','task_id':'generic-task','arm':'DAG_ID','repeat':2}}
    second.pop('arm')
    derived=prepare_replay(runtime,second)
    assert derived['kind']=='replay' and derived['category']=='SECOND_MODEL_REPLAY' and derived['arm']=='DAG_ID'
    assert derived['model_provider']['model']=='gpt-6.1-sol'
    assert _session(runtime,'second-replay-fixture')['records']==_session(runtime,episode['work_id'])['records']
    assert not set(row['work_id'] for row in derived['action_bindings'])&set(row['work_id'] for row in prepared['action_bindings'])
def test_public_projection_preserves_complete_ordered_scientific_inputs():
    from research.experiments.mechanism_replay import _public
    source = {'train_sample_ids': list(range(4096)),
              'validation_origin_ids': [f'origin-{i}' for i in range(512)],
              'nested': {'parameters': list(range(128))},
              'oracle': {'expected_answer': 'private'}}
    public = _public(source)
    assert public['train_sample_ids'] == source['train_sample_ids']
    assert public['validation_origin_ids'] == source['validation_origin_ids']
    assert public['nested'] == source['nested']
    assert 'oracle' not in public
