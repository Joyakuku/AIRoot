"""Injectable clock.

Expiry, ``issued_at``/``expires_at`` and journal ordering must be deterministic
in tests, so nothing in the core calls ``datetime.now`` directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

UTC = timezone.utc


def isoformat(moment: datetime) -> str:
    """RFC 3339 / JSON-Schema ``date-time`` form, second precision, ``Z`` suffix."""

    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Clock(Protocol):
    def now(self) -> datetime: ...

    def timestamp(self) -> str: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def timestamp(self) -> str:
        return isoformat(self.now())


class FakeClock:
    """Deterministic clock: every ``now()`` advances by ``step``."""

    def __init__(self, start: str = "2024-01-01T00:00:00Z", step: timedelta = timedelta(seconds=1)) -> None:
        self._current = parse_timestamp(start)
        self._step = step

    def now(self) -> datetime:
        moment = self._current
        self._current = self._current + self._step
        return moment

    def timestamp(self) -> str:
        return isoformat(self.now())

    def advance(self, delta: timedelta) -> None:
        self._current = self._current + delta


SYSTEM_CLOCK = SystemClock()
