"""Event selection logic for Calendar Zone.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from datetime import datetime, timedelta

REASON_ACTIVE = "active"
REASON_HOLDING = "holding"
REASON_UPCOMING = "upcoming"
REASON_LAST = "last"
REASON_NONE = "none"


@dataclass(frozen=True)
class ZoneEvent:
    """A calendar event that carries a location."""

    summary: str
    start: datetime
    end: datetime
    location: str
    all_day: bool = False


@dataclass(frozen=True)
class Selection:
    """Which event the zone should follow right now."""

    event: ZoneEvent | None
    reason: str
    next_check: datetime | None
    switch_at: datetime | None = None


def select_event(
    events: Iterable[ZoneEvent], now: datetime, delay: timedelta
) -> Selection:
    """Pick the event whose location the zone should use at ``now``.

    Rules:
    1. An active event wins. When several are active, a timed event beats an
       all-day one, then the most recently started wins.
    2. With no active event, the zone stays at the previous event for
       ``delay`` after it ends, but never for more than half the gap to the
       next event, then moves to the next event.
    3. With no next event, the zone stays where the last event put it.
    """
    usable = [e for e in events if e.location and e.end > e.start]

    boundaries: set[datetime] = set()
    for event in usable:
        if event.start > now:
            boundaries.add(event.start)
        if event.end > now:
            boundaries.add(event.end)

    def _next_check(extra: datetime | None = None) -> datetime | None:
        points = set(boundaries)
        if extra is not None and extra > now:
            points.add(extra)
        return min(points) if points else None

    active = [e for e in usable if e.start <= now < e.end]
    if active:
        chosen = max(active, key=lambda e: (not e.all_day, e.start))
        return Selection(chosen, REASON_ACTIVE, _next_check())

    upcoming = sorted(
        (e for e in usable if e.start > now), key=lambda e: (e.start, e.all_day)
    )
    past = [e for e in usable if e.end <= now]
    nxt = upcoming[0] if upcoming else None
    prev = max(past, key=lambda e: (e.end, not e.all_day)) if past else None

    if prev is not None and nxt is not None:
        gap = nxt.start - prev.end
        hold = min(delay, gap / 2)
        switch_at = prev.end + hold
        if now < switch_at:
            return Selection(prev, REASON_HOLDING, _next_check(switch_at), switch_at)
        return Selection(nxt, REASON_UPCOMING, _next_check())

    if prev is not None:
        switch_at = prev.end + delay
        if now < switch_at:
            return Selection(prev, REASON_HOLDING, _next_check(switch_at), switch_at)
        return Selection(prev, REASON_LAST, _next_check())

    if nxt is not None:
        return Selection(nxt, REASON_UPCOMING, _next_check())

    return Selection(None, REASON_NONE, None)
