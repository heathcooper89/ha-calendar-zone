"""Keeps a zone positioned at the location of the relevant calendar event."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import CoreState, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    TrackStates,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_state_change_filtered,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    ATTR_EVENT,
    ATTR_LOCATION,
    CONF_CALENDAR,
    CONF_COUNTRY,
    CONF_DELAY,
    CONF_GOOGLE_API_KEY,
    CONF_ICON,
    CONF_PASSIVE,
    CONF_RADIUS,
    CONF_TITLE_FILTER,
    CONF_ZONE_ID,
    DEFAULT_DELAY,
    DEFAULT_ICON,
    DEFAULT_RADIUS,
    DOMAIN,
    LOOKAHEAD,
    POLL_INTERVAL,
    STATUS_CALENDAR_UNAVAILABLE,
    STATUS_FOLLOWING,
    STATUS_LOOKUP_FAILED,
    STATUS_NO_EVENTS,
    STATUS_NOT_FOUND,
    STATUS_STARTING,
)
from .geocode import GeocodeError, Geocoder, GeoResult, Throttle, parse_coordinates
from .schedule import ZoneEvent, select_event

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
ATTR_IN_ZONES = "in_zones"


def zone_entity_id(object_id: str) -> str:
    """Entity ID for a zone object ID, e.g. shift_work -> zone.shift_work."""
    return f"zone.{object_id}"


def _parse_when(value: str) -> tuple[datetime, bool]:
    """Parse a calendar start/end value. Returns (datetime, is_all_day)."""
    if len(value) == 10:
        return dt_util.start_of_local_day(date.fromisoformat(value)), True
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Unrecognised date/time {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed, False


class CalendarZoneManager:
    """Owns one zone entity and moves it to follow a calendar."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        opts = {**entry.data, **entry.options}
        # The display name can be changed in the options; the entity ID is
        # fixed when the zone is set up. Entries created before the entity ID
        # was stored derive it from the name they were created with.
        self.name: str = opts.get(CONF_NAME) or entry.title
        self.entity_id = zone_entity_id(
            entry.data.get(CONF_ZONE_ID) or slugify(entry.data.get(CONF_NAME, self.name))
        )
        self.calendar: str = opts[CONF_CALENDAR]
        self.radius = float(opts.get(CONF_RADIUS, DEFAULT_RADIUS))
        self.passive = bool(opts.get(CONF_PASSIVE, False))
        self.icon: str = opts.get(CONF_ICON) or DEFAULT_ICON
        self.delay = timedelta(minutes=float(opts.get(CONF_DELAY, DEFAULT_DELAY)))
        self.title_filter: str = (opts.get(CONF_TITLE_FILTER) or "").strip().lower()
        self._google_key: str | None = opts.get(CONF_GOOGLE_API_KEY)
        # Default to the country Home Assistant is configured for.
        self._country: str | None = opts.get(CONF_COUNTRY) or hass.config.country

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.geocoder: Geocoder | None = None

        # Current zone position and what put it there.
        self.position: GeoResult | None = None
        self.location_text: str | None = None
        self.event: dict[str, Any] | None = None
        self.reason: str | None = None
        self.status: str = STATUS_STARTING
        self.next_check: datetime | None = None
        self.last_refresh: datetime | None = None

        self._persons: set[str] = set()
        self._trackers: set[str] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_timer: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        """Restore the last position, publish the zone and start tracking."""
        stored = await self._store.async_load() or {}
        self.geocoder = Geocoder(
            async_get_clientsession(self.hass),
            self._google_key,
            self._country,
            stored.get("cache") or {},
            self.hass.data.setdefault(f"{DOMAIN}_nominatim_throttle", Throttle()),
        )
        if last := stored.get("last"):
            try:
                self.position = GeoResult.from_dict(last["position"])
                self.location_text = last.get("location_text")
                self.event = last.get("event")
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Ignoring unreadable stored position for %s", self.name)

        self._recount()
        self._publish()

        self._unsubs.append(
            async_track_state_change_filtered(
                self.hass,
                TrackStates(False, set(), {"person", "device_tracker"}),
                self._tracked_changed,
            ).async_remove
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.calendar], self._calendar_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._poll, POLL_INTERVAL)
        )

        if self.hass.state is CoreState.running:
            self._schedule_refresh()
        else:
            self._unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._started
                )
            )

    async def async_stop(self) -> None:
        """Stop tracking and remove the zone."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await self._save()
        self.hass.states.async_remove(self.entity_id)

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for when status changes (used by sensors)."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    # -------------------------------------------------------------- triggers

    @callback
    def _started(self, _event: Event) -> None:
        self._schedule_refresh()

    @callback
    def _calendar_changed(self, _event: Event) -> None:
        self._schedule_refresh()

    @callback
    def _poll(self, _now: datetime) -> None:
        self._schedule_refresh()

    @callback
    def _timer_fired(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._schedule_refresh()

    @callback
    def _schedule_refresh(self) -> None:
        self.entry.async_create_task(
            self.hass, self.async_refresh(), f"{DOMAIN} refresh {self.entity_id}"
        )

    # --------------------------------------------------------------- refresh

    async def async_refresh(self) -> None:
        """Work out which event applies now and move the zone to it."""
        async with self._lock:
            await self._refresh()
        self._notify()

    async def _refresh(self) -> None:
        now = dt_util.now()
        self.last_refresh = now
        try:
            events = await self._fetch_events(now)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not read %s: %s", self.calendar, err)
            self.status = STATUS_CALENDAR_UNAVAILABLE
            return

        selection = select_event(events, now, self.delay)
        self.reason = selection.reason
        self._set_timer(selection.next_check)

        if selection.event is None:
            self.status = STATUS_NO_EVENTS
            return

        ev = selection.event
        event_info = {
            "summary": ev.summary,
            "start": ev.start.isoformat(),
            "end": ev.end.isoformat(),
            "location": ev.location,
        }

        if ev.location == self.location_text and self.position is not None:
            self.event = event_info
            self.status = STATUS_FOLLOWING
            self._publish()
            return

        assert self.geocoder is not None
        try:
            result = await self.geocoder.async_resolve(ev.location)
        except GeocodeError as err:
            _LOGGER.warning("Location lookup for %r failed: %s", ev.location, err)
            self.status = STATUS_LOOKUP_FAILED
            return

        if result is None:
            _LOGGER.warning(
                "No coordinates found for %r (event %r); zone %s not moved",
                ev.location,
                ev.summary,
                self.entity_id,
            )
            self.status = STATUS_NOT_FOUND
            return

        _LOGGER.info(
            "Moving %s to %s, %s for %r",
            self.entity_id,
            result.latitude,
            result.longitude,
            ev.summary,
        )
        self.position = result
        self.location_text = ev.location
        self.event = event_info
        self.status = STATUS_FOLLOWING
        self._recount()
        self._publish()
        await self._save()

    async def _fetch_events(self, now: datetime) -> list[ZoneEvent]:
        if self.hass.states.get(self.calendar) is None:
            raise HomeAssistantError(f"{self.calendar} does not exist yet")
        start = now - self.delay - timedelta(days=1)
        end = now + LOOKAHEAD
        response = await self.hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": self.calendar,
                "start_date_time": start,
                "end_date_time": end,
            },
            blocking=True,
            return_response=True,
        )
        raw = (response or {}).get(self.calendar, {}).get("events", [])
        events: list[ZoneEvent] = []
        for item in raw:
            summary = item.get("summary") or ""
            if self.title_filter and self.title_filter not in summary.lower():
                continue
            location = (item.get("location") or "").strip()
            if not location:
                coords = parse_coordinates(item.get("description"))
                if coords is None:
                    continue
                location = f"{coords[0]},{coords[1]}"
            try:
                ev_start, all_day = _parse_when(item["start"])
                ev_end, _ = _parse_when(item["end"])
            except (KeyError, ValueError) as err:
                _LOGGER.debug("Skipping event %r: %s", summary, err)
                continue
            events.append(ZoneEvent(summary, ev_start, ev_end, location, all_day))
        return events

    @callback
    def _set_timer(self, when: datetime | None) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        self.next_check = when
        if when is not None:
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass,
                self._timer_fired,
                dt_util.as_utc(when) + timedelta(seconds=1),
            )

    # ------------------------------------------------------------- the zone

    def _in_zone(self, state: State) -> bool:
        if ATTR_IN_ZONES in state.attributes:
            return self.entity_id in (state.attributes[ATTR_IN_ZONES] or [])
        return state.state == self.name

    @callback
    def _recount(self) -> None:
        self._persons = {
            s.entity_id for s in self.hass.states.async_all("person") if self._in_zone(s)
        }
        self._trackers = {
            s.entity_id
            for s in self.hass.states.async_all("device_tracker")
            if self._in_zone(s)
        }

    @callback
    def _tracked_changed(self, event: Event) -> None:
        entity_id: str = event.data["entity_id"]
        new_state: State | None = event.data["new_state"]
        target = self._persons if entity_id.startswith("person.") else self._trackers
        inside = new_state is not None and self._in_zone(new_state)
        if inside == (entity_id in target):
            return
        if inside:
            target.add(entity_id)
        else:
            target.discard(entity_id)
        self._publish()

    @callback
    def _publish(self) -> None:
        """Write the zone into Home Assistant's state machine."""
        if self.position is None:
            return
        attributes: dict[str, Any] = {
            ATTR_LATITUDE: self.position.latitude,
            ATTR_LONGITUDE: self.position.longitude,
            "radius": self.radius,
            "passive": self.passive,
            "persons": sorted(self._persons),
            "device_trackers": sorted(self._trackers),
            "editable": False,
            ATTR_ICON: self.icon,
            ATTR_FRIENDLY_NAME: self.name,
        }
        if self.event:
            attributes[ATTR_EVENT] = self.event.get("summary")
            attributes[ATTR_LOCATION] = self.event.get("location")
        self.hass.states.async_set(self.entity_id, len(self._persons), attributes)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    async def _save(self) -> None:
        data: dict[str, Any] = {"cache": self.geocoder.cache if self.geocoder else {}}
        if self.position is not None:
            data["last"] = {
                "position": self.position.as_dict(),
                "location_text": self.location_text,
                "event": self.event,
            }
        await self._store.async_save(data)
