import json

from research.agents.step_budget import expand_if_earned, initialize


def turn(step, tool, result):
    return {'step': step, 'executed_action': {'tool': tool}, 'tool_result': result}


def test_default_step_policy_starts_at_30_and_grows_only_for_verified_progress():
    state = {'transcript': [turn(i, 'read_file', {'exit_code': 0}) for i in range(1, 31)]}
    policy = initialize(state, {})
    assert policy['limit'] == 30
    assert not expand_if_earned(state)
    state['transcript'][-1] = turn(30, 'write_file', {'exit_code': 0})
    assert expand_if_earned(state)
    assert state['dynamic_step_budget']['limit'] == 60
    recovered = json.loads(json.dumps(state))
    assert recovered['dynamic_step_budget'] == state['dynamic_step_budget']


def test_policy_is_configurable_capped_and_no_further_growth_without_new_progress():
    state = {'transcript': []}
    policy = initialize(state, {'step_budget_policy': {'initial_steps': 2, 'increase_by': 3, 'max_steps': 5}})
    assert policy['limit'] == 2
    state['transcript'] = [turn(1, 'experiment_run', {'status': 'queued', 'run_id': 'queued-only'}), turn(2, 'read_file', {'exit_code': 0})]
    assert not expand_if_earned(state)
    state['transcript'][0] = turn(1, 'experiment_run', {'status': 'running', 'run_id': 'active-run'})
    assert expand_if_earned(state)
    assert state['dynamic_step_budget']['limit'] == 5
    state['transcript'].extend([turn(3, 'read_file', {'exit_code': 0}), turn(4, 'read_file', {'exit_code': 0}), turn(5, 'read_file', {'exit_code': 0})])
    assert not expand_if_earned(state)


def test_explicit_legacy_budget_remains_a_hard_outer_limit():
    state = {'transcript': []}
    assert initialize(state, {'agent_budget': {'steps': 4}}) is None
    assert 'dynamic_step_budget' not in state
