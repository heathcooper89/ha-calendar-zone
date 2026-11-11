"""Calendar Zone: a zone that follows the location of calendar events."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_REFRESH
from .manager import CalendarZoneManager

PLATFORMS = [Platform.SENSOR]

# Set up from the UI only; no configuration.yaml options.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CalendarZoneConfigEntry = ConfigEntry[CalendarZoneManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the refresh action."""

    async def _refresh(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CalendarZoneConfigEntry) -> bool:
    """Set up a calendar zone from a config entry."""
    manager = CalendarZoneManager(hass, entry)
    if hass.states.get(manager.entity_id) is not None:
        raise ConfigEntryError(
            f"{manager.entity_id} already exists. Rename or delete the other "
            "zone, or give this calendar zone a different name."
        )
    if entry.title != manager.name:
        # Keep the integration entry's title in step with the zone name.
        hass.config_entries.async_update_entry(entry, title=manager.name)
    await manager.async_start()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CalendarZoneConfigEntry) -> bool:
    """Unload a config entry and remove its zone."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _options_updated(hass: HomeAssistant, entry: CalendarZoneConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
