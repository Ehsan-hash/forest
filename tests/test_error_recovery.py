import json

from research.agents.error_recovery import classify_failure, diagnosis_prompt, observe_tool_result


def test_failure_classification_separates_permissions_environment_model_experiment_and_budget():
    cases = [
        ('read_file', {'error': 'Absolute file access requires an explicit read-only project authorization', 'exit_code': 1}, 'permission_error'),
        ('python', {'error': 'ModuleNotFoundError: No module named scipy', 'exit_code': 1}, 'environment_error'),
        ('experiment_run', {'error': 'Provider returned an incomplete response', 'exit_code': 1}, 'model_error'),
        ('run_command', {'error': 'run exited with status 1', 'exit_code': 1}, 'experiment_failure'),
        ('experiment_run', {'error': 'Configured GPU budget exhausted', 'exit_code': 1}, 'budget_exhausted'),
    ]
    assert [classify_failure(tool, result)['category'] for tool, result, _ in cases] == [expected for _, _, expected in cases]


def test_second_same_class_failure_requires_diagnosis_and_persists_audit():
    state = {'transcript': []}
    failure = {'error': 'Absolute file access requires an explicit read-only project authorization',
               'exit_code': 1, 'control_acceptance': {'accepted_at': 'different each retry'}}
    first, action = observe_tool_result(state, 'read_file', failure, 1)
    assert first['repeat_count'] == 1 and action == 'continue'
    second, action = observe_tool_result(state, 'read_file', failure, 2)
    assert second['repeat_count'] == 2 and action == 'diagnose'
    assert 'stop retrying' in diagnosis_prompt(second)
    persisted = json.loads(json.dumps(state))
    third, action = observe_tool_result(persisted, 'read_file', failure, 3)
    assert third['state'] == 'blocked_after_failed_diagnosis' and action == 'block'
    assert persisted['error_recovery']['history'][-1]['category'] == 'permission_error'


def test_successful_diagnostic_observation_resets_repeat_count():
    state = {}
    failure = {'error': 'Execution denied by policy', 'exit_code': 1}
    observe_tool_result(state, 'run_command', failure, 1)
    _, action = observe_tool_result(state, 'run_command', failure, 2)
    assert action == 'diagnose'
    event, action = observe_tool_result(state, 'read_file', {'content': 'observed policy', 'exit_code': 0}, 3)
    assert action == 'continue' and event['state'] == 'diagnosis_observed'
    repeat, action = observe_tool_result(state, 'run_command', failure, 4)
    assert repeat['repeat_count'] == 1 and action == 'continue'
