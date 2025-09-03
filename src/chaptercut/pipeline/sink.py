"""How the pipeline reports progress without knowing that Telegram exists.

`pipeline/` must never import `bot/`. It talks to this protocol instead, which
is why the whole pipeline is testable headless.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from chaptercut.queue.models import Phase


@runtime_checkable
class ProgressSink(Protocol):
    async def update(
        self,
        phase: Phase,
        pct: float | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        """Report progress. `pct` is 0..100, `detail` is a short human string."""
        ...


class NullSink:
    """Swallows progress. The default for tests and for jobs with no chat."""

    async def update(
        self,
        phase: Phase,
        pct: float | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        return None


class RecordingSink:
    """Keeps every update, so tests can assert on the phase sequence."""

    def __init__(self) -> None:
        self.events: list[tuple[Phase, float | None, str | None]] = []

    async def update(
        self,
        phase: Phase,
        pct: float | None = None,
        detail: str | None = None,
        force: bool = False,
    ) -> None:
        self.events.append((phase, pct, detail))

    @property
    def phases(self) -> list[Phase]:
        seen: list[Phase] = []
        for phase, _pct, _detail in self.events:
            if not seen or seen[-1] != phase:
                seen.append(phase)
        return seen
