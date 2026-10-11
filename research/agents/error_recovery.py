"""Persisted diagnosis circuit breaker for repeated Research Agent tool errors."""
from __future__ import annotations


DIAGNOSTIC_TOOLS = {
    'read_file', 'list_files', 'read_transcript', 'read_context_segment',
    'inspect_process', 'read_process_output', 'results',
}
SUBSTANTIVE_TOOLS = {
    'write_file', 'write_file_chunk', 'python', 'run_command', 'start_process',
    'experiment_run', 'verification_run', 'graph_command', 'paper_generate',
    'paper_compile', 'figure_render', 'theory_check',
}


def classify_failure(tool, result):
    """Classify only observed tool failures; never infer model-side success."""
    if result.get('action_discarded'):
        return None
    error = str(result.get('error') or result.get('reason') or '').strip()
    exit_code = result.get('exit_code')
    failed = bool(error) or (isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0) or result.get('status') == 'failed'
    if not failed:
        return None
    text = error.casefold()
    code = str(result.get('code') or result.get('error_code') or '').upper()
    if code in {'PATH_ESCAPE', 'PERMISSION_DENIED', 'EACCES', 'EPERM', 'AUTHORIZATION'} or any(
            token in text for token in ('permission denied', 'not authorized', 'authorization', 'unauthorized', 'forbidden', 'path escapes', 'access denied')):
        category = 'permission_error'
    elif any(token in text for token in ('budget', 'token limit', 'cost limit', 'gpu hour', 'resource limit')):
        category = 'budget_exhausted'
    elif any(token in text for token in ('provider', 'model response', 'model request', 'context window', 'incomplete response')):
        category = 'model_error'
    elif any(token in text for token in ('not found', 'no such file', 'no module named', 'missing dependency', 'executable',
                                          'missing project file', 'timed out', 'timeout', 'unavailable', 'environment')):
        category = 'environment_error'
    elif tool in {'run_command', 'python', 'start_process', 'experiment_run', 'verification_run'} and (
            exit_code not in (None, 0) or result.get('status') == 'failed'):
        category = 'experiment_failure'
    else:
        category = 'tool_error'
    return {'category': category, 'tool': tool, 'code': code or None, 'message': error or 'Tool returned a nonzero exit status'}


def observe_tool_result(state, tool, result, step):
    """Return a persisted event and whether the caller should diagnose or stop."""
    recovery = state.setdefault('error_recovery', {'last_category': None, 'repeat_count': 0,
        'diagnosis_pending': False, 'history': []})
    failure = classify_failure(tool, result)
    if failure is None:
        if recovery.get('diagnosis_pending') and (tool in DIAGNOSTIC_TOOLS or tool in SUBSTANTIVE_TOOLS):
            event = {'step': step, 'state': 'diagnosis_observed', 'tool': tool,
                     'prior_category': recovery.get('last_category')}
            recovery.update(last_category=None, repeat_count=0, diagnosis_pending=False,
                            last_error=None, last_failure_tool=None)
            recovery.setdefault('history', []).append(event)
            return event, 'continue'
        return None, 'continue'

    prior_category = recovery.get('last_category')
    if failure['category'] == 'budget_exhausted':
        event = {'step': step, **failure, 'repeat_count': recovery.get('repeat_count', 0) + 1,
                 'state': 'budget_exhausted'}
        recovery.update(last_category=failure['category'], repeat_count=event['repeat_count'],
                        diagnosis_pending=False, last_error=failure['message'], last_failure_tool=tool)
        recovery.setdefault('history', []).append(event)
        return event, 'block'
    if recovery.get('diagnosis_pending'):
        event = {'step': step, **failure, 'repeat_count': recovery.get('repeat_count', 2),
                 'state': 'blocked_after_failed_diagnosis'}
        recovery.setdefault('history', []).append(event)
        recovery['last_error'] = failure['message']
        return event, 'block'

    count = recovery.get('repeat_count', 0) + 1 if prior_category == failure['category'] else 1
    outcome = 'diagnose' if count >= 2 else 'continue'
    event = {'step': step, **failure, 'repeat_count': count,
             'state': 'diagnosis_required' if outcome == 'diagnose' else 'observed'}
    recovery.update(last_category=failure['category'], repeat_count=count,
                    diagnosis_pending=outcome == 'diagnose', last_error=failure['message'],
                    last_failure_tool=tool)
    recovery.setdefault('history', []).append(event)
    return event, outcome


def diagnosis_prompt(event):
    return ('ERROR RECOVERY REQUIRED: the same classified failure occurred twice. stop retrying the failing action. '
            f"Classification: {event['category']}; tool: {event['tool']}; observed error: {event['message']}. "
            'Inspect the actual source, permissions, environment, provider response, experiment log, or remaining budget '
            'using available diagnostic tools. State the evidence-based cause, then either make one materially corrected '
            'action or stop and report the blocker. Do not merely change arguments cosmetically, repeat the action, '
            'claim an experiment succeeded, or spend more compute without resolving the diagnosed cause.')
