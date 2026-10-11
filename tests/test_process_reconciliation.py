"""Observed process identity and original receipts are required for recovery."""
import json
import os
from pathlib import Path
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import threading

from research.agents.processes import ManagedProcesses, configuration_hash, is_alive, atomic_json, request_sources, workspace_inventory
from test_agent_processes import completed


def test_completion_binds_configuration_and_saved_original_artifacts(tmp_path):
    manager=ManagedProcesses(tmp_path)
    (tmp_path/'fit.py').write_text("from pathlib import Path\nPath('metrics.json').write_text('{\"observed\": 1}')\n")
    state=manager.start([sys.executable,'fit.py'],process_id='fit-once')
    result=completed(manager,state['process_id'])
    assert result['status']=='completed'
    folder=manager.folder('fit-once')
    request=json.loads((folder/'request.json').read_text())
    assert request['source_hashes']['fit.py']
    assert result['configuration_hash']==request['configuration_hash']==configuration_hash(request)
    artifact=next(item for item in result['artifacts'] if item.get('relative_path')=='metrics.json')
    assert Path(artifact['path']).read_text()=='{"observed": 1}'
    # A new method can edit its workspace; the original evidence is preserved.
    (tmp_path/'metrics.json').write_text('{"new_working_result": 2}')
    assert manager.inspect('fit-once')['status']=='completed'
    Path(artifact['path']).write_text('{"corrupted": 3}')
    assert manager.inspect('fit-once')['status']=='reconciliation'


def test_supervisor_crash_reconciles_and_never_relaunches_unknown_fit(tmp_path):
    manager=ManagedProcesses(tmp_path)
    command=[sys.executable,'-c',"from pathlib import Path; import time; time.sleep(.5); p=Path('fits'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]
    state=manager.start(command,process_id='crashed-supervisor')
    assert is_alive(state.get('host_pid'),state.get('host_created'))
    os.kill(state['host_pid'],signal.SIGKILL)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        observed=manager.inspect('crashed-supervisor')
        if observed['status']=='reconciliation': break
        time.sleep(.05)
    assert observed['status']=='reconciliation'
    assert (tmp_path/'fits').read_text()=='1'
    assert manager.start(command,process_id='crashed-supervisor')['status']=='reconciliation'
    time.sleep(.6)
    assert (tmp_path/'fits').read_text()=='1'


def test_committed_launch_without_pid_is_uncertain_and_cannot_spawn(tmp_path):
    manager=ManagedProcesses(tmp_path)
    folder=manager.folder('commit-crash')
    folder.mkdir()
    request={'process_id':'commit-crash','command':[sys.executable,'-c',"from pathlib import Path; Path('duplicate').write_text('bad')"],
        'cwd':str(tmp_path),'env':{},'timeout':None,'workspace':str(tmp_path),
        'source_hashes':{},'created_at':time.time()-10}
    request['configuration_hash']=configuration_hash(request)
    atomic_json(folder/'request.json',request)
    atomic_json(folder/'state.json',{'status':'starting','launch_committed':True,
        'launch_id':'durable-before-popen','configuration_hash':request['configuration_hash']})
    assert manager.start(request['command'],process_id='commit-crash')['status']=='reconciliation'
    assert not (tmp_path/'duplicate').exists()
    assert not (folder/'supervisor.log').exists()


def test_terminal_state_without_bound_receipt_is_not_success(tmp_path):
    manager=ManagedProcesses(tmp_path)
    state=manager.start(['/bin/echo','actual execution'],process_id='receipt-loss')
    assert completed(manager,state['process_id'])['status']=='completed'
    (manager.folder('receipt-loss')/'completion.json').unlink()
    assert manager.inspect('receipt-loss')['status']=='reconciliation'
    assert manager.start(['/bin/echo','actual execution'],process_id='receipt-loss')['status']=='reconciliation'


def test_concurrent_same_job_launch_is_idempotent(tmp_path):
    barrier=threading.Barrier(2)
    command=[sys.executable,'-c',"from pathlib import Path; import time; time.sleep(.2); p=Path('launches'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
    def launch():
        manager=ManagedProcesses(tmp_path)
        barrier.wait()
        return manager.start(command,process_id='shared-action')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:launch(),range(2)))
    assert {result['process_id'] for result in results}=={'shared-action'}
    assert completed(ManagedProcesses(tmp_path),'shared-action')['status']=='completed'
    assert (tmp_path/'launches').read_text()=='x'


def test_bubblewrap_cannot_write_or_forge_supervisor_records(tmp_path):
    from runners.bubblewrap import BubblewrapProcesses
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    manager=BubblewrapProcesses(workspace)
    assert not manager.directory.is_relative_to(workspace)
    target=manager.directory/'isolated'/'state.json'
    code="from pathlib import Path; import json; p=Path("+repr(str(target))+"); Path('isolation.json').write_text(json.dumps({'control_visible':p.exists()}))"
    state=manager.start([sys.executable,'-c',code],process_id='isolated')
    assert completed(manager,state['process_id'])['status']=='completed'
    assert json.loads((workspace/'isolation.json').read_text())=={'control_visible':False}


def test_imported_helper_change_produces_distinct_verified_execution(tmp_path):
    manager=ManagedProcesses(tmp_path)
    (tmp_path/'train.py').write_text("from helper import value\nfrom pathlib import Path\np=Path('observations'); p.write_text(p.read_text()+str(value) if p.exists() else str(value))\n")
    helper=tmp_path/'helper.py'
    helper.write_text('value=1\n')
    command=[sys.executable,'train.py']
    first_sources=request_sources(command,tmp_path,tmp_path)
    first=manager.start(command,process_id='method-1')
    assert completed(manager,first['process_id'])['status']=='completed'
    first_stat=helper.stat()
    helper.write_text('value=2\n')
    # Keep identical timestamp/size to reproduce stale timestamp-based pyc.
    os.utime(helper,ns=(first_stat.st_atime_ns,first_stat.st_mtime_ns))
    second_sources=request_sources(command,tmp_path,tmp_path)
    assert first_sources['helper.py']!=second_sources['helper.py']
    assert first_sources['train.py']==second_sources['train.py']
    second=manager.start(command,process_id='method-2')
    completed_second=completed(manager,second['process_id'])
    assert completed_second['status']=='completed'
    assert completed_second['configuration_hash']!=manager.inspect('method-1')['configuration_hash']
    assert (tmp_path/'observations').read_text()=='12'


def test_script_outside_cwd_binds_imports_and_verifies_changed_method(tmp_path):
    manager=ManagedProcesses(tmp_path)
    scratch=tmp_path/'scratch'; scratch.mkdir()
    (tmp_path/'train.py').write_text("from helper import value\nfrom pathlib import Path\np=Path('observations'); p.write_text(p.read_text()+str(value) if p.exists() else str(value))\n")
    helper=tmp_path/'helper.py'; helper.write_text('value=1\n')
    command=[sys.executable,'../train.py']
    first_sources=request_sources(command,tmp_path,scratch)
    assert 'helper.py' in first_sources
    first=manager.start(command,process_id='parent-method-1',cwd='scratch')
    assert completed(manager,first['process_id'])['status']=='completed'
    previous=helper.stat(); helper.write_text('value=2\n')
    os.utime(helper,ns=(previous.st_atime_ns,previous.st_mtime_ns))
    second_sources=request_sources(command,tmp_path,scratch)
    assert first_sources['helper.py']!=second_sources['helper.py']
    second=manager.start(command,process_id='parent-method-2',cwd='scratch')
    assert completed(manager,second['process_id'])['status']=='completed'
    assert (scratch/'observations').read_text()=='12'


def test_child_script_binds_root_modules_loaded_through_pythonpath(tmp_path):
    from research.agents.runtime import ToolRuntime
    experiments=tmp_path/'experiments'; experiments.mkdir()
    (experiments/'train.py').write_text("from models import value\nfrom pathlib import Path\np=Path('observations'); p.write_text(p.read_text()+str(value) if p.exists() else str(value))\n")
    models=tmp_path/'models.py'; models.write_text('value=1\n')
    arguments={'command':[sys.executable,'train.py'],'cwd':'experiments',
        'env':{'PYTHONPATH':str(tmp_path)}}
    tool=ToolRuntime('authorized-tree-observation',tmp_path,config={'execution_backend':'local'})
    first=tool.dispatch('start_process',arguments,None,'root-module-first')
    assert completed(tool.processes,first['process_id'])['status']=='completed'
    saved=models.stat(); models.write_text('value=2\n')
    os.utime(models,ns=(saved.st_atime_ns,saved.st_mtime_ns))
    # This fresh tool context must discover a source change outside both the
    # command cwd and the executed script's directory, without an override.
    resumed=ToolRuntime('authorized-tree-observation',tmp_path,config={'execution_backend':'local'})
    second=resumed.dispatch('start_process',arguments,None,'root-module-second')
    assert first['process_id']!=second['process_id']
    assert completed(resumed.processes,second['process_id'])['status']=='completed'
    assert (experiments/'observations').read_text()=='12'
    assert 'models.py' in json.loads((resumed.processes.folder(second['process_id'])/'request.json').read_text())['source_hashes']


def test_overwritten_output_with_restored_timestamp_preserves_original_receipt(tmp_path):
    manager=ManagedProcesses(tmp_path)
    output=tmp_path/'metrics.json'; output.write_text('{"value":0}')
    code="from pathlib import Path\nimport os\np=Path('metrics.json'); old=p.stat(); p.write_text('{\"value\":1}'); os.utime(p, ns=(old.st_atime_ns,old.st_mtime_ns))\n"
    (tmp_path/'train.py').write_text(code)
    result=completed(manager,manager.start([sys.executable,'train.py'],process_id='preserved-timestamp')['process_id'])
    assert result['status']=='completed'
    original=next(item for item in result['artifacts'] if item.get('relative_path')=='metrics.json')
    assert Path(original['path']).read_text()=='{"value":1}'


def test_comment_only_method_reexecution_is_not_new_scientific_progress(tmp_path):
    from research.agents.lifecycle import artifact_manifest,progress_facts,digest
    manager=ManagedProcesses(tmp_path)
    train=tmp_path/'train.py'
    code="from pathlib import Path\nPath('metrics.json').write_text('{\"value\":1}')\n"
    train.write_text(code)
    first=completed(manager,manager.start([sys.executable,'train.py'],process_id='same-method-1')['process_id'])
    context={'evidence_fingerprint':'original-evidence','nodes':[]}
    state={'transcript':[],'execution_snapshot':{'artifact_hashes':artifact_manifest({},tmp_path,['train.py','metrics.json'])}}
    before=digest(progress_facts(state,['train.py','metrics.json'],[first],context))
    train.write_text('# Same scientific method with a new comment.\n'+code)
    second=completed(manager,manager.start([sys.executable,'train.py'],process_id='same-method-2')['process_id'])
    state['execution_snapshot']['artifact_hashes']=artifact_manifest({},tmp_path,['train.py','metrics.json'])
    after=digest(progress_facts(state,['train.py','metrics.json'],[first,second],context))
    assert before==after,'A comment and an identical successful result extended the no-progress detector'


def test_concurrent_atomic_checkpoint_publish_does_not_crash_receipt_capture(tmp_path):
    workspace=tmp_path/'workspace'; workspace.mkdir()
    manager=ManagedProcesses(workspace,directory=tmp_path/'processes')
    # Reproduce the supervisor seeing a save_session temporary pathname while
    # a different process owns its atomic publication. These bytes are state,
    # never raw experimental outputs.
    temporary=workspace/'agent_transcript.json.temporary-intent.tmp'
    temporary.write_text('checkpoint pending rename')
    assert temporary.name not in workspace_inventory(workspace)
    temporary.unlink()
    stop=threading.Event()
    def publisher():
        while not stop.is_set():
            atomic_json(workspace/'agent_transcript.json',{'state':'x'*500000})
    thread=threading.Thread(target=publisher)
    thread.start()
    try:
        for index in range(4):
            command=[sys.executable,'-c',"from pathlib import Path; import time; time.sleep(.1); Path('metrics.json').write_text('{\"value\":1}')"]
            result=completed(manager,manager.start(command,process_id='checkpoint-publisher-'+str(index))['process_id'])
            assert result['status']=='completed'
            assert not result.get('artifact_errors')
            assert any(item.get('relative_path')=='metrics.json' for item in result['artifacts'])
            assert not any('agent_transcript.json' in item.get('relative_path','') for item in result['artifacts'])
    finally:
        stop.set(); thread.join(timeout=5)
