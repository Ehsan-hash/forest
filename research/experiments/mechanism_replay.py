"""Source-bound mechanism replay; virtual actions never execute scientific fits.

The caller owns real model requests and their actual resource accounting. This module
retains the original snapshot, applies declared factual changes, records virtual
operations, and independently checks the resulting repair plan. Missing revised
artifacts remain unavailable rather than becoming invented scientific evidence.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import time
from typing import Mapping


ARMS = {'FULL','FLAT','DAG_ID','ID_ONLY','RERUN_ALL','FULL_NO_GATE'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS forest_replay_sessions (
 campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, state_json TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(campaign_id,work_id));
CREATE TABLE IF NOT EXISTS forest_replay_actions (
 campaign_id TEXT NOT NULL, action_work_id TEXT NOT NULL, parent_work_id TEXT NOT NULL,
 binding_json TEXT NOT NULL, result_json TEXT NOT NULL, recorded_at REAL NOT NULL,
 PRIMARY KEY(campaign_id,action_work_id));
'''


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _read(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict):
        raise ValueError('Replay source must be a JSON object: '+str(path))
    return value


def _metadata(path):
    stat=Path(path).stat()
    return [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]


def _merge(before,change):
    result=deepcopy(before)
    for key,value in change.items():
        result[key]=_merge(result[key],value) if isinstance(value,Mapping) and isinstance(result.get(key),Mapping) else deepcopy(value)
    return result


def _public(value):
    """Omit controller answers; keep actual scientific facts and source references."""
    if isinstance(value,Mapping):
        return {key:_public(item) for key,item in value.items() if key not in
            {'controller_mutation_id','mutation_id','mutation','oracle','expected_actions','affected_record_ids',
             'C1_revision','long_from_scratch'}}
    if isinstance(value,list):
        return [_public(item) for item in value]
    return value


def _conditions(config,task):
    data_keys={'dataset_version_id','train_sample_ids','train_origin_ids','training_sample_count',
        'training_origin_count','removed_training_rows','excluded_train_origin_ids'}
    envelope={'work_id','task_id','configuration_id','phase','formal_score_eligible','output_directory',
        'output_reference','configuration_path','fit_timeout_seconds','log_every_updates','source_revision_reference',
        'registered_evaluation_contract','validation_sample_ids','validation_origin_ids','recipe_version_id'}
    return {'training_data':{key:deepcopy(config[key]) for key in data_keys if key in config},
        'training_recipe':{key:deepcopy(value) for key,value in config.items() if key not in data_keys|envelope},
        'validation_input':{'validation_sample_ids':deepcopy(config.get('validation_sample_ids')),
            'validation_origin_ids':deepcopy(config.get('validation_origin_ids')),
            **{key:deepcopy(value) for key,value in config.items() if key.startswith('validation_noise_')}},
        'evaluation_contract':{key:deepcopy(task.get(key)) for key in
            ('primary_metric','primary_direction','side_constraints','effect_threshold')}}


def _revised_conditions(before,facts):
    after=deepcopy(before)
    for key,facet in [('training_data_revision','training_data'),('training_recipe_revision','training_recipe'),
                      ('validation_input_config','validation_input')]:
        if key in facts:
            if not isinstance(facts[key],Mapping):
                raise ValueError('Replay factual revision must be a mapping: '+key)
            after[facet]=_merge(before[facet],facts[key])
    if 'evaluation_contract' in facts:
        revision=dict(facts['evaluation_contract'])
        rename={'revised_primary_metric':'primary_metric','revised_direction':'primary_direction',
                'revised_side_constraints':'side_constraints','revised_effect_threshold':'effect_threshold'}
        revision={rename.get(key,key):value for key,value in revision.items()
            if key not in {'original_primary_metric','training_contract_unchanged','selection_rule','revision_id'}}
        after['evaluation_contract']=_merge(before['evaluation_contract'],revision)
    # An alias alone does not change the scientific training/input conditions.
    alias=facts.get('data_alias')
    if alias and not (alias.get('ordered_sample_ids_unchanged') is True and alias.get('same_dataset_version')):
        raise ValueError('Data alias requires actual unchanged ordered IDs and dataset version provenance')
    return after


def build_controller(records,task,factual_changes,*,arm,work_id):
    """Build a readable dependency controller from records, never from C-labels."""
    if arm not in ARMS or not records or not work_id:
        raise ValueError('Replay needs a supported treatment, explicit work ID and original records')
    nodes=[];edges=[];changed=set();bindings=[]
    scopes={}
    for record in records:
        source=str(record['source_work_id'])
        before=_conditions(record['configuration'],task)
        after=_revised_conditions(before,factual_changes)
        changed.update(key for key in before if before[key]!=after[key])
        scopes[source]={'before':before,'after':after}
        parent=None
        for suffix,kind,facet,operation in [('checkpoint','checkpoint',['training_data','training_recipe'],'train'),
                ('predictions','predictions',['validation_input'],'infer'),
                ('metrics','metrics',['evaluation_contract'],'score')]:
            record_id=source+'--'+suffix
            nodes.append({'id':record_id,'kind':kind,'configuration_id':record['configuration_id'],
                'seed':record['seed'],'source_work_id':source,'scope_inputs':facet,
                'source_reference':record.get(suffix+'_reference')})
            if parent: edges.append({'from':parent,'to':record_id,'relation':'produces'})
            parent=record_id
            bindings.append({'kind':'replay_transition','action_name':operation,
                'work_id':work_id+'--replay--'+operation+'--'+source,
                'record_id':record_id,'source_work_id':source,'configuration_id':record['configuration_id'],
                'seed':record['seed']})
    for suffix in ('selection','comparison','claim'):
        nodes.append({'id':work_id+'--original-'+suffix,'kind':suffix,'scope_inputs':['evaluation_contract']})
    selection,comparison,claim=[work_id+'--original-'+suffix for suffix in ('selection','comparison','claim')]
    edges.extend({'from':node['id'],'to':selection,'relation':'selection_input'} for node in nodes if node['kind']=='metrics')
    edges.extend([{'from':selection,'to':comparison,'relation':'paired_comparison'},
                  {'from':comparison,'to':claim,'relation':'supports'}])
    invalid={node['id'] for node in nodes if set(node['scope_inputs'])&changed}
    while True:
        propagated=invalid|{edge['to'] for edge in edges if edge['from'] in invalid}
        if propagated==invalid: break
        invalid=propagated
    required={node['id'] for node in nodes if node['id'] in invalid and node['kind']!='claim'}
    if arm=='RERUN_ALL': required={node['id'] for node in nodes if node['kind']!='claim'}
    for name,record_id in [('select',selection),('compare',comparison),('inspect',None)]:
        bindings.append({'kind':'replay_transition','action_name':name,'work_id':work_id+'--replay--'+name,
            'record_id':record_id,'configuration_id':None,'seed':None})
    return {'version':1,'work_id':work_id,'arm':arm,'task':deepcopy(task),'facts':deepcopy(factual_changes),
        'records':deepcopy(records),'nodes':nodes,'edges':edges,'conditions':scopes,'action_bindings':bindings,
        'truth':{'changed_facets':sorted(changed),'invalidate_record_ids':sorted(invalid),
            'reuse_record_ids':sorted({node['id'] for node in nodes}-invalid),'required_record_ids':sorted(required)}}


def _view(controller):
    arm=controller['arm']
    records=[{key:deepcopy(row[key]) for key in ('source_work_id','configuration_id','seed','observed_metrics',
        'receipt_reference','checkpoint_reference','predictions_reference','metrics_reference') if key in row} for row in controller['records']]
    for observation,original in zip(records,controller['records']):
        observation['measured_original_fit_gpu_seconds']=original.get('virtual_training_reference_seconds')
    nodes=deepcopy(controller['nodes'])
    if arm in {'FULL','FULL_NO_GATE'}:
        return {'view':'typed_dependency_graph','nodes':nodes,'edges':controller['edges'],'observations':records}
    if arm=='DAG_ID':
        return {'view':'untyped_dependency_graph','node_ids':[node['id'] for node in nodes],
            'edges':[{'from':edge['from'],'to':edge['to']} for edge in controller['edges']],
            'records':[{key:row[key] for key in ('id','source_reference') if key in row} for row in nodes],
            'observations':records}
    return {'view':'identifier_records_without_propagation' if arm=='ID_ONLY' else 'flat_records',
        'records':nodes,'observations':records,'graph_propagation':False}


def _session(runtime,work_id):
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
        row=db.execute('SELECT state_json FROM forest_replay_sessions WHERE campaign_id=? AND work_id=?',
            (runtime.campaign_id,work_id)).fetchone()
    return json.loads(row[0]) if row else None


def prepare_replay(runtime,payload):
    from .study_runtime import _normalize_episode_payload,_manifest_work_items
    episode=_normalize_episode_payload(payload)
    work_id=str(episode.get('work_id',''))
    if not work_id: raise ValueError('A registered replay work ID is required')
    controller=_session(runtime,work_id)
    if controller is None:
        task=next((deepcopy(row) for row in runtime.manifest.get('tasks',[]) if row.get('task_id')==episode.get('task_id')),None)
        if task is None: raise ValueError('Replay task is not registered in the current manifest')
        fits=[row for row in _manifest_work_items(runtime.manifest) if row.get('kind')=='fit' and row.get('task_id')==episode['task_id']]
        rows=runtime._episode_candidate_rows(episode,fits)
        runtime._select_original_pair(task,rows,episode)  # Require actual paired source evidence.
        records=[]
        for row in rows:
            if row['state']!='completed' or not row.get('receipt_path'):
                raise ValueError('Replay snapshot contains an incomplete or uncertified original record')
            config=row['config'];receipt=row['receipt'];folder=Path(row['receipt_path']).parent
            prediction_name=receipt.get('validation_prediction_file')
            if not prediction_name:
                raise ValueError('Replay original receipt has no observed prediction artifact')
            prediction=folder/str(prediction_name)
            checkpoint=folder/str(receipt.get('checkpoint_file','checkpoint.pt'))
            references={str(row['receipt_path']):{'value':receipt,'metadata':_metadata(row['receipt_path'])}}
            effective=folder/'effective_config.json'
            references[str(effective)]={'value':config,'metadata':_metadata(effective)}
            artifact_metadata={str(path):_metadata(path) for path in (prediction,checkpoint) if path.is_file()}
            if len(artifact_metadata)!=2:
                raise ValueError('Replay original snapshot artifacts are missing')
            records.append({'source_work_id':str(config['work_id']),'configuration_id':config['configuration_id'],
                'seed':config['seed'],'configuration':config,'observed_metrics':receipt['validation_metrics'],
                'receipt_reference':str(row['receipt_path']),'checkpoint_reference':str(checkpoint),
                'predictions_reference':str(prediction),'metrics_reference':str(row['receipt_path']),
                'virtual_training_reference_seconds':row.get('fit_gpu_seconds'),'original_sources':references,
                'original_artifact_metadata':artifact_metadata})
        controller=build_controller(records,task,episode.get('factual_changes') or {},
            arm=episode['arm'],work_id=work_id)
        with runtime.ledger.connect() as db:
            db.execute('INSERT OR IGNORE INTO forest_replay_sessions VALUES(?,?,?,?)',
                (runtime.campaign_id,work_id,_json(controller),time.time()))
        controller=_session(runtime,work_id)
    if controller['arm']!=episode['arm'] or controller['facts']!=(episode.get('factual_changes') or {}) or controller['task'].get('task_id')!=episode.get('task_id'):
        raise ValueError('A replay work ID cannot acquire different scientific conditions or treatment')
    public={'task_contract':_public(controller['task']),'factual_changes':_public(controller['facts']),
        'before_conditions':_public({key:row['before'] for key,row in controller['conditions'].items()}),
        'evidence_view':_view(controller),'action_bindings':controller['action_bindings'],
        'replay_semantics':'Actions replay registered original artifacts. Missing revised artifacts are REPLAY_ACTION_UNAVAILABLE, never a real training/inference result. Submit the invalidation/reuse sets and a source-bound repair plan; do not invent metrics or improvement.'}
    decision_schema={
        'type':'object','required':['mode','actions','submission','rationale'],
        'properties':{
            'mode':{'enum':['action','submit']},
            'actions':{'type':'array','items':{
                'type':'object','required':['kind','action_work_id'],
                'properties':{
                    'kind':{'enum':sorted({b['action_name'] for b in controller['action_bindings']})},
                    'action_work_id':{'enum':[b['work_id'] for b in controller['action_bindings']]}}}},
            'submission':{'type':'object','properties':{
                'invalidate_record_ids':{'type':'array','items':{'type':'string'}},
                'reuse_record_ids':{'type':'array','items':{'type':'string'}},
                'selected_configuration_id':{'type':['string','null']},
                'claim':{'type':'string'},'scientific_repair_complete':{'type':'boolean'}}},
            'rationale':{'type':'string'}}}
    return {**episode,'kind':'replay','replay_controller_version':1,'messages':[
        {'role':'system','content':'Evaluate one source-bound dependency mechanism replay. The proposed plan is independently validated. The replay produces no physical training or inference. Use supplied factual changes and records, without inferring unobserved revised performance.'},
        {'role':'user','content':_json(public)}], 'decision_schema':decision_schema,
        'allowed_decisions':sorted({binding['action_name'] for binding in controller['action_bindings']}),
        'action_bindings':controller['action_bindings'],'output_stage':'experiment'}


def _sources_match(controller):
    for record in controller['records']:
        for path,original in record.get('original_sources',{}).items():
            try:
                if _metadata(path)!=original['metadata'] and _read(path)!=original['value']: return False
            except (OSError,ValueError,UnicodeError): return False
        for path,original_metadata in record.get('original_artifact_metadata',{}).items():
            try:
                if _metadata(path)!=original_metadata: return False
            except OSError: return False
    return True


def _selection(controller):
    """Re-evaluate a contract using only observed original values, never forecasts."""
    task=controller['task']
    contract=next(iter(controller['conditions'].values()))['after']['evaluation_contract']
    metric=contract.get('primary_metric');direction=contract.get('primary_direction')
    if direction not in {'minimize','maximize'} or not metric:
        raise ValueError('Replay selection requires an explicit active metric and direction')
    anchors=[row['configuration_id'] for row in task.get('snapshot_configurations',[]) if row.get('is_anchor')]
    if len(anchors)!=1: raise ValueError('Replay task must register one fixed anchor')
    by_config={}
    for record in controller['records']:
        value=record['virtual_training_reference_seconds'] if metric=='fit_gpu_seconds' else record['observed_metrics'].get(metric)
        if value is None or not math.isfinite(float(value)): continue
        by_config.setdefault(record['configuration_id'],[]).append((record,float(value)))
    seeds=set(task.get('shared_snapshot_seeds',[]))
    by_config={key:rows for key,rows in by_config.items() if {row['seed'] for row,_ in rows}==seeds and len(rows)==len(seeds)}
    anchor=by_config.get(anchors[0])
    if not anchor: raise ValueError('Active selection metric is not observed for every paired anchor seed')
    anchor_by_seed={row['seed']:row for row,_ in anchor}
    eligible=[];candidates=[]
    for config_id,rows in by_config.items():
        if config_id==anchors[0]: continue
        mean=sum(value for _,value in rows)/len(rows)
        candidate={'configuration_id':config_id,'mean_metric':mean,'rows':rows};candidates.append(candidate)
        allowed=True
        for constraint in contract.get('side_constraints') or []:
            key=constraint['metric'];margin=float(constraint['margin']);rule=constraint['allowed_direction']
            for row,_ in rows:
                base=anchor_by_seed[row['seed']]
                left=row['virtual_training_reference_seconds'] if key=='fit_gpu_seconds' else row['observed_metrics'].get(key)
                right=base['virtual_training_reference_seconds'] if key=='fit_gpu_seconds' else base['observed_metrics'].get(key)
                if left is None or right is None or not math.isfinite(float(left)) or not math.isfinite(float(right)):
                    allowed=False;continue
                if rule=='at_most_anchor_times': passes=left<=margin*right
                elif rule=='at_least_anchor_times': passes=left>=margin*right
                elif rule=='no_more_than_absolute_drop': passes=left>=right-margin
                elif rule=='no_more_than_absolute_increase': passes=left<=right+margin
                else: raise ValueError('Unknown registered side constraint: '+rule)
                allowed=allowed and passes
        if allowed: eligible.append(candidate)
    if not candidates: raise ValueError('No observed non-anchor candidate is available under the active metric')
    sign=1 if direction=='minimize' else -1
    chosen=min(eligible or candidates,key=lambda row:(sign*row['mean_metric'],row['configuration_id']))
    selected_by_seed={row['seed']:value for row,value in chosen['rows']}
    anchor_values={row['seed']:value for row,value in anchor}
    return {'selected_configuration_id':chosen['configuration_id'],'anchor_configuration_id':anchors[0],
        'active_metric':metric,'direction':direction,'constraint_satisfied':bool(eligible),
        'original_metric_mean':chosen['mean_metric'],'paired_seed_ids':sorted(seeds),
        'original_paired_differences':[{ 'seed':seed,'selected_minus_anchor':selected_by_seed[seed]-anchor_values[seed]} for seed in sorted(seeds)],
        'value_source':'Observed original snapshot records under the applicable contract; no revised fit'}


def execute_replay_action(runtime,episode,action):
    started_at=time.time();started_clock=time.monotonic()
    controller=_session(runtime,str(episode['work_id']))
    if controller is None: raise ValueError('Replay controller must be persisted before executing a decision')
    if not _sources_match(controller):
        return {'kind':'replay_transition','state':'awaiting_reconciliation',
            'action_work_id':action.get('action_work_id'),'result':{'status':'REPLAY_SOURCE_RECONCILIATION','gpu_actual_seconds':0}}
    name=str(action.get('kind',action.get('action','')))
    binding=next((row for row in controller['action_bindings'] if row['work_id']==action.get('action_work_id')
        and row['action_name']==name),None)
    if binding is None or any(action.get(key) is not None and action[key]!=binding.get(key) for key in ('configuration_id','seed')):
        raise ValueError('Replay action must select its exact registered operation and record binding')
    with runtime.ledger.connect() as db:
        previous=db.execute('SELECT * FROM forest_replay_actions WHERE campaign_id=? AND action_work_id=?',
            (runtime.campaign_id,binding['work_id'])).fetchone()
    if previous:
        if previous['binding_json']!=_json(binding) or previous['parent_work_id']!=episode['work_id']:
            raise ValueError('A replay action identity was reused with different conditions')
        return json.loads(previous['result_json'])
    record=next((row for row in controller['records'] if row['source_work_id']==binding.get('source_work_id')),None)
    changed=set(controller['truth']['changed_facets'])
    requires_new_outputs=bool(changed&{'training_data','training_recipe'} or name in {'infer','score','select','compare'} and 'validation_input' in changed)
    unavailable=name!='inspect' and requires_new_outputs
    result={'status':'REPLAY_ACTION_UNAVAILABLE' if unavailable else 'REPLAYED','operation':name,
        'record_id':binding['record_id'],'replay_only':True,'physical_fit_executed':False,
        'physical_inference_executed':False,'gpu_actual_seconds':0,'scientific_result_generated':False,
        'target_conditions':_public(controller['conditions'].get(binding.get('source_work_id'))),
        'original_artifact_references':{key:value for key,value in (record or {}).items() if key.endswith('_reference')},
        'virtual_reference_seconds':record.get('virtual_training_reference_seconds') if record and name=='train' else None,
        'virtual_reference_measurement_source':record['source_work_id'] if record and name=='train' else None,
        'availability_reason':'No predeclared artifact certifies the changed scientific conditions' if unavailable else 'Existing registered original artifacts; no newly fitted values'}
    if name=='inspect': result['observations']=_view(controller)['observations']
    elif name=='score' and not unavailable: result['original_observed_metrics']=deepcopy(record['observed_metrics'])
    elif name in {'select','compare'} and not unavailable:
        try: result['artifact_based_analysis']=_selection(controller)
        except ValueError as exc:
            result.update(status='REPLAY_ACTION_UNAVAILABLE',availability_reason=str(exc))
    response={'kind':'replay_transition','state':'completed','action_work_id':binding['work_id'],'result':result}
    finished_at=time.time();elapsed_seconds=max(0.0,time.monotonic()-started_clock)
    result.update(started_at=started_at,finished_at=finished_at,elapsed_seconds=elapsed_seconds)
    with runtime.ledger.connect() as db:
        inserted=db.execute('INSERT OR IGNORE INTO forest_replay_actions VALUES(?,?,?,?,?,?)',
            (runtime.campaign_id,binding['work_id'],episode['work_id'],_json(binding),_json(response),finished_at)).rowcount
        if inserted:
            columns={row[1] for row in db.execute('PRAGMA table_info(forest_study_active_intervals)')}
            if 'category' in columns:
                db.execute('INSERT INTO forest_study_active_intervals(interval_id,campaign_id,parent_work_id,action_work_id,category,started_at,finished_at,elapsed_seconds,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    ('replay:'+binding['work_id'],runtime.campaign_id,episode['work_id'],binding['work_id'],
                     'replay_controller',started_at,finished_at,elapsed_seconds,finished_at))
            elif 'source' in columns:
                db.execute('INSERT INTO forest_study_active_intervals(campaign_id,parent_work_id,source,elapsed_seconds,recorded_at) VALUES(?,?,?,?,?)',
                    (runtime.campaign_id,episode['work_id'],'replay_controller:'+binding['work_id'],elapsed_seconds,finished_at))
        return json.loads(db.execute('SELECT result_json FROM forest_replay_actions WHERE campaign_id=? AND action_work_id=?',
            (runtime.campaign_id,binding['work_id'])).fetchone()[0])


def verify_replay_submission(runtime,episode,history,submission):
    work_id=str(episode['work_id'])
    controller=_session(runtime,work_id)
    if controller is None or not _sources_match(controller):
        return {'state':'failed','valid':False,'reason':'Original replay source requires reconciliation','scientific_repair_complete':False}
    with runtime.ledger.connect() as db:
        rows=db.execute('SELECT result_json FROM forest_replay_actions WHERE campaign_id=? AND parent_work_id=?',
            (runtime.campaign_id,work_id)).fetchall()
    recorded=[json.loads(row[0]) for row in rows]
    completed={row['action_work_id']:row for row in recorded}
    submitted_actions=[action for entry in history for action in entry.get('actions',[])
        if isinstance(action,Mapping) and not (
            action.get('action_name')=='read_context_segment' or
            isinstance(action.get('result'),Mapping) and
            action['result'].get('operation')=='read_context_segment')]
    forged=[action.get('action_work_id') for action in submitted_actions if action.get('state')=='completed' and
        (action.get('kind')!='replay_transition' or completed.get(action.get('action_work_id'))!=action)]
    performed={row['result']['record_id'] for row in recorded if row['result']['record_id'] is not None}
    truth=controller['truth'];missing=sorted(set(truth['required_record_ids'])-performed)
    invalidation=submission.get('invalidate_record_ids');reuse=submission.get('reuse_record_ids')
    invalidation_matches=isinstance(invalidation,list) and all(isinstance(value,str) for value in invalidation) and len(invalidation)==len(set(invalidation)) and set(invalidation)==set(truth['invalidate_record_ids'])
    reuse_matches=isinstance(reuse,list) and all(isinstance(value,str) for value in reuse) and len(reuse)==len(set(reuse)) and set(reuse)==set(truth['reuse_record_ids'])
    unavailable=[row['action_work_id'] for row in recorded if row['result']['status']=='REPLAY_ACTION_UNAVAILABLE']
    fabricated=submission.get('scientific_repair_complete') is True
    selected=submission.get('selected_configuration_id')
    selected_matches=True
    if selected is not None:
        analyses=[row['result'].get('artifact_based_analysis') for row in recorded if row['result'].get('operation')=='select']
        applicable=[value for value in analyses if value is not None]
        selected_matches=bool(applicable and applicable[-1]['selected_configuration_id']==selected)
    valid=not forged and not missing and invalidation_matches and reuse_matches and not fabricated and selected_matches
    result={'state':'completed' if valid else 'failed','valid':valid,'replay_only':True,
        'mechanistic_plan_valid':valid,'scientific_repair_complete':False,'gpu_actual_seconds':0,
        'gate_mode':'advisory' if controller['arm']=='FULL_NO_GATE' else 'enforced',
        'proposal_accepted_without_gate':controller['arm']=='FULL_NO_GATE',
        'invalidation_matches':invalidation_matches,'reuse_matches':reuse_matches,'missing_record_operations':missing,
        'selection_matches_observed_artifacts':selected_matches,
        'unavailable_action_work_ids':unavailable,'forged_history_actions':forged,
        'completed_virtual_action_work_ids':sorted(completed),
        'virtual_reference_seconds':sum(row['result']['virtual_reference_seconds'] for row in recorded if row['result']['virtual_reference_seconds'] is not None),
        'virtual_reference_costs_missing':sum(row['result']['virtual_reference_seconds'] is None for row in recorded),
        'scientific_metric_values_generated':False,'submitted_claim':submission.get('claim'),
        'submitted_claim_is_unverified_model_text':True}
    return result
