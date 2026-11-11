from pathlib import Path

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

CUSTOM_COMPONENTS = str(Path(__file__).parents[1] / "custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant find this repository's integration.

    The test harness has its own custom_components package, so add this
    repository's folder to it rather than fighting over the import.
    """
    import custom_components

    if CUSTOM_COMPONENTS not in custom_components.__path__:
        custom_components.__path__.append(CUSTOM_COMPONENTS)
    yield


@pytest.fixture(autouse=True)
def no_nominatim_throttle(monkeypatch):
    """The 1 request/second pause never ends under a frozen test clock."""
    from custom_components.calendar_zone import geocode

    monkeypatch.setattr(geocode, "_NOMINATIM_MIN_INTERVAL", 0)
