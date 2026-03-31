from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Lock
from typing import Protocol

from app.core.types import utc_now


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=utc_now().tzinfo)
    return value


class RuntimeClock(Protocol):
    @property
    def is_controllable(self) -> bool: ...

    def now(self) -> datetime: ...

    def sleep_delay_until(self, target: datetime) -> float | None: ...

    def set(self, value: datetime) -> None: ...

    def advance(self, delta: timedelta) -> datetime: ...


class SystemClock:
    @property
    def is_controllable(self) -> bool:
        return False

    def now(self) -> datetime:
        return utc_now()

    def sleep_delay_until(self, target: datetime) -> float | None:
        return max((_ensure_utc(target) - self.now()).total_seconds(), 0.0)

    def set(self, value: datetime) -> None:
        del value
        raise RuntimeError("SystemClock cannot be adjusted.")

    def advance(self, delta: timedelta) -> datetime:
        del delta
        raise RuntimeError("SystemClock cannot be adjusted.")


class AdjustableClock:
    def __init__(
        self,
        *,
        start_at: datetime | None = None,
        tick_real_time: bool = False,
    ) -> None:
        real_now = utc_now()
        self._lock = Lock()
        self._anchor_real = real_now
        self._anchor_virtual = _ensure_utc(start_at or real_now)
        self._tick_real_time = tick_real_time

    @property
    def tick_real_time(self) -> bool:
        return self._tick_real_time

    @property
    def is_controllable(self) -> bool:
        return True

    def now(self) -> datetime:
        real_now = utc_now()
        with self._lock:
            if not self._tick_real_time:
                return self._anchor_virtual
            return self._current_virtual(real_now)

    def sleep_delay_until(self, target: datetime) -> float | None:
        real_now = utc_now()
        with self._lock:
            if not self._tick_real_time:
                del target
                return 60.0 * 60.0 * 24.0 * 365.0
            current = self._current_virtual(real_now)
            return max((_ensure_utc(target) - current).total_seconds(), 0.0)

    def set(self, value: datetime) -> None:
        real_now = utc_now()
        with self._lock:
            self._anchor_real = real_now
            self._anchor_virtual = _ensure_utc(value)

    def advance(self, delta: timedelta) -> datetime:
        real_now = utc_now()
        with self._lock:
            current = (
                self._current_virtual(real_now)
                if self._tick_real_time
                else self._anchor_virtual
            )
            self._anchor_real = real_now
            self._anchor_virtual = current + delta
            return self._anchor_virtual

    def pause(self) -> datetime:
        real_now = utc_now()
        with self._lock:
            current = (
                self._current_virtual(real_now)
                if self._tick_real_time
                else self._anchor_virtual
            )
            self._anchor_real = real_now
            self._anchor_virtual = current
            self._tick_real_time = False
            return self._anchor_virtual

    def resume(self) -> datetime:
        real_now = utc_now()
        with self._lock:
            current = (
                self._current_virtual(real_now)
                if self._tick_real_time
                else self._anchor_virtual
            )
            self._anchor_real = real_now
            self._anchor_virtual = current
            self._tick_real_time = True
            return self._anchor_virtual

    def _current_virtual(self, real_now: datetime) -> datetime:
        return self._anchor_virtual + (real_now - self._anchor_real)


class FunctionClock:
    def __init__(self, provider: Callable[[], datetime]) -> None:
        self._provider = provider

    @property
    def is_controllable(self) -> bool:
        return False

    def now(self) -> datetime:
        return _ensure_utc(self._provider())

    def sleep_delay_until(self, target: datetime) -> float | None:
        del target
        return None

    def set(self, value: datetime) -> None:
        del value
        raise RuntimeError("FunctionClock cannot be adjusted.")

    def advance(self, delta: timedelta) -> datetime:
        del delta
        raise RuntimeError("FunctionClock cannot be adjusted.")
