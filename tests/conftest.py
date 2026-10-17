"""Shared fixtures. The Home Assistant tests need pytest-homeassistant-custom-component."""

import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("pytest_homeassistant_custom_component"):
    import custom_components

    # The test package ships its own custom_components; add this repository's to it.
    custom_components.__path__.append(str(Path(__file__).parents[1] / "custom_components"))

    pytest_plugins = ["pytest_homeassistant_custom_component"]

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Let Home Assistant load the integration from custom_components/."""
        yield
