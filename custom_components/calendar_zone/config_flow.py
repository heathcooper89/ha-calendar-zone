"""Config flow for Calendar Zone."""

from __future__ import annotations

from typing import Any

try:  # Home Assistant 2026.10+
    import probatio as vol
except ImportError:  # older releases
    import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    CountrySelector,
    EntitySelector,
    EntitySelectorConfig,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .const import (
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
    DEFAULT_NAME,
    DEFAULT_RADIUS,
    DOMAIN,
)
from .geocode import async_validate_google_key
from .manager import zone_entity_id


def _options_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    def opt(key: str) -> dict[str, Any]:
        value = defaults.get(key)
        return {"suggested_value": value} if value not in (None, "") else {}

    return {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): TextSelector(),
        vol.Required(CONF_CALENDAR, default=defaults.get(CONF_CALENDAR, vol.UNDEFINED)): EntitySelector(
            EntitySelectorConfig(domain="calendar")
        ),
        vol.Required(CONF_RADIUS, default=defaults.get(CONF_RADIUS, DEFAULT_RADIUS)): NumberSelector(
            NumberSelectorConfig(
                min=10, max=50000, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="m"
            )
        ),
        vol.Required(CONF_PASSIVE, default=defaults.get(CONF_PASSIVE, False)): BooleanSelector(),
        vol.Required(CONF_ICON, default=defaults.get(CONF_ICON, DEFAULT_ICON)): IconSelector(),
        vol.Required(CONF_DELAY, default=defaults.get(CONF_DELAY, DEFAULT_DELAY)): NumberSelector(
            NumberSelectorConfig(
                min=0, max=1440, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
            )
        ),
        vol.Optional(CONF_TITLE_FILTER, description=opt(CONF_TITLE_FILTER)): TextSelector(),
        vol.Optional(CONF_COUNTRY, description=opt(CONF_COUNTRY)): CountrySelector(),
        vol.Optional(CONF_GOOGLE_API_KEY, description=opt(CONF_GOOGLE_API_KEY)): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }


async def _validate_key(flow: ConfigFlow | OptionsFlow, user_input: dict[str, Any]) -> dict[str, str]:
    key = (user_input.get(CONF_GOOGLE_API_KEY) or "").strip()
    if not key:
        return {}
    if await async_validate_google_key(async_get_clientsession(flow.hass), key):
        return {CONF_GOOGLE_API_KEY: "invalid_api_key"}
    return {}


def _parse_zone_id(raw: str | None, name: str) -> tuple[str, str | None]:
    """Return (object_id, error). Blank means: derive it from the name."""
    value = (raw or "").strip().lower().removeprefix("zone.")
    if not value:
        value = slugify(name)
    elif slugify(value) != value:
        return value, "invalid_zone_id"
    if not value or value == "home":
        return value, "invalid_zone_id"
    return value, None


class CalendarZoneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of a calendar zone."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            object_id, error = _parse_zone_id(user_input.get(CONF_ZONE_ID), name)
            if error:
                errors[CONF_ZONE_ID] = error
            else:
                await self.async_set_unique_id(object_id)
                self._abort_if_unique_id_configured()
                if self.hass.states.get(zone_entity_id(object_id)) is not None:
                    errors[CONF_ZONE_ID] = "zone_exists"
                else:
                    errors = await _validate_key(self, user_input)
            if not errors:
                options = {k: v for k, v in user_input.items() if k != CONF_ZONE_ID}
                options[CONF_NAME] = name
                return self.async_create_entry(
                    title=name, data={CONF_ZONE_ID: object_id}, options=options
                )

        defaults = dict(user_input or {})
        if CONF_COUNTRY not in defaults and self.hass.config.country:
            defaults[CONF_COUNTRY] = self.hass.config.country
        schema = _options_schema(defaults)
        # The entity ID is chosen once, straight after the name.
        items = list(schema.items())
        zone_id_field = vol.Optional(
            CONF_ZONE_ID,
            description=(
                {"suggested_value": defaults[CONF_ZONE_ID]}
                if defaults.get(CONF_ZONE_ID)
                else {}
            ),
        )
        items.insert(1, (zone_id_field, TextSelector()))
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(dict(items)), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CalendarZoneOptionsFlow()


class CalendarZoneOptionsFlow(OptionsFlow):
    """Change a calendar zone's settings, including its name."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_NAME] = user_input[CONF_NAME].strip()
            if not user_input[CONF_NAME]:
                errors[CONF_NAME] = "name_required"
            else:
                errors = await _validate_key(self, user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        defaults = dict(user_input or self.config_entry.options)
        defaults.setdefault(CONF_NAME, self.config_entry.title)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_options_schema(defaults)),
            description_placeholders={
                "entity_id": zone_entity_id(
                    self.config_entry.data.get(CONF_ZONE_ID)
                    or slugify(self.config_entry.data.get(CONF_NAME, self.config_entry.title))
                )
            },
            errors=errors,
        )
