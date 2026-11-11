"""Status sensors for a calendar zone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CalendarZoneConfigEntry
from .const import DOMAIN
from .manager import CalendarZoneManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CalendarZoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(
        [
            CurrentEventSensor(manager),
            StatusSensor(manager),
            NextCheckSensor(manager),
        ]
    )


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: CalendarZoneManager, key: str) -> None:
        self.manager = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name=manager.name,
            entry_type=DeviceEntryType.SERVICE,
            model="Calendar zone",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))


class CurrentEventSensor(_Base):
    """The calendar event the zone is following."""

    _attr_icon = "mdi:calendar-account"

    def __init__(self, manager: CalendarZoneManager) -> None:
        super().__init__(manager, "event")

    @property
    def native_value(self) -> str | None:
        return (self.manager.event or {}).get("summary")

    @property
    def attribution(self) -> str | None:
        """Credit the service that found the location, as its licence requires."""
        pos = self.manager.position
        return pos.attribution if pos else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self.manager
        event = m.event or {}
        pos = m.position
        return {
            "zone": m.entity_id,
            "location": event.get("location"),
            "start": event.get("start"),
            "end": event.get("end"),
            "reason": m.reason,
            "latitude": pos.latitude if pos else None,
            "longitude": pos.longitude if pos else None,
            "resolved_by": pos.source if pos else None,
            "resolved_address": pos.formatted_address if pos else None,
        }


class StatusSensor(_Base):
    """Whether the last update worked."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    def __init__(self, manager: CalendarZoneManager) -> None:
        super().__init__(manager, "status")
        self._attr_options = [
            "starting",
            "following",
            "no_events",
            "location_not_found",
            "lookup_failed",
            "calendar_unavailable",
        ]

    @property
    def native_value(self) -> str:
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self.manager.last_refresh
        return {"last_refresh": last.isoformat() if last else None}


class NextCheckSensor(_Base):
    """When the zone may next move."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager: CalendarZoneManager) -> None:
        super().__init__(manager, "next_change")

    @property
    def native_value(self):
        return self.manager.next_check
