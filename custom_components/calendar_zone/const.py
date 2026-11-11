"""Constants for Calendar Zone."""

from datetime import timedelta

DOMAIN = "calendar_zone"

CONF_ZONE_ID = "zone_id"
CONF_CALENDAR = "calendar_entity"
CONF_RADIUS = "radius"
CONF_PASSIVE = "passive"
CONF_ICON = "icon"
CONF_DELAY = "delay_minutes"
CONF_TITLE_FILTER = "title_filter"
CONF_GOOGLE_API_KEY = "google_api_key"
CONF_COUNTRY = "country_code"

DEFAULT_NAME = "Calendar zone"
DEFAULT_RADIUS = 100
DEFAULT_ICON = "mdi:map-marker-radius"
DEFAULT_DELAY = 0

LOOKAHEAD = timedelta(days=14)
POLL_INTERVAL = timedelta(minutes=15)

ATTR_EVENT = "event"
ATTR_LOCATION = "location"

SERVICE_REFRESH = "refresh"

STATUS_FOLLOWING = "following"
STATUS_NO_EVENTS = "no_events"
STATUS_NOT_FOUND = "location_not_found"
STATUS_LOOKUP_FAILED = "lookup_failed"
STATUS_CALENDAR_UNAVAILABLE = "calendar_unavailable"
STATUS_STARTING = "starting"
