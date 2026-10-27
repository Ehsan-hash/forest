"""Account for failed model turns and bound incremental code recovery.

An incomplete function call is never executable. A recovery is a new billed
request under the original run's remaining budget, not a transport retry.
"""
from __future__ import annotations

from .output_budget import grow_output_allocation, recovery_attempt_limit


def account_usage(state, usage, elapsed):
    usage = usage or {}
    state['active_seconds'] = state.get('active_seconds', 0) + elapsed
    totals = state['totals']
    for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens'):
        totals[key] = totals.get(key, 0) + (usage.get(key) or 0)
    if usage.get('input_tokens') is None or usage.get('output_tokens') is None:
        state['usage_complete'] = False
    if 'known_cost' not in totals:
        totals['known_cost'] = totals.get('cost') or 0
    if usage.get('cost') is None:
        state['cost_complete'] = False
    else:
        totals['known_cost'] += usage['cost']
    totals['cost'] = totals['known_cost'] if state.get('cost_complete', True) else None


def record_provider_failure(state, error, elapsed, model):
    """Save public failure/usage; partial text is diagnostic, never an action."""
    account_usage(state, error.usage, elapsed)
    turn = {'step': len(state['transcript']) + 1, 'model': error.model or model,
            'status': 'incomplete' if error.code == 'incomplete_response' else 'failed',
            'content': '', 'usage': error.usage, 'elapsed_seconds': elapsed,
            'request_id': error.request_id, 'response_id': error.response_id,
            'error': {'code': error.code, 'message': str(error), 'ambiguous': error.ambiguous,
                      'incomplete_reason': error.incomplete_reason},
            'tool_calls': [], 'tool_executed': False}
    if error.partial_output:
        turn['incomplete_public_output'] = error.partial_output
        turn['partial_output_policy'] = 'Diagnostic fragments only; never parsed or executed as tool actions.'
    state['transcript'].append(turn)
    state['status'] = 'provider_error'
    state['last_provider_error'] = turn['error']
    return turn


def prepare_output_recovery(state, error, config, allowed, *, messages=None, output_budget=None):
    """Recover only a reported token truncation; partial output is never parsed."""
    maximum = recovery_attempt_limit(config)
    recovery = state.get('output_recovery', {})
    usage = error.usage or {}
    executed_actions = sum(1 for turn in state.get('transcript', [])
                           if isinstance(turn.get('executed_action'), dict))
    used = recovery.get('used', 0) if recovery.get('executed_actions') == executed_actions else 0
    if (error.code != 'incomplete_response' or error.incomplete_reason != 'max_output_tokens'
            or error.ambiguous or not any(name != 'finish' for name in allowed)
            or used >= maximum):
        return False
    previous = output_budget or error.requested_output_tokens
    next_budget = None
    if isinstance(previous, int) and previous > 0 and messages is not None:
        try:
            next_budget = grow_output_allocation(config, messages, previous)
        except ValueError:
            return False
    state['output_recovery'] = {'used': used + 1, 'executed_actions': executed_actions, 'pending': True,
                                'failed_step': len(state['transcript']), 'request_id': error.request_id,
                                'previous_output_tokens': previous, 'next_output_tokens': next_budget,
                                'strategy': 'increase_within_declared_capability' if next_budget else 'split_into_smaller_actions'}
    state['status'] = 'recovering_output'
    state['messages'].append({'role': 'user', 'content': (
        'The last response was explicitly truncated at max_output_tokens. No action from that response was executed. '
        'Its measured tokens, cost, and elapsed time are retained against this same run. '
        + (f'The next request may use at most {next_budget} output tokens, within the provider declared output and context limits. '
           if next_budget else 'Provider capacity is unknown or already at its declared ceiling; do not guess a larger limit. Split the work into smaller independently verifiable actions or artifacts. ')
        + 'Return exactly one complete, valid JSON tool action from the enabled tools. Keep arguments concise; for large files use write_file_chunk with the observed exact byte offset, and for large research plans, datasets, analyses, or prose create a sequence of separately verifiable artifacts and combine them only after each is complete. '
        'Never repeat a partial JSON object or treat diagnostic fragments as saved output. Execute and inspect each real computation under the existing outer budgets. '
        'Diagnostic fragments remain available in read_transcript; they are not executable evidence.')})
    return True


def model_turn_tools(state, allowed):
    """Recovery calls remain authorized but cannot finish on a truncated turn."""
    if state.get('output_recovery', {}).get('pending'):
        return [name for name in allowed if name != 'finish']
    return list(allowed)
