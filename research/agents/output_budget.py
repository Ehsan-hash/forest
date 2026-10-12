"""Provider-aware per-request output allocation.

Provider capability values are accepted only when explicitly reported by the
provider configuration. Unknown capabilities use a project/provider supplied
conservative fallback; a fallback is never represented as a model maximum.
"""
from __future__ import annotations

import math
import inspect


DEFAULT_FALLBACK_TOKENS = 1024


def complete_for_stage(client, messages, stage, **kwargs):
    """Pass stage metadata only when the provider adapter supports it."""
    try:
        parameters = inspect.signature(client.complete).parameters.values()
        accepts_stage = any(parameter.name == 'output_stage' or
                            parameter.kind == inspect.Parameter.VAR_KEYWORD
                            for parameter in parameters)
    except (TypeError, ValueError):
        accepts_stage = False
    if accepts_stage:
        kwargs['output_stage'] = stage
    return client.complete(messages, **kwargs)


def configure_request_policy(client, task_config, stage=None):
    """Apply durable project/run allocation preferences to a provider client."""
    task_config = task_config or {}
    allowed = ('adaptive_output_budget', 'adaptive_output_fallback_tokens',
               'output_recovery_attempts', 'task_complexity')
    additions = {key: task_config[key] for key in allowed if key in task_config}
    selected_stage = stage or task_config.get('research_stage') or task_config.get('output_stage') or task_config.get('kind')
    if selected_stage:
        additions['output_stage'] = selected_stage
    client.config = {**client.config, **additions}
    return client
MIN_OUTPUT_TOKENS = 1
MAX_OUTPUT_RECOVERIES = 4
STAGE_FACTORS = {'concise': 1, 'research': 2, 'planning': 2, 'ideas': 2,
                 'experiment_design': 2, 'analysis': 3, 'paper': 4,
                 'manuscript': 4, 'figure_design': 2, 'visual_review': 2}


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{name} must be a positive integer')
    return value


def recovery_attempt_limit(config):
    value = (config or {}).get('output_recovery_attempts', 2)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_OUTPUT_RECOVERIES:
        raise ValueError(f'output_recovery_attempts must be an integer from zero to {MAX_OUTPUT_RECOVERIES}')
    return value


def model_capabilities(config):
    """Return declared model limits without inferring from a model name."""
    config = config or {}
    declared = config.get('model_capabilities') or {}
    if not isinstance(declared, dict):
        raise ValueError('model_capabilities must be an object')
    result = {}
    for field in ('max_output_tokens', 'context_window_tokens'):
        value = declared.get(field)
        if value is not None:
            result[field] = _positive_int(value, f'model_capabilities.{field}')
    if 'context_window_tokens' not in result:
        explicit_context = config.get('context_length', config.get('context_window_tokens'))
        if explicit_context is not None:
            result['context_window_tokens'] = _positive_int(explicit_context, 'context window tokens')
    if result:
        result['source'] = declared.get('source', 'provider_config')
    return result


def _estimated_input_tokens(messages):
    # UTF-8 bytes / 3 is deliberately conservative across common tokenizers.
    # Providers with a tokenizer can supply a more exact estimate in config.
    size = sum(len(str(item.get('content', '')).encode('utf-8')) for item in messages)
    return math.ceil(size / 3) + 256


def _stage_target(config, stage, *, known_output_capacity):
    policy = config.get('adaptive_output_budget') or {}
    if not isinstance(policy, dict):
        raise ValueError('adaptive_output_budget must be an object')
    targets = policy.get('stage_targets') or {}
    if not isinstance(targets, dict):
        raise ValueError('adaptive_output_budget.stage_targets must be an object')
    explicit = targets.get(stage) if stage else None
    if explicit is None:
        # These are request-size priors, not hard limits. Capability and outer
        # budget checks below remain authoritative.
        complexity = config.get('task_complexity', policy.get('complexity', 'normal'))
        base = policy.get('initial_tokens')
        if base is not None:
            explicit = _positive_int(base, 'adaptive_output_budget.initial_tokens')
        else:
            fallback = config.get('adaptive_output_fallback_tokens', DEFAULT_FALLBACK_TOKENS)
            explicit = _positive_int(fallback, 'adaptive_output_fallback_tokens')
            if known_output_capacity and stage in STAGE_FACTORS:
                explicit *= STAGE_FACTORS[stage]
        if known_output_capacity and complexity in ('high', 'very_high'):
            factor = policy.get('complexity_factor', 2 if complexity == 'high' else 3)
            if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor < 1:
                raise ValueError('adaptive_output_budget.complexity_factor must be finite and at least one')
            explicit = math.ceil(explicit * factor)
    return _positive_int(explicit, 'adaptive output target')


def output_allocation(config, messages, *, stage=None, requested=None):
    """Allocate tokens for one request and describe all binding constraints."""
    config = config or {}
    capabilities = model_capabilities(config)
    policy = config.get('adaptive_output_budget') or {}
    if not isinstance(policy, dict):
        raise ValueError('adaptive_output_budget must be an object')
    fallback = _positive_int(config.get('adaptive_output_fallback_tokens', DEFAULT_FALLBACK_TOKENS),
                             'adaptive_output_fallback_tokens')
    prior = _stage_target(config, stage, known_output_capacity=bool(
        {'max_output_tokens', 'context_window_tokens'} & capabilities.keys()))
    # Old max_output_tokens/max_tokens fields historically described request
    # limits. Preserve them as a requested starting point, never model facts.
    legacy_request = config.get('max_output_tokens', config.get('max_tokens'))
    if requested is not None:
        target = _positive_int(requested, 'requested output tokens')
    elif legacy_request is not None:
        target = _positive_int(legacy_request, 'max_output_tokens')
    else:
        target = prior
    constraints = []
    if 'max_output_tokens' in capabilities:
        target = min(target, capabilities['max_output_tokens'])
        constraints.append('declared_model_output_limit')
    context = capabilities.get('context_window_tokens')
    if context is not None:
        remaining = context - _estimated_input_tokens(messages)
        if remaining < MIN_OUTPUT_TOKENS:
            return {'tokens': 0, 'capabilities': capabilities, 'source': 'context_window_exhausted',
                    'constraints': ['declared_context_window'], 'remaining_context_tokens': remaining}
        target = min(target, remaining)
        constraints.append('declared_context_window')
    if 'max_output_tokens' not in capabilities:
        # Unknown model capacity is not guessed. The configurable conservative
        # fallback is a starting allocation, not a claim about the model.
        if legacy_request is None and requested is None:
            target = min(target, prior)
        constraints.append('conservative_fallback')
    return {'tokens': max(MIN_OUTPUT_TOKENS, target), 'capabilities': capabilities,
            'source': capabilities.get('source', 'provider_config') if capabilities else 'conservative_fallback',
            'constraints': constraints, 'estimated_input_tokens': _estimated_input_tokens(messages),
            'remaining_context_tokens': context - _estimated_input_tokens(messages) if context is not None else None}


def grow_output_allocation(config, messages, previous, *, stage=None):
    """Grow only within declared capacity; return None when splitting is needed."""
    capabilities = model_capabilities(config)
    maximum = capabilities.get('max_output_tokens')
    context = capabilities.get('context_window_tokens')
    if maximum is None and context is None:
        return None
    # A declared context window is itself a rigorous per-request ceiling even
    # for providers that do not publish a distinct completion-token limit.
    remaining = context - _estimated_input_tokens(messages) if context is not None else maximum
    ceiling = remaining if maximum is None else min(maximum, remaining)
    if ceiling <= previous:
        return None
    policy = config.get('adaptive_output_budget') or {}
    factor = policy.get('growth_factor', 2)
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 1:
        raise ValueError('adaptive_output_budget.growth_factor must be finite and greater than one')
    return min(ceiling, max(previous + 1, math.ceil(previous * factor)))
