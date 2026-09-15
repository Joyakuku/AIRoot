"""Version constraints for ``airoot where --version``.

Supports the documented form (``>=3.11,<3.13``): a comma-separated conjunction of
comparators ``>=``, ``>``, ``<=``, ``<``, ``==``, ``=`` (a bare version means
equality). Pre-release and range semantics beyond this are **not** implemented in
P1 — an unsupported constraint is refused rather than silently widened, because a
version that does not satisfy the constraint must never be used (v0.3 §5).
"""

from __future__ import annotations

import re

from ..exits import AirootError

_COMPARATOR = re.compile(r"^\s*(>=|<=|==|>|<|=)?\s*([0-9][0-9A-Za-z.+_-]*)\s*$")


def parse_version(text: str) -> tuple[int, ...]:
    """Numeric components of a version; non-numeric tails are ignored for ordering."""

    if not isinstance(text, str) or not text.strip():
        raise AirootError("INVALID_INPUT", f"empty version: {text!r}")
    parts: list[int] = []
    for chunk in re.split(r"[._\-+]", text.strip()):
        match = re.match(r"^(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
    if not parts:
        raise AirootError("INVALID_INPUT", f"version has no numeric component: {text!r}")
    return tuple(parts)


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def satisfies(version: str, constraint: str | None) -> bool:
    """``True`` when ``version`` satisfies every comparator in ``constraint``."""

    if constraint is None or not constraint.strip():
        return True

    actual = parse_version(version)
    for clause in constraint.split(","):
        clause = clause.strip()
        if not clause:
            raise AirootError("INVALID_INPUT", f"empty comparator in constraint: {constraint!r}")
        match = _COMPARATOR.match(clause)
        if not match:
            raise AirootError(
                "INVALID_INPUT",
                f"unsupported version constraint: {clause!r}",
                evidence=["supported comparators: >=, >, <=, <, ==, = (comma separated)"],
            )
        operator = match.group(1) or "=="
        expected = parse_version(match.group(2))
        result = _compare(actual, expected)
        if operator == ">=" and result < 0:
            return False
        if operator == ">" and result <= 0:
            return False
        if operator == "<=" and result > 0:
            return False
        if operator == "<" and result >= 0:
            return False
        if operator in ("==", "=") and result != 0:
            return False
    return True
