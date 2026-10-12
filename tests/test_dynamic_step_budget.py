import json
from research.agents.lifecycle import initialize, round_due, checkpoint_round, resume_round
from research.agents.runtime import budget_reason


def test_legacy_caps_migrate_to_round_hints_without_lifetime_limits():
    for cap in (10, 30, 150, 400):
        state = {'transcript': [], 'totals': {'input_tokens': 0, 'cost': None}}
        life = initialize(state, {'agent_budget': {'steps': cap}})
        assert life['round_steps'] == cap
        for index in range(1, 1201):
            state['transcript'].append({'step': index})
            if round_due(state):
                wait = checkpoint_round(state, reason='work cadence')
                assert wait['type'] == 'work_round'
                assert resume_round(state)
            assert budget_reason(state, {'agent_budget': {'steps': cap}}) is None
        assert life['round_index'] > 1
        assert 'max_steps' not in life


def test_configurable_round_boundary_preserves_pending_action_and_same_session():
    state = {'run_id': 'same-run', 'transcript': []}
    initialize(state, {'execution_round': {'steps': 2}})
    state['transcript'] = [{'step': 1}, {'step': 2}]
    state['pending_action'] = {'id': 'durable-action'}
    assert not round_due(state)
    state['pending_action'] = None
    assert round_due(state)
    checkpoint_round(state, reason='checkpoint')
    recovered = json.loads(json.dumps(state))
    assert resume_round(recovered)
    assert recovered['run_id'] == 'same-run'
    assert recovered['lifecycle']['round_index'] == 2
    assert not resume_round(recovered)


def test_old_dynamic_allowance_is_discarded_as_accounting_policy():
    state = {'transcript': [{'step': 500}], 'dynamic_step_budget': {'limit': 150, 'max_steps': 150}}
    life = initialize(state, {'step_budget_policy': {'initial_steps': 5, 'max_steps': 150}})
    assert life['round_steps'] == 5
    assert 'dynamic_step_budget' not in state
    assert 'legacy_step_budget' not in life
