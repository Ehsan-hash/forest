"""Database-backed worker. Browser/API lifetimes do not own scientific processes."""
from __future__ import annotations
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
import psutil
from sqlalchemy import select, text
from services.api.db import *
from services.api.common import get,project_dir,safe_path,emit
from services.api.config import ROOT,settings
from runners.local import process_matches,stop_group,process_tree_resources
from services.worker.scheduler import ACTIVE, _lock_project, reserve_project_time, freeze_budget_exhausted_elapsed

from research.execution.recovery import recovery_decision, resource_request, admission
from research.execution.process_manager import process_manager
from research.agents.budget import interrupt_run_reservations
from runners.container import cancel_container, control_container, ContainerRunner

PROCESS_STATES=('running','paused','pausing')
WORKER_POLL_INTERVAL_SECONDS=0.4
TIMEOUT_STOP_GRACE_SECONDS=0.1

def capacity():
    return {'cpu': float(os.environ.get('FOREST_WORKER_CPU_SLOTS', os.cpu_count() or 1)),
            'memory_bytes': int(float(os.environ.get('FOREST_WORKER_MEMORY_GB', psutil.virtual_memory().total / 1024**3)) * 1024**3),
            'gpu': int(os.environ.get('FOREST_WORKER_GPU_SLOTS', 0))}


def task_request(config):
    request=resource_request(config)
    if config.get('execution_backend')=='container':
        import re
        options=config.get('container') or {}
        cpu=float(options.get('cpus',1))
        memory=str(options.get('memory','2g')).lower()
        match=re.fullmatch(r'([1-9][0-9]*)([bkmg]?)',memory)
        if not match: raise ValueError('Invalid container memory limit')
        memory_bytes=int(match[1])*{'':1,'b':1,'k':1024,'m':1024**2,'g':1024**3}[match[2]]
        gpu=options.get('gpus',0)
        gpu=int(os.environ.get('FOREST_WORKER_GPU_SLOTS',0)) or 1 if gpu=='all' else int(gpu or 0)
        maximum=int(options.get('max_processes',1))
        if not 1<=maximum<=32: raise ValueError('container.max_processes must be between 1 and 32')
        request={'cpu':max(request['cpu'],cpu*maximum),'memory_bytes':max(request['memory_bytes'],memory_bytes*maximum),'gpu':max(request['gpu'],gpu*maximum)}
    return request


def reservation_for(run):
    """Hold committed allocations until reconciliation, plus live detached work."""
    try: request=task_request(run.config)
    except (ValueError,TypeError): request=resource_request({})
    workspace=safe_path(project_dir(run.project_id),run.output_path+'/workspace')
    backend=run.config.get('execution_backend')
    folder=(workspace.parent/'.forest-container-processes'/workspace.name if backend=='container'
        else workspace.parent/'.forest-processes'/workspace.name if backend=='bubblewrap' else workspace/'.forest-processes')
    managed=[]
    if folder.is_dir() or (backend=='bubblewrap' and (workspace/'.forest-processes').is_dir()):
        try: managed=[item for item in process_manager(workspace,run.config).all() if item['status'] not in ('completed','failed','cancelled','lost')]
        except (OSError,ValueError,KeyError,RuntimeError):
            if run.config.get('execution_backend')=='container': managed=[{'status':'unknown'}]
    executor_live=bool(run.pid and process_matches(run.pid,run.process_created))
    container_active=False
    handle=workspace.parent/'container_task.json'
    if run.config.get('execution_backend')=='container' and handle.exists():
        try:
            state=ContainerRunner(run.config.get('container')).status(json.loads(handle.read_text()))
            container_active=state['status'] in ('running','paused','starting')
        except (OSError,ValueError,RuntimeError): container_active=True  # Retain reservation during daemon uncertainty.
    # A different worker must not release this slot between executor exit and
    # the owner's committed terminal/recovery transition. Never-started paused
    # nodes have no allocation; their detached work, if any, is checked above.
    committed=(run.status in ('running','pausing') or (run.status=='paused' and run.pid is not None)
        or bool((run.resource or {}).get('graph_recovery_compute_hold'))
        or bool((run.resource or {}).get('execution_reconciliation_compute_hold'))
        or bool((run.resource or {}).get('execution_handoff_compute_hold')))
    if not committed and not executor_live and not managed and not container_active: return None
    request['memory_bytes']=max(request['memory_bytes'],int(run.resource.get('rss_bytes') or 0),sum(int(item.get('rss_bytes') or 0) for item in managed))
    if run.config.get('execution_backend')!='container': request['cpu']=max(request['cpu'],len(managed))
    return request


def pause_container_for_reconnect(config, output):
    """Keep a detached live container stopped until its next run is admitted."""
    handle = Path(output) / 'container_task.json'
    if not handle.is_file():
        return None
    runner = ContainerRunner(config.get('container'))
    job = json.loads(handle.read_text())
    state = runner.status(job)
    if state['status'] == 'running':
        state = runner.pause(job)
        if state['status'] == 'running':
            raise RuntimeError('Container remained live while its executor was interrupted')
    return state


def _claim_queued_candidate(session, candidate):
    """Claim a run using the shared project -> task lock order."""
    project = _lock_project(session, candidate.project_id)
    run = session.scalar(select(TaskRun).where(
        TaskRun.id == candidate.id, TaskRun.status == 'queued'
    ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
    return run, project


def _lock_waiting_snapshot(session, value):
    """Refresh a yielded run after taking the API's project -> run locks."""
    _lock_project(session, value['project_id'])
    run = session.scalar(select(TaskRun).where(TaskRun.id == value['id'])
        .with_for_update().execution_options(populate_existing=True))
    if not run or run.status != value['status']:
        return None
    current = run.resource or {}
    observed = value.get('resource') or {}
    if current.get('waiting_started_at') != observed.get('waiting_started_at'):
        return None
    if ((getattr(run,'config',None) or {}).get('execution_attempt', {}).get('id') !=
            (value.get('config') or {}).get('execution_attempt', {}).get('id')):
        return None
    if current.get('wait_for') != observed.get('wait_for'):
        return None
    return run


def resume_live_process_for_dispatch(config, workspace, resource):
    """Continue a yielded local child only after its run passes admission."""
    if (config.get('execution_backend') != 'container' and
            (resource or {}).get('live_process_pending_dispatch')):
        process_manager(workspace, config).signal_all(signal.SIGCONT)


def _waiting_outcome_resource(resource, outcome, observed_at=None):
    """Persist a live wait clock only while work is actually waiting to resume."""
    value = {**(resource or {}), 'wait_for': outcome.get('wait_for', {}),
             'resume_after': outcome.get('resume_after')}
    reconcile=(outcome.get('status')=='waiting_input' and (
        (outcome.get('wait_for') or {}).get('type')=='execution_reconciliation' or
        ((value.get('blocked_reason')=='execution_reconciliation') and
         (value.get('reconciliation_jobs') or value.get('reconciliation_job')))))
    if reconcile:value['execution_reconciliation_compute_hold']=True
    if reconcile or (outcome.get('status') == 'waiting' and
            (outcome.get('wait_for') or {}).get('type') != 'work_round'):
        value['waiting_started_at'] = time.time() if observed_at is None else observed_at
        value['elapsed_before_wait'] = value.get('elapsed_seconds', 0)
    else:
        value.pop('waiting_started_at', None)
        value.pop('elapsed_before_wait', None)
    return value


def _reconcile_execution_hold(run,output):
    """Keep unknown owned computation charged until original receipts settle."""
    resource=dict(run.resource or {})
    if not resource.get('execution_reconciliation_compute_hold'):return
    manager=None;inspection_error=None
    try:
        manager=process_manager(Path(output)/'workspace',run.config)
        jobs=manager.all()
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        inspection_error=str(exc);jobs=[{'status':'reconciliation','error':inspection_error}]
    requested=set(resource.get('reconciliation_jobs') or [])
    requested.update(ident for ident in (resource.get('reconciliation_job'),
        (resource.get('wait_for') or {}).get('process_id')) if ident)
    missing=requested-{job.get('process_id') for job in jobs}
    jobs.extend({'process_id':ident,'status':'reconciliation','error':'Original job registry is missing'} for ident in missing)
    unfinished=[job for job in jobs if job.get('status') not in ('completed','failed','cancelled')]
    started=resource.get('waiting_started_at') or time.time()
    before=float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0)))
    total=before+max(0,time.time()-float(started))
    resource.update(waiting_started_at=started,elapsed_before_wait=before,elapsed_seconds=total,
        blocked_reason='execution_reconciliation',reconciliation_jobs=[job.get('process_id') for job in jobs if job.get('process_id')])
    timeout=run.config.get('timeout')
    if timeout is not None and total>=float(timeout):
        resource['hard_budget_exhausted']={'kind':'seconds','elapsed_seconds':total,'limit':float(timeout)}
        if unfinished and not resource.get('reconciliation_cancel_requested'):
            identity={'inspection_error':inspection_error,'jobs':[
                {'process_id':job.get('process_id'),'status':job.get('status'),'error':job.get('error')} for job in unfinished]}
            previous=resource.get('reconciliation_cancel_error') or {}
            if previous.get('identity')!=identity:
                try:
                    if manager is None:raise RuntimeError('Original process backend is unavailable: '+str(inspection_error))
                    manager.cancel_all()
                    resource['reconciliation_cancel_requested']=True
                    resource.pop('reconciliation_cancel_error',None)
                except (OSError,ValueError,KeyError,RuntimeError) as exc:
                    resource['reconciliation_cancel_error']={'identity':identity,'error':str(exc)}
            run.error='Actual task time budget exhausted; original job stop requires receipt reconciliation'
    if not unfinished:
        resource.pop('waiting_started_at',None);resource.pop('elapsed_before_wait',None)
        resource.pop('execution_reconciliation_compute_hold',None)
        resource['last_reconciled_jobs']=resource.pop('reconciliation_jobs',[])
        resource.pop('reconciliation_job',None)
        if resource.get('reconciliation_cancel_requested') or resource.get('reconciliation_cancel_error'):
            resource['last_reconciliation_cancellation'] = {
                'jobs':resource['last_reconciled_jobs'],
                'requested':resource.pop('reconciliation_cancel_requested',False),
                'error':resource.pop('reconciliation_cancel_error',None),'confirmed_at':now()}
        if resource.get('hard_budget_exhausted'):
            resource.update(blocked_reason='time_budget_exhausted')
            run.status='budget_exhausted';run.exit_code=124
            run.error='Actual task time budget exhausted; original job stop confirmed'
        else:
            resource.update(blocked_reason=None,wait_for={})
            run.status='queued';run.pid=None;run.process_created=None;run.error=None
            run.config={**run.config,'_next_attempt':{'mode':'continue','reason':'original_execution_reconciled'}}
    run.resource=resource


def _agent_checkpoint(output, run_id):
    """Load only an intact checkpoint owned by this run for recovery admission."""
    path = Path(output) / 'workspace' / 'agent_session.json'
    try:
        checkpoint = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(checkpoint, dict) or checkpoint.get('run_id') != run_id:
        return None
    if not isinstance(checkpoint.get('transcript'), list):
        return None
    # Scientific checkpoints carry decisions/evidence only. Actual resource
    # accounting is restored from the independent supervisor ledger; older
    # sessions with embedded usage remain readable until their next save.
    usage_path=Path(output)/'agent_usage.json'
    if not usage_path.exists(): usage_path=path.with_name('agent_usage.json')
    if usage_path.exists():
        try: usage=json.loads(usage_path.read_text())
        except (OSError,ValueError,UnicodeError): return None
        if not isinstance(usage,dict) or usage.get('run_id')!=run_id: return None
        checkpoint={**checkpoint,**{key:usage[key] for key in (
            'totals','active_seconds','cost_complete','usage_complete') if key in usage}}
    return checkpoint


def _legacy_step_continuation(value, output):
    """Migrate only old step-bound stops, never a user pause or hard exhaustion."""
    if value['status'] != 'budget_exhausted' or value['kind'] != 'agent':
        return False
    checkpoint = _agent_checkpoint(output, value['id'])
    reason = (checkpoint or {}).get('budget_reason', '')
    if not isinstance(reason, str) or not reason.startswith('Configured agent budget reached: steps='):
        return False
    if checkpoint.get('status') != 'budget_exhausted':
        return False
    if not isinstance(checkpoint.get('totals'), dict):
        return False
    from research.agents.runtime import budget_reason
    config = dict(value.get('config') or {})
    budgets = dict(config.get('agent_budget') or {})
    budgets.pop('steps', None)
    config['agent_budget'] = budgets
    config.pop('max_steps', None)
    # The runtime's resource check deliberately excludes cumulative step count.
    # Old dynamic step state is ignored while testing all actual resource caps.
    check = {**checkpoint, 'dynamic_step_budget': None}
    if budget_reason(check, config):
        return False
    timeout = config.get('timeout')
    elapsed = (value.get('resource') or {}).get('elapsed_seconds', 0)
    return timeout is None or float(elapsed or 0) < float(timeout)


def _checkpoint_recovery_config(run, output):
    """Limit repeated crashes at one checkpoint, without a lifetime retry cap."""
    config = dict(run.config or {})
    if run.kind != 'agent':
        return config
    checkpoint = _agent_checkpoint(output, run.id)
    if not checkpoint:
        return config
    identity = {
        'transcript': checkpoint.get('transcript'),
        'pending_action': checkpoint.get('pending_action'),
        'memory': checkpoint.get('memory'),
        'wait_for': checkpoint.get('wait_for'),
    }
    previous = (run.resource or {}).get('checkpoint_recovery') or {}
    failures = int(previous.get('failures', 0)) if previous.get('identity') == identity else 0
    config['_recovery_failures'] = failures
    run.resource = {**(run.resource or {}), 'checkpoint_recovery': {
        'identity': identity, 'failures': failures + 1,
        'checkpoint_step': len(checkpoint['transcript']),
    }}
    return config


def _admit_graph_recovery_handoff(session, run, output):
    """Consume a durable route intent only after the source receipt has settled.

    The caller holds project -> run locks. A live or uncertain detached job
    keeps its time/capacity reservation and must never release compute for a
    competing alternate merely because the Agent executor yielded.
    """
    pending=(run.resource or {}).get('pending_graph_recovery')
    if run.status!='waiting_input' or not isinstance(pending,dict): return
    if run.resource.get('hard_budget_exhausted') and not run.resource.get('graph_recovery_compute_hold'):
        run.resource={**run.resource,'blocked_reason':'time_budget_exhausted'}
        return
    manager=None
    registry_error=None
    try:
        manager=process_manager(Path(output)/'workspace',run.config)
        jobs=manager.all()
    except (OSError,ValueError,RuntimeError) as exc:
        registry_error=str(exc)
        jobs=[{'status':'reconciliation','error':str(exc)}]
    unfinished=[job for job in jobs if job.get('status') not in ('completed','failed','cancelled')]
    resource=dict(run.resource or {})
    if unfinished:
        started=resource.get('waiting_started_at') or time.time()
        before=float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0)))
        total=before+max(0,time.time()-float(started))
        resource.update(waiting_started_at=started,elapsed_before_wait=before,
            elapsed_seconds=total,graph_recovery_compute_hold=True)
        if run.config.get('timeout') is not None and total>=float(run.config['timeout']):
            resource.update(blocked_reason='time_budget_exhausted')
            run.error='Task time budget exhausted while reconciling its original recovery jobs'
            previous=resource.get('graph_recovery_cancel_error') or {}
            resource['hard_budget_exhausted']={'kind':'seconds','elapsed_seconds':total,
                'limit':float(run.config['timeout'])}
            try:
                # A registry failure cannot identify owned jobs safely. Retain
                # the allocation and do not blindly repeat the same cancellation.
                if registry_error and previous.get('registry_error')==registry_error:
                    raise RuntimeError(previous['error'])
                if manager is None:raise RuntimeError('Original process backend could not be inspected: '+str(registry_error))
                manager.cancel_all()
                resource.pop('graph_recovery_cancel_error',None)
            except (OSError,ValueError,RuntimeError) as exc:
                resource.update(blocked_reason='execution_reconciliation',
                    graph_recovery_cancel_error={'error':str(exc),'registry_error':registry_error})
                run.error+='; original jobs could not be proven cancelled: '+str(exc)
        elif any(job.get('status') in ('lost','reconciliation') for job in unfinished):
            resource.update(blocked_reason='execution_reconciliation')
            run.error='Original recovery jobs require receipt reconciliation before admitting another path'
        run.resource=resource
        return
    if resource.get('waiting_started_at') is not None:
        resource['elapsed_seconds']=float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0)))+max(
            0,time.time()-float(resource['waiting_started_at']))
    resource.pop('waiting_started_at',None); resource.pop('elapsed_before_wait',None)
    resource.pop('graph_recovery_compute_hold',None)
    run.resource=resource
    if resource.get('hard_budget_exhausted'):
        run.resource={**resource,'blocked_reason':'time_budget_exhausted'}
        return
    from research.agents.research_path import enqueue_recovery_alternatives
    context=pending.get('context') or {}
    decision=dict(pending.get('decision') or {})
    try:
        with session.begin_nested():
            rows=enqueue_recovery_alternatives(session,session.get(Project,run.project_id),context,source_run_id=run.id)
        decision.update(run_ids=[row.id for row in rows],handoff_status='admitted' if rows else 'superseded')
    except Exception as exc:
        detail=getattr(exc,'detail',{})
        code=detail.get('code') if isinstance(detail,dict) else None
        decision.update(handoff_status='blocked',admission_error=detail or str(exc))
        resource={**run.resource,'blocked_reason':'time_budget_exhausted' if code=='TIME_BUDGET_EXHAUSTED' else 'research_route_replan'}
        run.error=detail.get('message',str(exc)) if isinstance(detail,dict) else str(exc)
        previous=resource.get('graph_recovery_admission_error') or {}
        identity={'state':pending.get('checkpoint_state'),'error':str(exc)}
        failures=int(previous.get('failures',0))+1 if previous.get('identity')==identity else 1
        resource['graph_recovery_admission_error']={'identity':identity,'failures':failures,'error':str(exc)}
        if not code and failures<2:
            run.resource={**resource,'pending_graph_recovery':pending}
            return  # One idempotent retry; recurring admission faults pause.
        run.resource=resource
    resource=dict(run.resource or {})
    resource.pop('pending_graph_recovery',None)
    resource['graph_recovery_handoff']={'checkpoint_state':pending.get('checkpoint_state'),**decision}
    alert=dict(resource.get('route_alert') or {}); alert['decision']=decision
    resource['route_alert']=alert
    run.resource=resource
    emit(session,run.project_id,'research_recovery_handoff',{'run_id':run.id,
        'checkpoint_state':pending.get('checkpoint_state'),**decision})


def _sync_committed_graph_handoff(run, output):
    # The archived checkpoint remains the original intent. Only the editable
    # current scientific checkpoint advances to the admitted graph operation;
    # the independent usage ledger is never rewritten. This function consumes
    # an ALREADY committed DB receipt on the next tick, so rollback of admission
    # cannot leave the scientific checkpoint claiming nonexistent queued jobs.
    receipt=(run.resource or {}).get('graph_recovery_handoff')
    if not receipt or run.status!='waiting_input': return
    if run.pid and process_matches(run.pid,run.process_created): return
    checkpoint_path=Path(output)/'workspace'/'agent_session.json'
    try:
        checkpoint=json.loads(checkpoint_path.read_text())
        if checkpoint.get('run_id')==run.id:
            checkpoint['pending_graph_recovery']=None
            if (checkpoint.get('wait_for') or {}).get('type')=='graph_recovery_handoff':
                checkpoint['wait_for']=None
            checkpoint['graph_recovery_handoff']=receipt
            execution=(checkpoint.get('execution_snapshot') or {}).get('execution')
            if isinstance(execution,dict):
                execution['next_operation']={'type':'await_graph_alternatives',
                    'run_ids':receipt.get('run_ids',[]),'handoff_status':receipt.get('handoff_status')}
            from research.agents.processes import atomic_json
            atomic_json(checkpoint_path,checkpoint)
            run.resource={**run.resource,'graph_recovery_checkpoint_synced':receipt.get('checkpoint_state')}
    except (OSError,ValueError,UnicodeError) as exc:
        run.resource={**run.resource,'graph_recovery_checkpoint_error':str(exc)}


def _advance_research_submission(session, run, output):
    """Atomic release/admission, then resume the same caller from real receipts."""
    if run.status != 'waiting_input' or run.resource.get('pending_intervention'):
        return
    resource = dict(run.resource or {})
    pending = resource.get('pending_submission')
    if pending:
        if resource.get('hard_budget_exhausted') and not resource.get('execution_handoff_compute_hold'):
            # An accepted child intent cannot revive a caller whose actual
            # execution allowance was exhausted before the handoff settled.
            run.resource = {**resource,'blocked_reason':'time_budget_exhausted'}
            return
        manager = None
        registry_error = None
        try:
            manager = process_manager(Path(output) / 'workspace', run.config)
            jobs = manager.all()
        except (OSError,ValueError,RuntimeError) as exc:
            registry_error = str(exc)
            jobs = [{'status': 'reconciliation', 'error': registry_error}]
        unfinished = [job for job in jobs if job.get('status') not in ('completed','failed','cancelled')]
        if unfinished:
            started = resource.get('waiting_started_at') or time.time()
            before = float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0)))
            total = before + max(0,time.time()-float(started))
            resource.update(waiting_started_at=started,elapsed_before_wait=before,
                elapsed_seconds=total,execution_handoff_compute_hold=True)
            if any(job.get('status') in ('lost','reconciliation') for job in unfinished):
                resource['blocked_reason'] = 'execution_reconciliation'
                run.error = 'Original caller jobs require reconciliation before admitting submitted work'
            timeout = run.config.get('timeout')
            if timeout is not None and total >= float(timeout):
                resource['hard_budget_exhausted'] = {'kind':'seconds','elapsed_seconds':total,'limit':float(timeout)}
                previous = resource.get('submission_cancel_error') or {}
                try:
                    if registry_error and previous.get('registry_error') == registry_error:
                        raise RuntimeError(previous['error'])
                    if manager is None:raise RuntimeError('Original process backend could not be inspected: '+str(registry_error))
                    manager.cancel_all()
                    resource.pop('submission_cancel_error',None)
                    resource['blocked_reason'] = 'time_budget_exhausted'
                except (OSError,ValueError,RuntimeError) as exc:
                    resource.update(blocked_reason='execution_reconciliation',submission_cancel_error={
                        'error':str(exc),'registry_error':registry_error})
            run.resource = resource
            return
        if resource.get('waiting_started_at') is not None:
            resource['elapsed_seconds'] = float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0))) + max(
                0,time.time()-float(resource['waiting_started_at']))
        resource.pop('waiting_started_at',None)
        resource.pop('elapsed_before_wait',None)
        resource.pop('execution_handoff_compute_hold',None)
        run.resource = resource
        if resource.get('hard_budget_exhausted'):
            run.resource = {**resource,'blocked_reason':'time_budget_exhausted'}
            return
        from research.execution.submissions import admit_submission
        try:
            with session.begin_nested():
                receipt = admit_submission(session,session.get(Project,run.project_id),run,pending)
        except Exception as exc:
            detail = getattr(exc,'detail',{})
            code = detail.get('code') if isinstance(detail,dict) else None
            previous = resource.get('submission_admission_error') or {}
            identity = {'action_id':pending['action_id'],'error':str(exc)}
            failures = int(previous.get('failures',0))+1 if previous.get('identity')==identity else 1
            resource['submission_admission_error'] = {'identity':identity,'failures':failures}
            if not code and failures < 2:
                run.resource = resource
                return
            receipt = {'action_id':pending['action_id'],'tool':pending['tool'],'run_ids':[],
                'handoff_status':'blocked','admission_error':detail or str(exc),'admitted_at':now()}
        resource = {**run.resource,'submission_receipts':{
            **run.resource.get('submission_receipts',{}),pending['action_id']:receipt},
            'awaited_submission_action_id':pending['action_id'],'blocked_reason':'research_submission',
            'wait_for':{'type':'research_submission','action_id':pending['action_id']}}
        resource.pop('pending_submission',None)
        resource.pop('waiting_started_at',None)
        resource.pop('elapsed_before_wait',None)
        run.resource = resource
        emit(session,run.project_id,'research_submission_admitted',{'run_id':run.id,**receipt})

    action_id = run.resource.get('awaited_submission_action_id')
    receipt = run.resource.get('submission_receipts',{}).get(action_id)
    if not receipt or run.resource.get('blocked_reason') != 'research_submission':
        return
    children = [session.get(TaskRun,ident) for ident in receipt.get('run_ids',[])]
    if any(child is None for child in children):
        run.resource = {**run.resource,'blocked_reason':'execution_reconciliation'}
        run.error = 'Submitted task is missing; reconcile the original submission receipt'
        return
    for child in children:
        if child.status not in ('completed','failed','cancelled','interrupted','skipped'):
            if child.status not in ('waiting_input','budget_exhausted') or reservation_for(child) is not None:
                return
    from research.execution.submissions import settle_tool_receipt
    result = settle_tool_receipt(session,receipt)
    run.resource = {**run.resource,'submission_receipts':{
        **run.resource['submission_receipts'],action_id:{**receipt,'settled_at':now(),
            'outcomes':result['outcomes']}},'wait_for':{},'blocked_reason':None}
    run.resource.pop('awaited_submission_action_id',None)
    run.config = {**run.config,'_next_attempt':{'mode':'continue','reason':'research_submission_settled'}}
    run.status = 'queued'
    run.pid = None
    run.process_created = None
    run.error = None
    node = session.get(Node,run.node_id) if run.node_id else None
    if node and node.extra.get('latest_run_id') == run.id:
        node.execution_status = 'queued'
    emit(session,run.project_id,'run_resumed',{'run_id':run.id,'reason':'Submitted tasks have actual settled receipts',
        'action_id':action_id,'run_ids':receipt.get('run_ids',[])})


def _invalidate_failed_run_dependents(session, project_id, node_id):
    """Invalidate graph consumers when a current run no longer has a result."""
    from research.kernel.graph import ImpactAnalyzer
    from services.api.common import graph_from_db

    project = session.get(Project, project_id)
    if project is None:
        return
    impact = ImpactAnalyzer(graph_from_db(session, project)).resolve([{
        'id': node_id,
        'category': 'semantic',
        'reason': 'Latest current run failed or was interrupted',
    }])
    rerun_nodes = set(impact['rerun_nodes'])
    refresh_nodes = set(impact['refresh_nodes'])
    for dependent_id in (rerun_nodes | refresh_nodes) - {node_id}:
        dependent = session.get(Node, dependent_id)
        if dependent is None:
            continue
        extra = {**(dependent.extra or {}), 'context_stale': True}
        if dependent.outputs:
            dependent.deliverable_status = 'needs_update'
            extra['results_current'] = False
        extra['needs_rerun'] = dependent_id in rerun_nodes
        extra['rerun_generation'] = int(extra.get('rerun_generation', 0)) + 1
        dependent.extra = extra


class WorkerLoop:
    def __init__(self):
        self.id=uid(); self.stopping=False; self.processes={}; self.log_offsets={}; self.last_heartbeat=0
        migrate()
        with Session.begin() as s: s.add(Worker(id=self.id,name=socket.gethostname(),pid=os.getpid(),capabilities={'concurrency':settings.worker_concurrency,'resources':capacity(),'runner':'local','isolation':'trusted_local_process'}))
    def reconcile_completed(self):
        # Recover node presentation from an existing completed run and its actual dependency revisions.
        with Session.begin() as s:
            for n in s.scalars(select(Node).where(Node.execution_status=='completed')):
                r=s.get(TaskRun,n.extra.get('latest_run_id')) if n.extra.get('latest_run_id') else None
                if not r or r.status!='completed' or r.node_revision!=n.revision or r.resource.get('pending_intervention'): continue
                # Startup recovery must enforce the same invalidation snapshot as completion.
                if int(n.extra.get('rerun_generation',0)) != int(r.resource.get('rerun_generation_at_enqueue',0)): continue
                dependencies=[s.get(TaskRun,ident) for ident in r.dependencies]
                if any(d is None or d.status!='completed' or (d.node_id and (s.get(Node,d.node_id) is None or s.get(Node,d.node_id).revision!=d.node_revision)) for d in dependencies): continue
                folder=safe_path(project_dir(r.project_id),r.output_path)
                if not (folder/'result.json').is_file(): continue
                if not n.extra.get('result_revision'):
                    n.extra={**n.extra,'results_current':True,'last_run_id':r.id,'result_revision':r.node_revision}
                    n.outputs=[{'kind':'run','id':r.id,'path':r.output_path+'/result.json','project_scope':True,'node_revision':r.node_revision}]
                    for filename in ('metrics.json','predictions.csv','sources.json','workspace/agent_result.json','workspace/theory_check.json'):
                        if (folder/filename).is_file(): n.outputs.append({'kind':'file','path':r.output_path+'/'+filename,'project_scope':True,'node_revision':r.node_revision})
    def shutdown(self,*args): self.stopping=True
    def tick(self):
        from services.interventions.application import kick_effects
        kick_effects()
        now_clock=time.time()
        if now_clock-self.last_heartbeat>=2:
            with Session.begin() as s: get(s,Worker,self.id).heartbeat=now()
            self.last_heartbeat=now_clock
            from services.worker.controller import advance_projects
            advance_projects()
        self.wake_waiting()
        with Session() as s: active=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('running','paused','pausing'))))]
        for r in active:
            if r['resource'].get('pending_intervention'): continue
            if r['worker_id']!=self.id:
                with Session() as s: other=s.get(Worker,r['worker_id']) if r['worker_id'] else None
                fresh=other and (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(other.heartbeat)).total_seconds()<12
                if fresh: continue
                with Session.begin() as s:
                    claim=s.scalar(select(TaskRun).where(TaskRun.id==r['id']).with_for_update())
                    if claim.worker_id!=r['worker_id']: continue
                    claim.worker_id=self.id
                r['worker_id']=self.id
            self.monitor(r)
        self.reap_cancelled()
        with Session() as s:
            active=[r for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('running','paused','pausing')))) if r.pid or r.status!='paused']
            owned=[r for r in active if r.worker_id==self.id]
        if len(owned)>=settings.worker_concurrency: return
        with Session.begin() as s:
            if engine.dialect.name=='sqlite': s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            elif engine.dialect.name=='postgresql':
                if not s.scalar(text('SELECT pg_try_advisory_xact_lock(824713)')): return
            reservations=[(x.id,request) for x in s.scalars(select(TaskRun).where(TaskRun.status.in_((*PROCESS_STATES,'queued','waiting','waiting_input','budget_exhausted')))) if (request:=reservation_for(x)) is not None]
            # Read candidate identities without row locks. Claim each run only
            # after locking its project, matching API run-control's
            # project -> run order and avoiding a PostgreSQL lock inversion.
            candidates=list(s.execute(select(
                TaskRun.id.label('id'), TaskRun.project_id.label('project_id'),
                TaskRun.priority.label('priority'), TaskRun.created_at.label('created_at'),
            ).where(TaskRun.status=='queued')))
            age_seconds=max(1,float(os.environ.get('FOREST_PRIORITY_AGING_SECONDS',300)))
            now_epoch=time.time()
            candidates.sort(key=lambda item: -(item.priority+(now_epoch-dt.datetime.fromisoformat(item.created_at).timestamp())/age_seconds))
            for candidate in candidates:
                r,p=_claim_queued_candidate(s,candidate)
                if not r or r.resource.get('pending_intervention'): continue
                branch=s.get(Branch,r.branch_id) if r.branch_id else None
                if branch and (branch.status=='materializing' or branch.extra.get('workspace_intervention')): continue
                try: request=task_request(r.config)
                except (ValueError,TypeError) as exc:
                    r.status='waiting_input'; r.error='Invalid task resource request: '+str(exc)
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status,'error':r.error}); continue
                allowed, dimension=admission(request,[reserved for ident,reserved in reservations if ident!=r.id],capacity())
                if allowed=='wait': continue
                if allowed=='impossible':
                    r.status='waiting_input'; r.error=f'Requested {dimension} exceeds worker capacity. Edit resources or configure an appropriate worker.'
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                deps=[s.get(TaskRun,d) for d in r.dependencies]
                if any(d is None or d.status in ('failed','cancelled','interrupted','skipped') for d in deps):
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':'dependency_failed'}; r.error='An execution dependency has no successful output. Retry it or rebind the input.'; emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                if any(d.status!='completed' for d in deps): continue
                # Check the exact queued inputs after prerequisite runs finish.
                from services.api.common import graph_from_db
                from research.kernel.artifacts import ArtifactResolver, resolve_branch_references
                graph=graph_from_db(s,p)
                resolver=ArtifactResolver(project_dir(r.project_id),graph); bound=[]; branch_bound=[]; missing=[]
                for reference in r.config.get('input_references',[]):
                    ref={'path':reference} if isinstance(reference,str) else dict(reference)
                    if not ref.get('path') and not ref.get('node_id'): continue
                    actual=resolver.resolve(ref,r.branch_id)
                    if not actual.get('available') and ref.get('path') and ref.get('node_id'):
                        producer=next((d for d in deps if d.node_id==ref['node_id']),None)
                        if producer:
                            candidate=safe_path(project_dir(r.project_id),producer.output_path+'/workspace/'+ref['path'])
                            if candidate.is_file(): actual={'available':True,'path':str(candidate),'relative_path':str(candidate.relative_to(project_dir(r.project_id)))}
                    if not actual.get('available'): missing.append(ref)
                    elif actual.get('path'): bound.append({'source_path':actual['relative_path'],
                        'destination':ref.get('destination') or Path(ref['path']).name,
                        'source_node_id':ref.get('node_id'), 'reference_path':ref.get('path')})
                reference_branch_id = r.branch_id or next(
                    (item['id'] for item in graph.get('branches',[]) if item.get('is_main')),
                    None)
                if reference_branch_id:
                    branch_bound,branch_missing=resolve_branch_references(
                        project_dir(r.project_id),graph,reference_branch_id)
                else:
                    branch_bound,branch_missing=[],[]
                missing.extend(branch_missing)
                if r.config.get('execution_attempt') and 'resolved_inputs' in r.config:
                    # Continuation is the same task with the same copied input
                    # binding, even if a producer has since been edited/retried.
                    bound=r.config['resolved_inputs']
                    branch_bound=r.config.get('resolved_branch_references',[])
                    missing=[binding for binding in [*bound,*branch_bound]
                        if not safe_path(project_dir(r.project_id),binding['source_path']).is_file()]
                if missing:
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':'input_missing'}; r.error='Required input files are missing or unavailable: '+json.dumps(missing,ensure_ascii=False); emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status}); continue
                r.config={**r.config,'resolved_inputs':bound,'resolved_branch_references':branch_bound,
                    '_workspace_source_snapshot':r.config.get('_workspace_source_snapshot',branch.workspace if branch else '.')}
                from services.api.verification import dispatch_verification
                gate=dispatch_verification(s,r,resolved_inputs=bound)
                if not gate['ready']:
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':gate['blocked_reason'],
                        'verification_gate':gate}; r.error=gate['message']
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status,'error':r.error,
                        'blocked_reason':gate['blocked_reason']}); continue
                r.resource={**r.resource,'verification_gate':gate}
                b=s.get(Branch,r.branch_id) if r.branch_id else None
                if b and b.status in ('pruned','archived','disabled'): r.status='waiting_input'; r.error='Branch is paused/pruned/archived'; continue
                if p.archived: r.status='waiting_input'; r.error='Project archived'; continue
                if r.config.get('breakpoint') and not r.config.get('breakpoint_passed'):
                    r.status='waiting_input'; r.error='Paused at configured before-node breakpoint'; r.config={**r.config,'breakpoint_passed':True}; continue
                reserved, held_by_other = reserve_project_time(s,p,r)
                if not reserved:
                    if held_by_other > 0:
                        # Another active run currently owns the remaining
                        # project allowance. Retry this queued run after that
                        # reservation is released or reduced.
                        continue
                    r.status='waiting_input'; r.resource={**r.resource,'blocked_reason':'time_budget_exhausted'}
                    r.error='Project time budget exhausted before this run could start'
                    emit(s,r.project_id,'run_changed',{'run_id':r.id,'status':r.status,'error':r.error,
                        'blocked_reason':'time_budget_exhausted'})
                    continue
                output=safe_path(project_dir(r.project_id),r.output_path); output.mkdir(parents=True,exist_ok=True)
                old_attempt=r.config.get('execution_attempt',{})
                attempt={'id':uid(),'number':int(old_attempt.get('number',0))+1,'mode':r.config.get('_next_attempt',{}).get('mode','initial' if not old_attempt else 'continue'), 'started_at':now()}
                attempt.update(r.config.get('_next_attempt',{}))
                if (output/'result.json').exists():
                    archive=output/'attempts'/str(old_attempt.get('number',0)); archive.mkdir(parents=True,exist_ok=True)
                    (output/'result.json').replace(archive/'result.json')
                resume_live_process=bool((r.resource or {}).get('live_process_pending_dispatch'))
                r.config={**r.config,'execution_attempt':attempt}; r.config.pop('_next_attempt',None)
                resource={**r.resource,'elapsed_before_attempt':r.resource.get('elapsed_seconds',0)}
                resource.pop('container_reconnect_pending_dispatch',None)
                resource.pop('live_process_pending_dispatch',None)
                resource.pop('last_observed_live_attempt_seconds',None)
                r.resource=resource
                if resume_live_process:
                    resume_live_process_for_dispatch(r.config,output/'workspace',{'live_process_pending_dispatch':True})
                log=(output/'stdout.txt').open('ab',buffering=0)
                env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONPATH':str(ROOT),'PATH':os.environ.get('PATH','')+':/opt/homebrew/bin','FOREST_RUN_ID':r.id,'FOREST_ATTEMPT_ID':attempt['id'],
                     'FOREST_EXECUTOR_TRANSACTION_FENCE':str(output/'.executor-transactions.lock')}
                recovery=r.config.get('recovery') or {}
                if recovery.get('checkpoint'): env['FOREST_CHECKPOINT_PATH']=str(safe_path(output/'workspace',recovery['checkpoint']))
                if attempt.get('checkpoint_path'): env['FOREST_RESUME_PATH']=attempt['checkpoint_path']
                proc=subprocess.Popen([sys.executable,'-m','services.worker.execute','--run-id',r.id],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env); log.close()
                self.processes[r.id]=proc; r.pid=proc.pid; r.process_created=psutil.Process(proc.pid).create_time(); r.status='running'; r.worker_id=self.id; r.started_at=now(); r.error=None
                n=s.get(Node,r.node_id) if r.node_id else None
                if n and n.revision==r.node_revision and n.extra.get('latest_run_id')==r.id: n.execution_status='running'
                emit(s,r.project_id,'run_started',{'run_id':r.id,'node_id':r.node_id,'pid':proc.pid,'attempt':attempt}); break
    def wake_waiting(self):
        """Waiting yields release executor slots; real process completion wakes work."""
        with Session() as s:
            reconciliations=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status=='waiting_input'))
                if (r.resource or {}).get('execution_reconciliation_compute_hold')]
        for value in reconciliations:
            if value.get('pid') and process_matches(value['pid'],value.get('process_created')):continue
            with Session.begin() as s:
                live=_lock_waiting_snapshot(s,value)
                if not live or live.resource.get('pending_intervention'):continue
                _reconcile_execution_hold(live,safe_path(project_dir(live.project_id),live.output_path))
                if live.status!=value['status']:
                    node=s.get(Node,live.node_id) if live.node_id else None
                    if node and node.extra.get('latest_run_id')==live.id:node.execution_status=live.status
                    emit(s,live.project_id,'run_changed',{'run_id':live.id,'status':live.status,'error':live.error})
        with Session() as s:
            submissions=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(
                ('waiting_input','queued','interrupted','failed'))))
                if (r.resource or {}).get('pending_submission') or
                    (r.status=='waiting_input' and (r.resource or {}).get('awaited_submission_action_id'))]
        for value in submissions:
            if ((value.get('resource') or {}).get('hard_budget_exhausted') and
                    not value['resource'].get('execution_handoff_compute_hold')):
                continue
            if value.get('pid') and process_matches(value['pid'],value.get('process_created')):
                continue
            with Session.begin() as s:
                live=_lock_waiting_snapshot(s,value)
                if live:
                    live.status='waiting_input'; live.pid=None; live.process_created=None
                    _advance_research_submission(s,live,safe_path(project_dir(live.project_id),live.output_path))
        with Session() as s:
            committed=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status=='waiting_input'))
                if (r.resource or {}).get('graph_recovery_handoff') and
                r.resource.get('graph_recovery_checkpoint_synced')!=r.resource['graph_recovery_handoff'].get('checkpoint_state')]
        for value in committed:
            with Session.begin() as s:
                live=_lock_waiting_snapshot(s,value)
                if live: _sync_committed_graph_handoff(live,safe_path(project_dir(live.project_id),live.output_path))
        with Session() as s:
            handoffs=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(
                ('waiting_input','queued','interrupted','failed'))))
                if (r.resource or {}).get('pending_graph_recovery')]
        for value in handoffs:
            if value['resource'].get('hard_budget_exhausted') and not value['resource'].get('graph_recovery_compute_hold'):
                continue
            if value.get('pid') and process_matches(value['pid'],value.get('process_created')):
                continue
            with Session.begin() as s:
                live=_lock_waiting_snapshot(s,value)
                if live:
                    live.status='waiting_input'; live.pid=None; live.process_created=None
                    _admit_graph_recovery_handoff(s,live,safe_path(project_dir(live.project_id),live.output_path))
        with Session() as s:
            waiting=[asdict(r) for r in s.scalars(select(TaskRun).where(TaskRun.status.in_(('waiting','budget_exhausted'))))]
        for value in waiting:
            folder=safe_path(project_dir(value['project_id']),value['output_path'])
            # A yielded receipt can be visible before its writer exits. Never
            # run a second executor against that same checkpoint concurrently.
            if value.get('pid') and process_matches(value['pid'],value.get('process_created')):
                continue
            managed=process_manager(folder/'workspace',value['config']).all()
            child_active=any(item['status'] not in ('completed','failed','cancelled','lost') for item in managed)
            legacy_step_stop=_legacy_step_continuation(value,folder)
            if legacy_step_stop:
                with Session.begin() as s:
                    live=_lock_waiting_snapshot(s,value)
                    if live:
                        freeze_budget_exhausted_elapsed(live)
                        live.status='queued'; live.pid=None; live.process_created=None; live.error=None
                        live.config={**live.config,'_next_attempt':{'mode':'continue','reason':'legacy_step_boundary'}}
                        live.resource={**live.resource,'step_stop_migrated':True}
                        node=s.get(Node,live.node_id) if live.node_id else None
                        if node and node.extra.get('latest_run_id')==live.id: node.execution_status='queued'
                        emit(s,live.project_id,'run_resumed',{'run_id':live.id,'reason':'Legacy step boundary converted to checkpoint continuation'})
                continue
            if value['status']=='budget_exhausted' and not child_active:
                with Session.begin() as s:
                    live=_lock_waiting_snapshot(s,value)
                    if live:
                        freeze_budget_exhausted_elapsed(live)
                continue
            with Session.begin() as s:
                live=_lock_waiting_snapshot(s,value)
                if not live: continue
                resource=live.resource or {}
                waiting_seconds=max(0,time.time()-float(resource.get('waiting_started_at') or time.time()))
                if (resource.get('wait_for') or {}).get('type')=='work_round': waiting_seconds=0
                total=float(resource.get('elapsed_before_wait',resource.get('elapsed_seconds',0)))+waiting_seconds
                timeout=(live.config or {}).get('timeout')
                live.resource={**resource,'elapsed_seconds':total,
                    'rss_bytes':sum(int(item.get('rss_bytes') or 0) for item in managed)}
            if timeout is not None and total>float(timeout):
                folder=safe_path(project_dir(value['project_id']),value['output_path'])
                process_manager(folder/'workspace',value['config']).cancel_all()
                if value['config'].get('execution_backend')=='container': cancel_container(value['config'],folder)
                if value['config'].get('remote'):
                    from runners.remote import cancel_remote
                    cancel_remote(value['config'],folder)
                with Session.begin() as s:
                    live=_lock_waiting_snapshot(s,value)
                    if live:
                        freeze_budget_exhausted_elapsed(live)
                        live.status='budget_exhausted'; live.error='Configured task time budget exhausted while waiting for a process'
                        interrupt_run_reservations(live.id,session=s)
                        node=s.get(Node,live.node_id) if live.node_id else None
                        if node and node.extra.get('latest_run_id')==live.id: node.execution_status=live.status
                        emit(s,live.project_id,'run_changed',{'run_id':live.id,'status':live.status,'error':live.error})
                continue
            if value['status']=='budget_exhausted': continue
            state=value['resource'].get('wait_for') or {}
            due=time.time() >= float(value['resource'].get('resume_after') or state.get('resume_after') or 0)
            if state.get('process_id') and state.get('workspace'):
                try:
                    process=process_manager(Path(state['workspace']),value['config']).inspect(state['process_id'])
                    if process.get('status')=='reconciliation':
                        with Session.begin() as s:
                            item=_lock_waiting_snapshot(s,value)
                            if item:
                                item.status='waiting_input'; item.error=process.get('error')
                                item.resource={**item.resource,'blocked_reason':'execution_reconciliation','reconciliation':process,
                                    'execution_reconciliation_compute_hold':True,'reconciliation_job':state['process_id']}
                                emit(s,item.project_id,'run_changed',{'run_id':item.id,'status':item.status,'error':item.error})
                        continue
                    due=process.get('status') in ('completed','failed','cancelled','lost')
                except (OSError,ValueError,KeyError,RuntimeError) as exc:
                    with Session.begin() as s:
                        item=_lock_waiting_snapshot(s,value)
                        if item:
                            item.status='waiting_input'; item.error='Cannot inspect awaited process: '+str(exc)
                            item.resource={**item.resource,'blocked_reason':'execution_reconciliation',
                                'execution_reconciliation_compute_hold':True,'reconciliation_job':state['process_id']}
                            emit(s,item.project_id,'run_changed',{'run_id':item.id,'status':item.status,'error':item.error})
                    continue
            if not due: continue
            with Session.begin() as s:
                r=_lock_waiting_snapshot(s,value)
                if r:
                    r.status='queued'; r.pid=None; r.process_created=None
                    r.config={**r.config,'_next_attempt':{'mode':'continue'}}
                    if state.get('type')=='work_round':
                        r.config={**r.config,'_recovery_failures':0}
                        r.resource={**r.resource,'checkpoint_recovery':{},'last_round':state}
                    node=s.get(Node,r.node_id) if r.node_id else None
                    if node and node.extra.get('latest_run_id')==r.id: node.execution_status='queued'
                    emit(s,r.project_id,'run_resumed',{'run_id':r.id,
                        'reason':'Starting next checkpointed work round' if state.get('type')=='work_round' else 'Awaited work is ready'})

    def reap_cancelled(self):
        """Reap exited local executor children after cancellation or row deletion.

        Cancellation and project deletion remove runs from the normal monitor
        query. Keep polling only worker-owned Popen handles whose row is missing
        or cancelled, and release both bookkeeping entries after the child exits.
        """
        if not self.processes:
            return
        identifiers = list(self.processes)
        with Session() as s:
            rows = s.execute(select(TaskRun.id, TaskRun.status).where(
                TaskRun.id.in_(identifiers))).all()
        statuses = dict(rows)
        for ident in identifiers:
            if ident in statuses and statuses[ident] != 'cancelled':
                continue
            proc = self.processes.get(ident)
            if proc is not None and proc.poll() is not None:
                self.processes.pop(ident, None)
                self.log_offsets.pop(ident, None)

    def monitor(self,value):
        if value['status']=='paused' and not value.get('pid'):
            return
        ident=value['id']; output=safe_path(project_dir(value['project_id']),value['output_path']); receipt=output/'result.json'; log=output/'stdout.txt'
        elapsed=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(value['started_at'])).total_seconds() if value.get('started_at') else 0
        before=float(value.get('resource',{}).get('elapsed_before_attempt',0))
        proc=self.processes.get(ident)
        if proc: proc.poll()
        alive=bool(value['pid'] and process_matches(value['pid'],value['process_created']))
        resource={'elapsed_seconds':before+elapsed,'cpu_percent':None,'rss_bytes':None,'cpu_measurement_scope':'host_process_tree'}
        if value['config'].get('execution_backend')=='container':
            resource.update(cpu_measurement_scope='host_executor_only',container_cpu_seconds=None)
        if alive:
            resource['last_observed_live_attempt_seconds']=elapsed
            try:
                resource.update(process_tree_resources(value['pid'],value['process_created']))
            except (psutil.NoSuchProcess,psutil.AccessDenied): pass
        if value['status']=='paused' and alive: return
        timeout=value['config'].get('timeout')
        timeout=float(timeout) if timeout is not None else None
        outcome=None
        if receipt.exists():
            try: candidate=json.loads(receipt.read_text())
            except ValueError: candidate=None
            expected=value['config'].get('execution_attempt',{}).get('id')
            if candidate and (not expected or candidate.get('attempt_id')==expected):
                if not alive: outcome=candidate
            elif candidate:
                # Never accept a receipt from a superseded attempt.
                stale=output/'attempts'/'stale'; stale.mkdir(parents=True,exist_ok=True)
                receipt.replace(stale/(str(time.time_ns())+'.json'))
        overdue_elapsed=None
        if timeout is not None:
            if outcome is not None:
                try: receipt_elapsed=max(0.0,float(outcome.get('elapsed_seconds',elapsed)))
                except (TypeError,ValueError): receipt_elapsed=elapsed
                if before+receipt_elapsed>timeout:
                    overdue_elapsed=receipt_elapsed
            elif before+elapsed>timeout:
                overdue_elapsed=elapsed
        if overdue_elapsed is not None:
            if value['config'].get('remote'):
                from runners.remote import cancel_remote
                cancel_remote(value['config'],output)
            process_manager(output/'workspace',value['config']).cancel_all()
            if value['config'].get('execution_backend')=='container': cancel_container(value['config'],output)
            if alive: stop_group(value['pid'],value['process_created'],grace=TIMEOUT_STOP_GRACE_SECONDS)
            if value.get('started_at'):
                started=dt.datetime.fromisoformat(value['started_at'])
                if started.tzinfo is None: started=started.replace(tzinfo=dt.timezone.utc)
                overdue_elapsed=max(overdue_elapsed,(dt.datetime.now(dt.timezone.utc)-started).total_seconds())
            elapsed=max(elapsed,overdue_elapsed)
            outcome={'status':'failed','exit_code':124,'error':f'Task exceeded its {timeout:g}s budget',
                     'budget_exhausted':True,'elapsed_seconds':elapsed}
        elif outcome is None and not alive:
            outcome={'status':'interrupted','exit_code':proc.returncode if proc else None,'error':'Task process ended without a current completion receipt.'}
        with Session.begin() as s:
            _lock_project(s,value['project_id'])
            r=s.scalar(select(TaskRun).where(TaskRun.id==ident).with_for_update())
            if not r or r.status in ('cancelled','completed','failed','interrupted','waiting','waiting_input','budget_exhausted') or r.resource.get('pending_intervention'): return
            if r.worker_id!=self.id: return
            if r.config.get('execution_attempt',{}).get('id')!=value['config'].get('execution_attempt',{}).get('id'): return
            r.resource={**r.resource,**resource}
            if log.exists() and self.log_offsets.get(ident)!=log.stat().st_size:
                self.log_offsets[ident]=log.stat().st_size; emit(s,r.project_id,'run_progress',{'run_id':r.id,'output_offset':log.stat().st_size})
            if outcome:
                if outcome.get('budget_exhausted') and (r.resource.get('pending_submission') or r.resource.get('pending_graph_recovery')):
                    r.resource={**r.resource,'hard_budget_exhausted':{'kind':'seconds',
                        'elapsed_seconds':before+float(outcome.get('elapsed_seconds',elapsed)),
                        'limit':timeout},'blocked_reason':'time_budget_exhausted'}
                if outcome['status'] in ('failed','interrupted','cancelled','budget_exhausted'):
                    # Close the failed attempt's accounting before recovery can
                    # queue another executor for the same run ID.
                    interrupt_run_reservations(r.id,session=s)
                attempt={**r.config.get('execution_attempt',{}),'status':outcome['status'],'exit_code':outcome.get('exit_code'),'error':outcome.get('error'),'finished_at':now(),'elapsed_seconds':outcome.get('elapsed_seconds',elapsed)}
                attempt['elapsed_seconds']=max(float(attempt['elapsed_seconds']),
                    float(r.resource.get('last_observed_live_attempt_seconds',0)))
                attempts=[*r.resource.get('attempts',[]),attempt]
                r.resource={**r.resource,'attempts':attempts,'elapsed_seconds':before+attempt['elapsed_seconds']}
                decision=None
                container_reconnect_pending=False
                committed_graph_handoff=bool(r.resource.get('pending_graph_recovery') and
                    outcome['status'] in ('interrupted','failed') and not outcome.get('budget_exhausted'))
                committed_submission=bool(r.resource.get('pending_submission') and
                    outcome['status'] in ('waiting','waiting_input','interrupted','failed') and not outcome.get('budget_exhausted'))
                if outcome['status'] in ('interrupted','failed') and not outcome.get('budget_exhausted') and not committed_graph_handoff and not committed_submission:
                    recovery_config=_checkpoint_recovery_config(r,output)
                    decision=recovery_decision(recovery_config,output,kind=r.kind,failed=outcome['status']=='failed')
                    if r.config.get('execution_backend')=='container' and r.kind in ('command','experiment') and (not decision or decision.get('mode')!='checkpoint'):
                        decision=None  # Recreate work only from an explicitly configured, present task checkpoint.
                    if r.config.get('execution_backend')=='container' and outcome['status']=='interrupted' and (output/'container_task.json').exists():
                        state=pause_container_for_reconnect(r.config,output)
                        container_reconnect_pending=state['status']=='paused'
                        if state['status'] in ('running','paused','starting','completed') or (state['status']=='failed' and not decision):
                            decision={'mode':'container_reconnect'}
                if decision:
                    r.config={**r.config,'_next_attempt':decision,'_recovery_failures':int(recovery_config.get('_recovery_failures',0))+1}
                    if decision.get('mode')=='container_reconnect' and container_reconnect_pending:
                        r.resource={**r.resource,'container_reconnect_pending_dispatch':True}
                    r.status='paused' if value['status']=='paused' else 'queued'; r.pid=None; r.process_created=None; r.error=outcome.get('error')
                    emit(s,r.project_id,'run_recovery_queued',{'run_id':r.id,'mode':decision['mode'],'attempt':attempt})
                elif outcome['status'] in ('waiting','waiting_input','budget_exhausted') or committed_graph_handoff or committed_submission:
                    r.status='waiting_input' if committed_graph_handoff or committed_submission else outcome['status']
                    r.pid=None; r.process_created=None; r.error=outcome.get('error')
                    r.resource=_waiting_outcome_resource(r.resource,{**outcome,'status':r.status})
                    _admit_graph_recovery_handoff(s,r,output)
                    _advance_research_submission(s,r,output)
                    emit(s,r.project_id,'run_waiting',{'run_id':r.id,'status':r.status,'wait_for':outcome.get('wait_for',{})})
                else:
                    r.status=outcome['status']; r.exit_code=outcome.get('exit_code'); r.error=outcome.get('error'); r.metrics=outcome.get('metrics',{}); r.finished_at=now()
                    from services.api.verification import verification_for_run
                    verification=verification_for_run(s,r)
                    r.resource={**r.resource,'verification_status':verification['verification_status'],
                        'verification':verification}
                    if r.kind=='verification':
                        producer_id=verification.get('producer_node_id')
                        producer=s.get(Node,producer_id) if producer_id else None
                        source=s.get(TaskRun,verification.get('producer_run_id')) if verification.get('producer_run_id') else None
                        if producer and source:
                            current=verification_for_run(s,source)
                            producer.extra={**producer.extra,'verification_status':current['verification_status'],
                                'latest_verification_run_id':r.id}
                    emit(s,r.project_id,'run_completed' if r.status=='completed' else 'run_failed',{'run_id':r.id,'status':r.status,'error':r.error})
                n=s.get(Node,r.node_id) if r.node_id else None
                if n and n.revision==r.node_revision and n.extra.get('latest_run_id')==r.id:
                    n.execution_status=r.status
                    if r.status in ('completed','failed','interrupted'):
                        invalidation_changed_after_enqueue = (
                            int(n.extra.get('rerun_generation', 0))
                            != int(r.resource.get('rerun_generation_at_enqueue', 0))
                        )
                        if invalidation_changed_after_enqueue:
                            # A later upstream edit invalidated this node while it ran.
                            # Keep accepted outputs and the appropriate rerun/refresh
                            # marker visible to the affected-scope scheduler.
                            n.deliverable_status='needs_update'
                            n.extra={**n.extra,'results_current':False}
                        else:
                            n.deliverable_status='ready_for_review' if r.status=='completed' else 'draft'
                            current_result={**n.extra,'results_current':r.status=='completed','last_run_id':r.id,'result_revision':r.node_revision,
                                'verification_status':r.resource.get('verification_status','unverified')}
                            current_result['needs_rerun'] = r.status != 'completed'
                            n.extra=current_result
                            if r.kind=='experiment' and r.status=='completed':
                                recovery=r.metrics.get('mechanism_recovery',[])
                                n.research_status='supported' if recovery and all(x['supported_descriptively'] for x in recovery) else 'not_supported' if recovery else 'unevaluated'
                            n.outputs=[{'kind':'run','id':r.id,'path':r.output_path+'/result.json','project_scope':True,'node_revision':r.node_revision}]
                            for filename in ('metrics.json','predictions.csv','sources.json','verification.json','workspace/agent_result.json','workspace/theory_check.json'):
                                if (output/filename).is_file(): n.outputs.append({'kind':'file','path':r.output_path+'/'+filename,'project_scope':True,'node_revision':r.node_revision})
                            if r.status != 'completed':
                                _invalidate_failed_run_dependents(s, r.project_id, n.id)
                self.processes.pop(ident,None)
    def run(self):
        self.reconcile_completed()
        signal.signal(signal.SIGTERM,self.shutdown); signal.signal(signal.SIGINT,self.shutdown)
        print(f'FOREST worker {self.id} ready (independent process; {settings.worker_concurrency} resource-aware slots)',flush=True)
        while not self.stopping:
            try: self.tick()
            except Exception:
                import traceback; traceback.print_exc()
            time.sleep(WORKER_POLL_INTERVAL_SECONDS)
        with Session.begin() as s:
            worker=get(s,Worker,self.id); worker.heartbeat='2000-01-01T00:00:00+00:00'
        print('Worker stopped; running child jobs remain recoverable by the next worker.',flush=True)
if __name__=='__main__': WorkerLoop().run()
