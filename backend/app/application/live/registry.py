"""A bounded set of live sessions (Phase 13).

Each session holds candle books, so the number of sessions is itself a memory
bound. Registration beyond the cap is refused - never answered by evicting a
session somebody is still reading.
"""

from __future__ import annotations

from app.application.live.session import LiveSession

__all__ = ["LiveCapacityError", "LiveSessionRegistry"]


class LiveCapacityError(RuntimeError):
    """The registry is full."""


class LiveSessionRegistry:
    def __init__(self, max_sessions: int = 8) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._max = max_sessions
        self._sessions: dict[str, LiveSession] = {}

    @property
    def capacity(self) -> int:
        return self._max

    def __len__(self) -> int:
        return len(self._sessions)

    def register(self, name: str, session: LiveSession) -> None:
        if name in self._sessions:
            raise ValueError(f"a live session named {name!r} already exists")
        if len(self._sessions) >= self._max:
            raise LiveCapacityError(
                f"at most {self._max} live sessions may run at once; close one first"
            )
        self._sessions[name] = session

    def get(self, name: str) -> LiveSession | None:
        return self._sessions.get(name)

    def remove(self, name: str) -> LiveSession | None:
        return self._sessions.pop(name, None)
