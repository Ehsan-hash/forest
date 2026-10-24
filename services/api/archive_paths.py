"""Archive path checks shared by the project import endpoint."""

from __future__ import annotations

from collections.abc import Iterable


def case_collision(names: Iterable[str]) -> tuple[str, str] | None:
    """Return the first pair of names that differ only by case, if any.

    A case-insensitive destination cannot hold both names, so an archive that
    carries both would lose one on extraction (#361).
    """
    seen: dict[str, str] = {}
    for name in names:
        folded = name.casefold()
        previous = seen.get(folded)
        if previous is not None and previous != name:
            return previous, name
        seen[folded] = name
    return None
