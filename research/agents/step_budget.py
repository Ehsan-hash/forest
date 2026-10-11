"""Persisted per-node step allowances with evidence-gated growth."""
from __future__ import annotations


DEFAULT_POLICY = {'initial_steps': 30, 'increase_by': 30, 'max_steps': 150}
NON_PROGRESS_TOOLS = {'read_file', 'list_files', 'read_transcript', 'read_context_segment',
                      'inspect_process', 'read_process_output', 'results'}


def initialize(state, config):
    """Initialize once; explicit legacy agent_budget.steps remains a hard cap."""
    if (config.get('agent_budget') or {}).get('steps') is not None or config.get('max_steps') is not None:
        return None
    policy = config.get('step_budget_policy') or DEFAULT_POLICY
    if not isinstance(policy, dict):
        raise ValueError('step_budget_policy must be an object')
    initial = policy.get('initial_steps', DEFAULT_POLICY['initial_steps'])
    increase = policy.get('increase_by', DEFAULT_POLICY['increase_by'])
    maximum = policy.get('max_steps', DEFAULT_POLICY['max_steps'])
    window = policy.get('progress_window_steps', 10)
    if (any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (initial, increase, maximum, window))
            or initial > maximum):
        raise ValueError('step_budget_policy requires positive integer limits with initial_steps <= max_steps')
    return state.setdefault('dynamic_step_budget', {'limit': initial, 'increase_by': increase,
        'max_steps': maximum, 'last_expansion_at': 0, 'expansions': [],
        'progress_baseline_step': 0, 'progress_window_steps': window,
        'source': 'project_or_run_policy'})


def has_verified_progress(state, since_step, window=10):
    newest = len(state.get('transcript', []))
    recent_floor = max(since_step, newest - window)
    for turn in state.get('transcript', []):
        if turn.get('step', 0) <= recent_floor:
            continue
        action = turn.get('executed_action') or {}
        result = turn.get('tool_result') or {}
        name = action.get('tool')
        if name in NON_PROGRESS_TOOLS or name == 'finish':
            continue
        if name in ('write_file', 'write_file_chunk') and result.get('exit_code') == 0:
            return True
        if name in ('start_process', 'run_command', 'python', 'experiment_run', 'verification_run',
                    'theory_check', 'paper_generate', 'paper_compile', 'figure_render'):
            if result.get('status') in ('running', 'completed') and (
                    result.get('process_id') or result.get('run_id') or result.get('exit_code') == 0):
                return True
        if name == 'graph_command' and result.get('revision') is not None:
            return True
        if name == 'update_memory' and result.get('changed') is True:
            return True
    return False


def expand_if_earned(state):
    budget = state.get('dynamic_step_budget')
    if not budget or len(state.get('transcript', [])) < budget['limit']:
        return False
    if budget['limit'] >= budget['max_steps']:
        return False
    if not has_verified_progress(state, budget.get('progress_baseline_step', 0),
                                 budget.get('progress_window_steps', 10)):
        return False
    old = budget['limit']
    new = min(budget['max_steps'], old + budget['increase_by'])
    budget['limit'] = new
    budget['progress_baseline_step'] = len(state.get('transcript', []))
    budget['last_expansion_at'] = len(state.get('transcript', []))
    budget.setdefault('expansions', []).append({'from': old, 'to': new,
        'after_step': len(state.get('transcript', [])), 'basis': 'verified_non_read_tool_result'})
    return True
