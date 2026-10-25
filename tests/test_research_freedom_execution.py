"""Actual fitted computations, editable configurations and recovery references."""
import ast
import json
from pathlib import Path
import sys
import time

from research.agents.runtime import ToolRuntime, collect_agent_metrics


def terminal(tool, ident):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = tool.processes.inspect(ident)
        if result['status'] in {'completed', 'failed', 'lost', 'reconciliation'}:
            return result
        time.sleep(.02)
    raise AssertionError('Actual computation did not finish')


def test_real_fit_config_evolution_and_restart_preserve_original_results(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    source = '''import json, sys
from pathlib import Path
import numpy as np
from sklearn.linear_model import Ridge
rng = np.random.default_rng(4721)
x = rng.normal(size=(80, 3)); y = x @ np.array([2., -1., .5]) + rng.normal(0, .05, 80)
validation = rng.normal(size=(32, 3)); target = validation @ np.array([2., -1., .5])
alpha = float(sys.argv[1]); model = Ridge(alpha=alpha).fit(x, y)
predictions = model.predict(validation)
Path('metrics.json').write_text(json.dumps({'alpha': alpha, 'mse': float(np.mean((predictions-target)**2)), 'coefficients': model.coef_.tolist(), 'fitted_samples': len(x), 'validated_samples': len(validation)}))
'''
    (workspace / 'fit.py').write_text(source)
    config = {'execution_backend': 'local', 'experiment_config': {'alpha': 0.0}, 'seed': 4721}
    first_tool = ToolRuntime('evolving-fit', workspace, config=config)
    first = first_tool.dispatch('start_process', {'command': [sys.executable, 'fit.py', '0']}, None, 'first-fit')
    first_receipt = terminal(first_tool, first['process_id'])
    assert first_receipt['status'] == 'completed'
    initial = collect_agent_metrics(workspace, {}, first_tool.processes)['observed_metrics']
    assert initial['fitted_samples'] == 80 and initial['validated_samples'] == 32
    assert initial['mse'] < .01
    original = next(a for a in first_receipt['artifacts'] if a.get('relative_path') == 'metrics.json')
    first_request = json.loads((first_tool.processes.folder(first['process_id']) / 'request.json').read_text())
    assert first_request['experiment']['experiment_config'] == {'alpha': 0.0}

    # New settings are new work, while the first result retains its own exact
    # source/configuration and original measured output after a process restart.
    new_tool = ToolRuntime('evolving-fit', workspace, config={**config, 'experiment_config': {'alpha': 100.0}})
    changed = new_tool.dispatch('start_process', {'command': [sys.executable, 'fit.py', '100']}, None, 'second-fit')
    second_receipt = terminal(new_tool, changed['process_id'])
    assert second_receipt['status'] == 'completed' and changed['process_id'] != first['process_id']
    revised = collect_agent_metrics(workspace, {}, new_tool.processes)['observed_metrics']
    assert revised['alpha'] == 100 and revised['mse'] > initial['mse']
    assert json.loads(Path(original['path']).read_text()) == initial
    assert new_tool.processes.inspect(first['process_id'])['status'] == 'completed'
    restored = ToolRuntime('evolving-fit', workspace, config=config)
    reused = restored.dispatch('start_process', {'command': [sys.executable, 'fit.py', '0']}, None, 'third-action-same-fit')
    assert reused['process_id'] == first['process_id'] and reused['status'] == 'completed'
    assert len(restored.processes.all()) == 2
    request = json.loads((new_tool.processes.folder(changed['process_id']) / 'request.json').read_text())
    assert request['configuration']['experiment']['experiment_config'] == {'alpha': 100.0}
    assert request['work_id'] == changed['process_id']
    assert not {'configuration_hash', 'sha256', 'fingerprint'} & request.keys()


def test_research_paths_do_not_import_digest_algorithms():
    root = Path(__file__).resolve().parents[1]
    analysis_integrity_modules = {
        root / 'research' / 'planning' / 'workspace_analysis.py',
        root / 'services' / 'api' / 'research_analysis.py',
    }
    for directory in ('research', 'services', 'forest_cli'):
        for path in (root / directory).rglob('*.py'):
            # These paired analysis modules use SHA-256 for context freshness,
            # saved snapshot integrity, and the emitted report checksum. Keep the
            # policy for all other research modules while allowing these checks.
            if path in analysis_integrity_modules:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(item.name != 'hashlib' for item in node.names), path
                elif isinstance(node, ast.ImportFrom):
                    assert node.module != 'hashlib', path
