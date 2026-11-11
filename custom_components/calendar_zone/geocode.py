"""Turn a calendar location into coordinates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim asks apps to identify themselves with a way to contact the author.
USER_AGENT = "HomeAssistant-CalendarZone/1.0.1 (+https://github.com/heathcooper89/ha-calendar-zone)"
TIMEOUT = aiohttp.ClientTimeout(total=15)

SOURCE_COORDINATES = "coordinates"
SOURCE_GOOGLE = "google"
SOURCE_OSM = "openstreetmap"

ATTRIBUTION = {
    SOURCE_GOOGLE: "Location data © Google",
    SOURCE_OSM: "Location data © OpenStreetMap contributors, ODbL",
}

# Google's terms allow coordinates from the Geocoding API to be kept for at
# most 30 days, after which they must be looked up again.
GOOGLE_MAX_AGE = 30 * 24 * 3600

# "-37.8136, 144.9631", "geo:-37.8136,144.9631", Google Maps "@-37.81,144.96"
_COORD_RE = re.compile(
    r"(?<![\d.])(-?\d{1,2}\.\d{2,})\s*,\s*(-?\d{1,3}\.\d{2,})(?![\d.])"
)

# Nominatim allows at most one request per second per application.
_NOMINATIM_MIN_INTERVAL = 1.1


class Throttle:
    """Spaces out requests to a rate-limited service (one per instance)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_call = 0.0


class GeocodeError(Exception):
    """A lookup service could not be reached or refused the request."""


@dataclass(frozen=True)
class GeoResult:
    """A resolved location."""

    latitude: float
    longitude: float
    source: str
    formatted_address: str | None = None
    resolved_at: float = 0.0

    @property
    def expired(self) -> bool:
        """True if the result is older than its service allows keeping it."""
        return (
            self.source == SOURCE_GOOGLE
            and time.time() - self.resolved_at > GOOGLE_MAX_AGE
        )

    @property
    def attribution(self) -> str | None:
        """Credit line required by the service that found the location."""
        return ATTRIBUTION.get(self.source)

    def as_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "source": self.source,
            "formatted_address": self.formatted_address,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeoResult:
        return cls(
            float(data["latitude"]),
            float(data["longitude"]),
            data["source"],
            data.get("formatted_address"),
            float(data.get("resolved_at") or 0.0),
        )


def parse_coordinates(text: str | None) -> tuple[float, float] | None:
    """Return (lat, lon) if the text contains decimal coordinates."""
    if not text:
        return None
    match = _COORD_RE.search(text)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


class Geocoder:
    """Resolve locations with Google (if a key is set) or OpenStreetMap."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        google_api_key: str | None,
        country_code: str | None,
        cache: dict[str, dict[str, Any]] | None = None,
        throttle: Throttle | None = None,
    ) -> None:
        self._session = session
        self._throttle = throttle or Throttle()
        self._google_key = (google_api_key or "").strip() or None
        self._country = (country_code or "").strip().lower() or None
        self.cache: dict[str, dict[str, Any]] = cache if cache is not None else {}

    async def async_resolve(self, text: str) -> GeoResult | None:
        """Resolve text to coordinates. None means the place was not found.

        Raises GeocodeError when no service could be reached.
        """
        coords = parse_coordinates(text)
        if coords:
            return GeoResult(coords[0], coords[1], SOURCE_COORDINATES)

        key = _normalise(text)
        if key in self.cache:
            try:
                cached = GeoResult.from_dict(self.cache[key])
            except (KeyError, TypeError, ValueError):
                cached = None
            if cached is not None and not cached.expired:
                return cached
            del self.cache[key]

        result: GeoResult | None = None
        if self._google_key:
            try:
                result = await self._google(text)
            except GeocodeError as err:
                _LOGGER.warning(
                    "Google geocoding failed (%s), trying OpenStreetMap", err
                )
                result = await self._nominatim_with_fallbacks(text)
        else:
            result = await self._nominatim_with_fallbacks(text)

        if result is not None:
            self.cache[key] = result.as_dict()
            while len(self.cache) > 200:
                self.cache.pop(next(iter(self.cache)))
        return result

    async def _google(self, text: str) -> GeoResult | None:
        params = {"address": text, "key": self._google_key or ""}
        if self._country:
            params["region"] = self._country
        data = await self._get_json(GOOGLE_URL, params)
        try:
            status = data.get("status")
            if status == "ZERO_RESULTS":
                return None
            if status != "OK":
                raise GeocodeError(
                    self._redact(f"{status}: {data.get('error_message', 'no details')}")
                )
            best = data["results"][0]
            loc = best["geometry"]["location"]
            return GeoResult(
                float(loc["lat"]),
                float(loc["lng"]),
                SOURCE_GOOGLE,
                best.get("formatted_address"),
                time.time(),
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as err:
            raise GeocodeError("Unexpected response from Google") from err

    async def _nominatim_with_fallbacks(self, text: str) -> GeoResult | None:
        """Try the full text, then once more without a leading business name.

        Only one leading part is dropped, so a failed street lookup never
        silently falls back to the middle of a suburb.
        """
        parts = [p.strip() for p in text.split(",") if p.strip()]
        attempts = [text]
        if len(parts) >= 3:
            attempts.append(", ".join(parts[1:]))
        for query in attempts:
            result = await self._nominatim(query)
            if result is not None:
                return result
        return None

    async def _nominatim(self, query: str) -> GeoResult | None:
        params = {"q": query, "format": "jsonv2", "limit": "1"}
        if self._country:
            params["countrycodes"] = self._country
        throttle = self._throttle
        async with throttle.lock:
            wait = _NOMINATIM_MIN_INTERVAL - (time.monotonic() - throttle.last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                data = await self._get_json(NOMINATIM_URL, params)
            finally:
                throttle.last_call = time.monotonic()
        if not data:
            return None
        try:
            best = data[0]
            return GeoResult(
                float(best["lat"]),
                float(best["lon"]),
                SOURCE_OSM,
                best.get("display_name"),
                time.time(),
            )
        except (KeyError, IndexError, TypeError, ValueError) as err:
            raise GeocodeError("Unexpected response from OpenStreetMap") from err

    def _redact(self, message: str) -> str:
        """Never let the API key reach logs or the UI."""
        if self._google_key:
            message = message.replace(self._google_key, "**REDACTED**")
        return message

    async def _get_json(self, url: str, params: dict[str, str]) -> Any:
        try:
            async with self._session.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise GeocodeError(f"HTTP {resp.status} from {url}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GeocodeError(self._redact(str(err) or type(err).__name__)) from err


async def async_validate_google_key(
    session: aiohttp.ClientSession, api_key: str
) -> str | None:
    """Return an error string if the key is rejected, else None."""
    geocoder = Geocoder(session, api_key, None)
    try:
        await geocoder._google("Sydney Opera House")
    except GeocodeError as err:
        return str(err)
    return None
