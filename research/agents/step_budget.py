"""Compatibility imports: legacy step values are work-round hints, not lifetime limits."""
from .lifecycle import initialize


def expand_if_earned(state):
    """Deprecated: automatic checkpointed rounds replace allowance expansion."""
    return False
