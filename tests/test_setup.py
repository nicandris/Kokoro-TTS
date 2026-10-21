"""Tests for config-entry setup wiring."""
from __future__ import annotations

from custom_components.kokoro_tts import async_setup_entry
from custom_components.kokoro_tts.config_flow import KokoroOptionsFlow

from homeassistant.config_entries import OptionsFlowWithReload


class _FakeConfigEntries:
    def __init__(self):
        self.forwarded = None

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded = list(platforms)


class _FakeHass:
    def __init__(self):
        self.config_entries = _FakeConfigEntries()


class _FakeEntry:
    entry_id = "entry-1"

    def __init__(self):
        self.listeners = []

    def add_update_listener(self, callback):
        self.listeners.append(callback)
        return lambda: None

    def async_on_unload(self, handle):
        pass


async def test_setup_entry_forwards_tts_without_an_update_listener():
    # Regression: an update listener reloaded the entry a second time after
    # reconfigure/reauth already did; HA deprecates the combination.
    hass = _FakeHass()
    entry = _FakeEntry()

    assert await async_setup_entry(hass, entry) is True
    assert entry.listeners == []
    assert hass.config_entries.forwarded == ["tts"]


def test_options_flow_reloads_the_entry_itself():
    # With no listener, saving options must still rebuild the TTS entity.
    assert issubclass(KokoroOptionsFlow, OptionsFlowWithReload)
