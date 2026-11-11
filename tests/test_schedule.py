"""Tests for the pure event selection logic."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "dz_schedule",
    Path(__file__).parents[1] / "custom_components/calendar_zone/schedule.py",
)
schedule = importlib.util.module_from_spec(_spec)
sys.modules["dz_schedule"] = schedule
_spec.loader.exec_module(schedule)

ZoneEvent = schedule.ZoneEvent
select_event = schedule.select_event

TZ = timezone(timedelta(hours=11))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


TODAY = ZoneEvent("Today", at(11, 12), at(11, 21), "Site A")
TOMORROW = ZoneEvent("Tomorrow", at(12, 8), at(12, 17), "Site B")
MORNING = ZoneEvent("Morning", at(11, 9), at(11, 13), "Site A")
AFTERNOON = ZoneEvent("Afternoon", at(11, 14), at(11, 18), "Site B")


def test_active_event_wins():
    sel = select_event([TODAY, TOMORROW], at(11, 15), timedelta(minutes=60))
    assert sel.event is TODAY
    assert sel.reason == "active"
    assert sel.next_check == at(11, 21)


def test_overnight_holds_for_full_delay():
    """9pm end, 8am start, 60 min delay -> stays until 10pm."""
    delay = timedelta(minutes=60)
    sel = select_event([TODAY, TOMORROW], at(11, 21, 30), delay)
    assert sel.event is TODAY and sel.reason == "holding"
    assert sel.switch_at == at(11, 22)
    assert sel.next_check == at(11, 22)

    sel = select_event([TODAY, TOMORROW], at(11, 22), delay)
    assert sel.event is TOMORROW and sel.reason == "upcoming"
    assert sel.next_check == at(12, 8)


def test_short_gap_splits_the_difference():
    """1pm end, 2pm start, 50 min delay -> switch at 1:30pm."""
    delay = timedelta(minutes=50)
    sel = select_event([MORNING, AFTERNOON], at(11, 13, 29), delay)
    assert sel.event is MORNING and sel.switch_at == at(11, 13, 30)

    sel = select_event([MORNING, AFTERNOON], at(11, 13, 30), delay)
    assert sel.event is AFTERNOON and sel.reason == "upcoming"


def test_next_event_becoming_active_overrides_hold():
    """Back-to-back events: the next event takes over the moment it starts."""
    first = ZoneEvent("First", at(11, 9), at(11, 13), "A")
    second = ZoneEvent("Second", at(11, 13), at(11, 17), "B")
    sel = select_event([first, second], at(11, 13), timedelta(minutes=60))
    assert sel.event is second and sel.reason == "active"


def test_zero_delay_moves_immediately():
    sel = select_event([TODAY, TOMORROW], at(11, 21), timedelta(0))
    assert sel.event is TOMORROW


def test_upcoming_only():
    sel = select_event([TOMORROW], at(11, 10), timedelta(minutes=60))
    assert sel.event is TOMORROW and sel.reason == "upcoming"
    assert sel.next_check == at(12, 8)


def test_last_event_with_nothing_upcoming_stays():
    sel = select_event([TODAY], at(11, 21, 30), timedelta(minutes=60))
    assert sel.event is TODAY and sel.reason == "holding"
    sel = select_event([TODAY], at(11, 23), timedelta(minutes=60))
    assert sel.event is TODAY and sel.reason == "last"
    assert sel.next_check is None


def test_no_events():
    sel = select_event([], at(11, 10), timedelta(minutes=60))
    assert sel.event is None and sel.reason == "none"


def test_events_without_location_are_ignored():
    blank = ZoneEvent("Blank", at(11, 10), at(11, 12), "")
    sel = select_event([blank, TOMORROW], at(11, 11), timedelta(0))
    assert sel.event is TOMORROW


def test_timed_event_beats_all_day():
    all_day = ZoneEvent("Conference", at(11, 0), at(12, 0), "Venue", all_day=True)
    sel = select_event([all_day, TODAY], at(11, 15), timedelta(0))
    assert sel.event is TODAY
    sel = select_event([all_day, TODAY], at(11, 10), timedelta(0))
    assert sel.event is all_day


def test_overlapping_events_latest_start_wins():
    long = ZoneEvent("Long", at(11, 8), at(11, 18), "A")
    short = ZoneEvent("Short", at(11, 10), at(11, 11), "B")
    sel = select_event([long, short], at(11, 10, 30), timedelta(0))
    assert sel.event is short
    assert sel.next_check == at(11, 11)
    sel = select_event([long, short], at(11, 11), timedelta(0))
    assert sel.event is long
