"""Real command receipts and action intents across fresh runtime processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario',['repeat-command','replay-python','missing-request',
    'pending-model','invalid-ledger'])
def test_actual_job_intent_recovery(tmp_path,scenario):
    env={**os.environ,'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'jobs.db'),
        'FOREST_DATA_DIR':str(tmp_path/'data'),'PYTHONPATH':str(ROOT)}
    result=subprocess.run([sys.executable,__file__,str(tmp_path),scenario],cwd=ROOT,
        env=env,capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    import json
    from sqlalchemy import select
    from services.api.db import migrate,Session,Project,TaskRun,ToolExecution,Provider,ModelRequest,uid
    from research.agents.runtime import ToolRuntime,run_agent,AgentYield
    from research.agents.budget import make_request_guard
    from research.agents.processes import atomic_json
    from test_agent_processes import completed

    directory,scenario=Path(sys.argv[1]),sys.argv[2]
    migrate()
    with Session.begin() as session:
        project=Project(name='Observed local job recovery')
        session.add(project); session.flush()
        run=TaskRun(project_id=project.id,request_id=uid(),status='running',kind='agent')
        session.add(run); session.flush()
        run_id=run.id
    workspace=directory/'workspace'; workspace.mkdir()
    config={'execution_backend':'local'}
    runtime=ToolRuntime(run_id,workspace,config=config)
    code="from pathlib import Path\np=Path('fit-count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
    script=workspace/'train.py'; script.write_text(code)
    if scenario in {'pending-model','invalid-ledger'}:
        with Session.begin() as session:
            provider=Provider(name='No transport is requested during reconciliation',kind='codex_cli',
                base_url='http://127.0.0.1:9',model='gpt-6-luna',config={'reasoning_effort':'high'})
            session.add(provider); session.flush()
            snapshot={'id':provider.id,'kind':provider.kind,'base_url':provider.base_url,
                'model':provider.model,'config':provider.config,
                '_usage_context':{'project_id':project.id,'run_id':run_id}}
        state={'run_id':run_id,'status':'executing','messages':[{'role':'system','content':'local'},{'role':'user','content':'local'}],
            'transcript':[],'totals':{'input_tokens':0,'output_tokens':0,'cost':None},'active_seconds':0}
        if scenario=='pending-model':
            state['pending_model_request']={'baseline_receipt_ids':[]}
            reservation=make_request_guard(snapshot)({'phase':'before','api':'codex_cli',
                'model':snapshot['model'],'max_output_tokens':100})
        else:
            (directory/'agent_usage.json').write_text('{interrupted ledger')
        atomic_json(workspace/'agent_session.json',state)
        try:
            run_agent(run_id,workspace,{**config,'provider_snapshot':snapshot,'instructions':'Retain the original task.'})
        except AgentYield as exc:
            assert exc.status=='waiting_input'
        else:
            raise AssertionError('Unreconciled execution cannot make another model call')
        with Session() as session:
            current=session.get(TaskRun,run_id)
            assert current.resource['blocked_reason']=='execution_reconciliation'
            rows=list(session.scalars(select(ModelRequest).where(ModelRequest.run_id==run_id)))
            if scenario=='pending-model':
                assert len(rows)==1 and rows[0].id==reservation and rows[0].status=='reserved'
                assert current.resource['model_request_receipts']==[reservation]
            else:
                assert rows==[]
                assert (directory/'agent_usage.json').read_text()=='{interrupted ledger'
        restored=json.loads((workspace/'agent_session.json').read_text())
        assert restored['status']=='waiting_input' and restored['lifecycle']['state']=='blocked'
        assert restored['transcript']==state['transcript']
        assert restored.get('pending_model_request')==state.get('pending_model_request')
        assert not set(restored)&{'totals','active_seconds'}
        assert not (workspace/'fit-count').exists()
    elif scenario=='repeat-command':
        arguments={'command':[sys.executable,'train.py']}
        first=runtime.execute('run_command',arguments,'original-command')
        assert completed(runtime.processes,first['process_id'])['status']=='completed'
        # A distinct decision/round must reuse a verified identical experiment.
        restarted=ToolRuntime(run_id,workspace,config=config)
        second=restarted.execute('run_command',arguments,'another-round-command')
        assert second['process_id']==first['process_id'] and second['status']=='completed'
        assert (workspace/'fit-count').read_text()=='1'
    elif scenario=='replay-python':
        arguments={'path':'train.py','code':code}
        action='accepted-python'
        first=runtime.execute('python',arguments,action)
        assert completed(runtime.processes,first['process_id'])['status']=='completed'
        edited="# A subsequent scientific method must survive replay.\nvalue=2\n"
        script.write_text(edited)
        # This is the durable boundary after an actual launch/receipt, but
        # before the accepted action has published its tool result.
        with Session.begin() as session:
            row=session.get(ToolExecution,action)
            row.status='pending'; row.result={'control_acceptance':row.result.get('control_acceptance')}
        replayed=ToolRuntime(run_id,workspace,config=config).execute('python',arguments,action)
        assert replayed['process_id']==first['process_id']
        assert replayed['status']=='completed'
        assert (workspace/'fit-count').read_text()=='1'
        assert script.read_text()==edited,'Recovery rewrote editable method source before reattaching the original job'
    else:
        action='intent-with-no-request'
        arguments={'command':[sys.executable,'train.py']}
        with Session.begin() as session:
            session.add(ToolExecution(id=action,run_id=run_id,tool='run_command',arguments=arguments,
                status='pending',result={}))
        atomic_json(directory/'agent_job_bindings.json',{action:{'job_id':'job-unconfirmed',
            'configuration_hash':'unconfirmed','command':arguments['command'],
            'launch_arguments':{'cwd':'.','env':None,'timeout':None}}})
        result=runtime.execute('run_command',arguments,action)
        assert result['status']=='reconciliation'
        assert not (workspace/'fit-count').exists()
        assert not runtime.processes.folder('job-unconfirmed').exists()
    print('Verified actual '+scenario)
