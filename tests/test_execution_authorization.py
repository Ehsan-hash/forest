"""Real copies and isolated deterministic jobs retain explicit source ACLs."""
import json
from pathlib import Path
import shutil
import sys

import pytest

from research.agents.path_access import authorized_read_path
from services.worker.execute import prepare_task_workspace,local_command,isolated_entrypoint


def test_workspace_copy_skips_denied_original_and_rejects_explicit_binding(tmp_path):
    source=tmp_path/'branches'/'original';source.mkdir(parents=True)
    denied=source/'secret.csv';denied.write_text('private original')
    (source/'method.py').write_text('value=7')
    output=tmp_path/'runs'/'source';(output/'workspace').mkdir(parents=True)
    config={'denied_readonly_paths':[str(denied)]}
    prepare_task_workspace(tmp_path,source,output,config)
    assert not (output/'workspace'/'secret.csv').exists()
    assert (output/'workspace'/'method.py').read_text()=='value=7'
    config['resolved_inputs']=[{'source_path':denied.relative_to(tmp_path).as_posix(),'destination':'renamed.csv'}]
    with pytest.raises(PermissionError,match='denied origin'):
        prepare_task_workspace(tmp_path,source,output,config)


def test_updated_explicit_acl_applies_to_preexisting_task_copy(tmp_path):
    source=tmp_path/'branches'/'original';source.mkdir(parents=True)
    denied=source/'secret.csv';denied.write_text('original measurement')
    output=tmp_path/'runs'/'source';(output/'workspace').mkdir(parents=True)
    prepare_task_workspace(tmp_path,source,output,{})
    assert authorized_read_path(output/'workspace','secret.csv').read_text()=='original measurement'
    with pytest.raises(Exception) as refused:
        authorized_read_path(output/'workspace','secret.csv',{'denied_readonly_paths':[str(denied)]})
    assert refused.value.detail['code']=='PATH_ESCAPE'
    from runners.bubblewrap import BubblewrapProcesses
    with pytest.raises(PermissionError,match='copied explicitly denied origin'):
        BubblewrapProcesses(output/'workspace',{'denied_readonly_paths':[str(denied)]})


def test_branch_fork_chain_cannot_relabel_explicitly_denied_original(tmp_path,monkeypatch):
    from services.interventions import workspaces
    monkeypatch.setattr(workspaces,'project_dir',lambda ident:tmp_path)
    source=tmp_path/'branches'/'original';source.mkdir(parents=True)
    denied=source/'secret.csv';denied.write_text('original private bytes')
    (source/'method.py').write_text('value=7')
    graph={'nodes':[{'id':'origin','branch_id':'original'}],
        'branches':[{'id':'original','workspace':'branches/original','status':'active'}],'edges':[],'runs':[]}
    plan=workspaces.prepare('project',graph,{'action':'fork','node_id':'origin','branch_id':'first'})
    workspaces.materialize('effect-first',plan)
    assert (tmp_path/plan['workspace']/'secret.csv').read_text()=='original private bytes'
    graph['branches'].append({'id':'first','workspace':plan['workspace'],'status':'active'})
    graph['nodes'].append({'id':'first-origin','branch_id':'first'})
    second=workspaces.prepare('project',graph,{'action':'fork','node_id':'first-origin','branch_id':'second'},
        {'denied_readonly_paths':[str(denied)]})
    workspaces.materialize('effect-second',second)
    assert not (tmp_path/second['workspace']/'secret.csv').exists()
    assert (tmp_path/second['workspace']/'method.py').read_text()=='value=7'
    output=tmp_path/'runs'/'child';(output/'workspace').mkdir(parents=True)
    prepare_task_workspace(tmp_path,tmp_path/plan['workspace'],output,{'denied_readonly_paths':[str(denied)]})
    assert not (output/'workspace'/'secret.csv').exists()


@pytest.mark.skipif(not shutil.which('bwrap'),reason='Real bubblewrap is unavailable')
@pytest.mark.parametrize('kind',['command','entrypoint'])
def test_deterministic_execution_uses_real_isolation_and_original_receipt(tmp_path,kind):
    output=tmp_path/'runs'/'actual';workspace=output/'workspace';workspace.mkdir(parents=True)
    denied=tmp_path/'private.csv';denied.write_text('not authorized')
    permitted=tmp_path/'authorized.txt';permitted.write_text('authorized measurement')
    config={'execution_backend':'bubblewrap','readonly_paths':[str(permitted)],
        'denied_readonly_paths':[str(denied)],'parameters':{'denied':str(denied),'permitted':str(permitted)}}
    if kind=='command':
        source="import json;from pathlib import Path;Path('metrics.json').write_text(json.dumps({'private_visible':Path("+repr(str(denied))+").exists(),'measurement':Path("+repr(str(permitted))+").read_text()}))"
        result=local_command([sys.executable,'-c',source],workspace,output,config)
        assert result.returncode==0
        observed=json.loads((workspace/'metrics.json').read_text())
    else:
        (workspace/'actual_method.py').write_text('''import json\nfrom pathlib import Path\ndef run(parameters,output):\n    result={'private_visible':Path(parameters['denied']).exists(),'measurement':Path(parameters['permitted']).read_text()}\n    Path(output,'actual-observation.json').write_text(json.dumps(result))\n    return result\n''')
        observed=isolated_entrypoint('actual_method:run',workspace,output,config)
        assert json.loads((output/'actual-observation.json').read_text())==observed
    assert observed=={'private_visible':False,'measurement':'authorized measurement'}
    receipt=json.loads((output/'execution.json').read_text())
    assert receipt['backend']=='bubblewrap' and receipt['status']=='completed'
    original=json.loads(Path(receipt['original_receipt']).read_text())
    assert original['exit_code']==0 and original['configuration']['experiment']['execution_backend']=='bubblewrap'
    assert any(item.get('artifact_id') for item in original['artifacts'])


@pytest.mark.skipif(not shutil.which('bwrap'),reason='Real bubblewrap is unavailable')
def test_deterministic_retry_requires_terminal_original_and_keeps_crash_identity(tmp_path,monkeypatch):
    from runners.bubblewrap import BubblewrapProcesses
    output=tmp_path/'runs'/'actual';workspace=output/'workspace';workspace.mkdir(parents=True)
    command=[sys.executable,'-c',"from pathlib import Path;import sys;p=Path('attempt-count');n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));sys.exit(3 if n==0 else 0)"]
    config={'execution_backend':'bubblewrap','execution_attempt':{'id':'first','mode':'initial'}}
    first=local_command(command,workspace,output,config)
    assert first.returncode==3
    original_id=json.loads((output/'execution.json').read_text())['work_id']
    config.update(_recovery_failures=1,execution_attempt={'id':'restarted','mode':'restart'})
    retried=local_command(command,workspace,output,config)
    assert retried.returncode==0
    next_id=json.loads((output/'execution.json').read_text())['work_id']
    assert next_id!=original_id
    config.update(_recovery_failures=2,execution_attempt={'id':'continued','mode':'continue'})
    config['timeout']=5  # The worker may refresh the current shared allowance.
    assert local_command(command,workspace,output,config).returncode==0
    assert json.loads((output/'execution.json').read_text())['work_id']==next_id
    assert (workspace/'attempt-count').read_text()=='2'
    accepted_output=tmp_path/'runs'/'accepted';accepted_workspace=accepted_output/'workspace'
    accepted_workspace.mkdir(parents=True)
    start=BubblewrapProcesses.start
    monkeypatch.setattr(BubblewrapProcesses,'start',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('Executor exited before launch')))
    with pytest.raises(OSError,match='before launch'):
        local_command(command,accepted_workspace,accepted_output,{'execution_backend':'bubblewrap'})
    accepted_id=json.loads((accepted_output/'deterministic_jobs.json').read_text())['jobs'][0]['work_id']
    monkeypatch.setattr(BubblewrapProcesses,'start',start)
    assert local_command(command,accepted_workspace,accepted_output,{'execution_backend':'bubblewrap'}).returncode==3
    assert json.loads((accepted_output/'execution.json').read_text())['work_id']==accepted_id
    assert (accepted_workspace/'attempt-count').read_text()=='1'
