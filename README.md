# Calendar Zone

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/heathcooper89/ha-calendar-zone/actions/workflows/validate.yml/badge.svg)](https://github.com/heathcooper89/ha-calendar-zone/actions/workflows/validate.yml)
[![Tests](https://github.com/heathcooper89/ha-calendar-zone/actions/workflows/tests.yml/badge.svg)](https://github.com/heathcooper89/ha-calendar-zone/actions/workflows/tests.yml)

A Home Assistant custom integration that creates a zone which moves to the location of the current or next event on a calendar.

Built for work locations that change from day to day: put each shift on a calendar with its address and `zone.shift_work` (or whatever you name it) follows along. Zone triggers, "is in zone" conditions, people's location state, the map and Waze Travel Time all work with it like any other zone.

Tested with Home Assistant 2026.2.3 and 2026.10.4. The integration icon shows from Home Assistant 2026.3.

## Install

**HACS (recommended):**

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=heathcooper89&repository=ha-calendar-zone&category=integration)

Or in HACS → ⋮ → Custom repositories, add `https://github.com/heathcooper89/ha-calendar-zone` with type Integration. Download Calendar Zone, then restart Home Assistant.

**Manual:** copy `custom_components/calendar_zone` from the [latest release](https://github.com/heathcooper89/ha-calendar-zone/releases) into `/config/custom_components/`, then restart Home Assistant.

Then go to Settings → Devices & services → Add integration → **Calendar Zone**.

## Settings

| Setting | What it does |
|---|---|
| Zone name | Shown in Home Assistant, including as a person's location when they're in the zone. Can be changed any time. |
| Zone entity ID | Set once at setup. Leave blank to base it on the name ("Shift work" → `zone.shift_work`), or type your own, e.g. `partner_work`. Automations refer to the zone by this, so it never changes, even if you rename the zone. |
| Calendar | The calendar to follow. Only events with a location (or coordinates in the description) are used. |
| Radius | Zone radius in metres. |
| Passive | A passive zone triggers automations but doesn't change people's location state. |
| Icon | Zone icon. |
| Delay before moving to the next event | See *How the zone moves*. |
| Only use events whose title contains | Optional. E.g. `work` to ignore a dentist appointment on the same calendar. Case-insensitive. |
| Country to search in | Optional. Limits address lookups to one country. Defaults to Home Assistant's country. |
| Google Maps API key | Optional. With a key, addresses are looked up with the Google Geocoding API. Without one, OpenStreetMap (Nominatim) is used. |

Everything except the entity ID can be changed later via **Configure** on the integration, including the name.

## How the zone moves

1. **An event is happening now** → the zone sits at its location. If events overlap, a timed event beats an all-day one, then the most recently started wins.
2. **Between events** → the zone stays at the previous event for the configured delay, but never more than half of the gap before the next event, then moves to the next event.
   - Ends 9pm, next starts 8am, 60 min delay → moves at 10pm.
   - Ends 1pm, next starts 2pm, 50 min delay → moves at 1:30pm (half the gap).
   - If the next event starts, the zone moves to it immediately.
3. **Nothing coming up** → the zone stays where the last event put it.
4. **No events at all yet** → the zone isn't created until there is one.

The calendar is read 14 days ahead. The zone is re-checked at every event start, end and delay expiry, whenever the calendar entity changes, and every 15 minutes (to catch edited events). The **Calendar Zone: Refresh** action re-checks immediately.

## Where coordinates come from

In this order:

1. Coordinates in the event location, e.g. `-37.8136, 144.9631`, `geo:-37.8136,144.9631`, or a Google Maps link containing `@-37.8136,144.9631`.
2. If the location is empty, coordinates in the event description.
3. Otherwise the location is treated as an address and looked up with Google (if a key is set) or OpenStreetMap. If Google fails (bad key, quota), OpenStreetMap is tried. If OpenStreetMap can't find "Business name, 12 Smith St, Suburb", it retries once without the business name.

Lookups are cached, so the same address is only looked up once. If an address can't be found, **the zone stays where it was** and the Status sensor shows "Location not found". Tip: full addresses with suburb, state and postcode work best with OpenStreetMap.

### Google API key

Create a key in Google Cloud Console with the **Geocoding API** enabled. The key is checked when you save it. Restricting the key to the Geocoding API is a good idea.

## Entities

Besides the zone itself, each calendar zone gets a device with:

- **Current event**: the event the zone is following. Attributes show the location text, start/end, the reason (`active`, `holding`, `upcoming`, `last`), coordinates, and how they were resolved (`coordinates`, `google`, `openstreetmap`) with the address the lookup service matched. Check this after a new address to make sure it found the right place.
- **Status** (diagnostic): `following`, `no_events`, `location_not_found`, `lookup_failed`, `calendar_unavailable`.
- **Next check** (diagnostic): when the zone may next move.

The zone's state is the number of people in it, with `persons` and `device_trackers` attributes, the same as built-in zones.

## Examples

Notify when someone arrives:

```yaml
triggers:
  - trigger: zone
    entity_id: person.alex
    zone: zone.shift_work
    event: enter
actions:
  - action: notify.mobile_app_your_iphone
    data:
      message: "Arrived at {{ state_attr('zone.shift_work', 'event') }}"
```

Waze Travel Time: set the destination to `zone.shift_work`. Waze then gets coordinates rather than an address.

## Good to know

- The zone belongs to this integration, so it doesn't appear under Settings → Areas, labels & zones → Zones and can't be edited there. Change it via the integration's **Configure** instead.
- Renaming a non-passive zone changes the location text people show while in it (e.g. from "Shift work" to "Hospital roster"). Automations using zone triggers or `zone.shift_work` keep working; ones that compare a person's state to the old name need updating.
- When the zone moves, someone already standing inside the new spot shows as "in the zone" after their phone's next location update.
- The iPhone companion app learns about zones from Home Assistant. If arrival notifications are late after the zone moves, opening the app makes it refresh its zone list.
- The last position is saved, so after a restart the zone is back immediately, before the calendar is read again.

## Issues and ideas

Please open an [issue](https://github.com/heathcooper89/ha-calendar-zone/issues). Including your Home Assistant version and the Status sensor's value helps.

## Tests

```bash
pip install -r requirements_test.txt
pytest
```

## Licence

MIT
