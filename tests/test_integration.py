"""Run the integration inside a real Home Assistant instance."""

from datetime import datetime, timedelta

import pytest

from homeassistant import config_entries
from homeassistant.components import zone
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

DOMAIN = "calendar_zone"
CAL = "calendar.work"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
GOOGLE = "https://maps.googleapis.com/maps/api/geocode/json"

TZ = "Australia/Melbourne"


def local(day, hour, minute=0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=dt_util.get_time_zone(TZ))


@pytest.fixture
async def calendar(hass: HomeAssistant):
    """A fake calendar: a state plus a calendar.get_events action."""
    await hass.config.async_set_time_zone(TZ)
    hass.config.country = "AU"
    events: list[dict] = []

    async def get_events(call):
        start, end = call.data["start_date_time"], call.data["end_date_time"]
        out = []
        for ev in events:
            s = dt_util.parse_datetime(ev["start"]) if len(ev["start"]) > 10 else None
            e = dt_util.parse_datetime(ev["end"]) if len(ev["end"]) > 10 else None
            if s is None or (s < end and e > start):
                out.append(ev)
        return {CAL: {"events": out}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    hass.states.async_set(CAL, "off", {"friendly_name": "Work"})
    await async_setup_component(hass, "zone", {})
    return events


def ev(summary, start: datetime, end: datetime, location="", description=""):
    return {
        "summary": summary,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "location": location,
        "description": description,
    }


async def _setup(hass, **options) -> MockConfigEntry:
    opts = {
        "calendar_entity": CAL,
        "radius": 150,
        "passive": False,
        "icon": "mdi:briefcase",
        "delay_minutes": 60,
        **options,
    }
    entry = MockConfigEntry(
        domain=DOMAIN, title="Shift work", data={"zone_id": "shift_work"},
        options={"name": "Shift work", **opts}, unique_id="shift_work",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, calendar) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Shift work",
            "calendar_entity": CAL,
            "radius": 150,
            "passive": True,
            "icon": "mdi:briefcase",
            "delay_minutes": 60,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"zone_id": "shift_work"}
    assert result["options"]["name"] == "Shift work"
    assert result["options"]["passive"] is True


async def test_config_flow_rejects_existing_zone(hass: HomeAssistant, calendar) -> None:
    hass.states.async_set("zone.office", 0, {"latitude": 1, "longitude": 2, "radius": 10})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Office", "calendar_entity": CAL, "radius": 100, "passive": False,
         "icon": "mdi:briefcase", "delay_minutes": 0},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"zone_id": "zone_exists"}


async def test_follows_calendar_with_delay(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 15))
    calendar.append(ev("Shift", local(11, 12), local(11, 21), "12 Smith St, Richmond VIC 3121"))
    calendar.append(ev("Shift", local(12, 8), local(12, 17), "", "Site: -37.8500, 145.0100"))
    aioclient_mock.get(NOMINATIM, json=[{"lat": "-37.8183", "lon": "144.9980",
                                         "display_name": "12 Smith Street, Richmond"}])

    entry = await _setup(hass)

    state = hass.states.get("zone.shift_work")
    assert state is not None
    assert state.attributes["latitude"] == pytest.approx(-37.8183)
    assert state.attributes["radius"] == 150
    assert state.attributes["passive"] is False
    assert state.attributes["friendly_name"] == "Shift work"
    assert state.attributes["event"] == "Shift"
    assert aioclient_mock.mock_calls[0][1].query["countrycodes"] == "au"

    # Home Assistant's own zone logic sees the zone.
    found = zone.async_active_zone(hass, -37.8184, 144.9981)
    assert found is not None and found.entity_id == "zone.shift_work"

    sensor = hass.states.get("sensor.shift_work_current_event")
    assert sensor.state == "Shift"
    assert sensor.attributes["resolved_by"] == "openstreetmap"
    assert hass.states.get("sensor.shift_work_status").state == "following"

    # 9:30pm: still holding at today's site (60 min delay).
    freezer.move_to(local(11, 21, 30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.8183)

    # 10pm: moves to tomorrow's site, taken from coordinates in the description.
    freezer.move_to(local(11, 22, 0, ) + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("zone.shift_work")
    assert state.attributes["latitude"] == pytest.approx(-37.85)
    assert state.attributes["longitude"] == pytest.approx(145.01)
    assert hass.states.get("sensor.shift_work_current_event").attributes["resolved_by"] == "coordinates"
    assert len(aioclient_mock.mock_calls) == 1  # no lookup needed for coordinates

    # Unloading removes the zone.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work") is None


async def test_short_gap_splits_difference(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 12))
    calendar.append(ev("AM", local(11, 9), local(11, 13), "-37.80, 144.90"))
    calendar.append(ev("PM", local(11, 14), local(11, 18), "-37.90, 145.10"))
    await _setup(hass, delay_minutes=50)
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.80)

    freezer.move_to(local(11, 13, 29))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.80)
    nxt = hass.states.get("sensor.shift_work_next_check").state
    assert dt_util.parse_datetime(nxt) == local(11, 13, 30)

    freezer.move_to(local(11, 13, 30, ) + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.90)


async def test_google_used_when_key_set(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "Acme, 1 Main St, Geelong VIC"))
    aioclient_mock.get(GOOGLE, json={"status": "OK", "results": [{
        "formatted_address": "1 Main St, Geelong VIC 3220, Australia",
        "geometry": {"location": {"lat": -38.15, "lng": 144.36}}}]})
    await _setup(hass, google_api_key="abc123", country_code="AU")
    state = hass.states.get("zone.shift_work")
    assert state.attributes["latitude"] == pytest.approx(-38.15)
    call = aioclient_mock.mock_calls[0]
    assert call[1].query["key"] == "abc123" and call[1].query["region"] == "au"
    assert hass.states.get("sensor.shift_work_current_event").attributes["resolved_by"] == "google"


async def test_passive_zone_and_person_count(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    await _setup(hass, passive=True)
    assert hass.states.get("zone.shift_work").attributes["passive"] is True
    # Passive zones never become someone's active zone...
    assert zone.async_active_zone(hass, -37.80, 144.90) is None
    # ...but still count people inside them.
    hass.states.async_set("person.alex", "not_home", {"in_zones": ["zone.shift_work"]})
    await hass.async_block_till_done()
    state = hass.states.get("zone.shift_work")
    assert state.state == "1"
    assert state.attributes["persons"] == ["person.alex"]


async def test_title_filter_and_restore(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Dentist", local(11, 9), local(11, 11), "-37.70, 144.70"))
    calendar.append(ev("Work shift", local(11, 12), local(11, 17), "-37.80, 144.90"))
    entry = await _setup(hass, title_filter="work")
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.80)

    # After a reload, the zone comes back at its last position before the
    # calendar is read again.
    calendar.clear()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("zone.shift_work")
    assert state.attributes["latitude"] == pytest.approx(-37.80)
    assert hass.states.get("sensor.shift_work_status").state == "no_events"


async def test_location_not_found_keeps_zone(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 11), "-37.80, 144.90"))
    calendar.append(ev("Shift", local(11, 12), local(11, 17), "Nowhere Rd, Atlantis"))
    aioclient_mock.get(NOMINATIM, json=[])
    await _setup(hass, delay_minutes=0)

    freezer.move_to(local(11, 11) + timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.80)
    assert hass.states.get("sensor.shift_work_status").state == "location_not_found"


async def test_refresh_action(hass: HomeAssistant, calendar, aioclient_mock, freezer) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    await _setup(hass)
    calendar[0]["location"] = "-37.95, 145.20"
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-37.95)


async def test_options_flow_updates_zone(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Shift work", "calendar_entity": CAL, "radius": 300, "passive": True,
         "icon": "mdi:hospital-building", "delay_minutes": 30},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("zone.shift_work")
    assert state.attributes["radius"] == 300
    assert state.attributes["passive"] is True
    assert state.attributes["icon"] == "mdi:hospital-building"


BASE_FORM = {"calendar_entity": CAL, "radius": 100, "passive": False,
             "icon": "mdi:briefcase", "delay_minutes": 0}


async def test_config_flow_custom_entity_id(hass: HomeAssistant, calendar) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Partner's work", "zone_id": "zone.partner_work", **BASE_FORM}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"zone_id": "partner_work"}
    assert result["title"] == "Partner's work"


async def test_config_flow_rejects_bad_entity_id(hass: HomeAssistant, calendar) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    for bad in ("Partner Work!", "home", "zone.Bad-ID"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Work", "zone_id": bad, **BASE_FORM}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"zone_id": "invalid_zone_id"}, bad


async def test_rename_keeps_entity_id(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Hospital roster", "calendar_entity": CAL, "radius": 150,
         "passive": False, "icon": "mdi:briefcase", "delay_minutes": 60},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    state = hass.states.get("zone.shift_work")
    assert state is not None
    assert state.attributes["friendly_name"] == "Hospital roster"
    assert hass.states.get("zone.hospital_roster") is None
    assert entry.title == "Hospital roster"
    # Sensors keep their entity IDs and pick up the new name.
    sensor = hass.states.get("sensor.shift_work_current_event")
    assert sensor.attributes["friendly_name"] == "Hospital roster Current event"
    # A person in the zone (old-style tracker without in_zones) is matched by
    # the new name.
    hass.states.async_set("person.alex", "Hospital roster")
    await hass.async_block_till_done()
    assert hass.states.get("zone.shift_work").state == "1"


async def test_rename_rejects_blank_name(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "  ", **BASE_FORM}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_required"}


async def test_entry_from_earlier_version(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    """Entries made before the entity ID was stored still load."""
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "-37.80, 144.90"))
    entry = MockConfigEntry(
        domain=DOMAIN, title="Old zone", data={"name": "Old zone"},
        options={"calendar_entity": CAL, "radius": 100, "passive": False,
                 "icon": "mdi:briefcase", "delay_minutes": 0},
        unique_id="old_zone",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("zone.old_zone")
    assert state is not None and state.attributes["friendly_name"] == "Old zone"


async def test_attribution_follows_lookup_service(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "12 Smith St, Richmond VIC"))
    aioclient_mock.get(NOMINATIM, json=[{"lat": "-37.81", "lon": "144.99", "display_name": "x"}])
    await _setup(hass)
    sensor = hass.states.get("sensor.shift_work_current_event")
    assert sensor.attributes["attribution"] == "Location data © OpenStreetMap contributors, ODbL"


async def test_google_results_expire_after_30_days(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    tz = dt_util.get_time_zone(TZ)
    dec2 = datetime(2026, 12, 2, 9, tzinfo=tz)
    freezer.move_to(local(1, 10))
    for start in (local(1, 9), local(15, 9), dec2):
        calendar.append(ev("Shift", start, start + timedelta(hours=8), "1 Main St, Geelong VIC"))
    aioclient_mock.get(GOOGLE, json={"status": "OK", "results": [{
        "formatted_address": "1 Main St", "geometry": {"location": {"lat": -38.15, "lng": 144.36}}}]})
    await _setup(hass, google_api_key="abc123")
    assert len(aioclient_mock.mock_calls) == 1
    assert hass.states.get("sensor.shift_work_current_event").attributes["attribution"] == "Location data © Google"

    # Two weeks later the same address is still fresh: no new lookup.
    freezer.move_to(local(15, 10))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert len(aioclient_mock.mock_calls) == 1

    # After 30 days Google's coordinates must be looked up again.
    freezer.move_to(dec2 + timedelta(hours=1))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert len(aioclient_mock.mock_calls) == 2
    assert hass.states.get("zone.shift_work").attributes["latitude"] == pytest.approx(-38.15)


async def test_api_key_never_logged(
    hass: HomeAssistant, calendar, aioclient_mock, freezer, caplog
) -> None:
    import aiohttp

    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "1 Main St, Geelong VIC"))
    aioclient_mock.get(
        GOOGLE, exc=aiohttp.ClientError("Invalid URL ...geocode/json?key=secretkey987")
    )
    aioclient_mock.get(NOMINATIM, exc=aiohttp.ClientError("connection reset"))
    await _setup(hass, google_api_key="secretkey987")
    assert "secretkey987" not in caplog.text
    assert "**REDACTED**" in caplog.text
    assert hass.states.get("sensor.shift_work_status").state == "lookup_failed"


async def test_malformed_response_is_a_failed_lookup(
    hass: HomeAssistant, calendar, aioclient_mock, freezer
) -> None:
    freezer.move_to(local(11, 10))
    calendar.append(ev("Shift", local(11, 9), local(11, 17), "12 Smith St, Richmond VIC"))
    aioclient_mock.get(NOMINATIM, json=[{"unexpected": True}])
    await _setup(hass)
    assert hass.states.get("sensor.shift_work_status").state == "lookup_failed"
    assert hass.states.get("zone.shift_work") is None
