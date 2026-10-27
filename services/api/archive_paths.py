"""Archive path checks shared by the project import endpoint."""

from __future__ import annotations

from collections.abc import Iterable
import posixpath
import unicodedata


def _destination_identity(name: str) -> str:
    """Return the portable identity used by archive destinations."""
    return unicodedata.normalize("NFC", posixpath.normpath(name)).casefold()


def case_collision(names: Iterable[str]) -> tuple[str, str] | None:
    """Return the first pair of names that target the same destination.

    Archive extraction can overwrite an earlier member when names are exact
    duplicates, differ only by case, use dot segments, or use different
    Unicode normalization forms. Keep the original spellings for diagnostics.
    """
    seen: dict[str, str] = {}
    for name in names:
        identity = _destination_identity(name)
        previous = seen.get(identity)
        if previous is not None:
            return previous, name
        seen[identity] = name
    return None
