"""Tests for config-entry setup wiring (the options-reload listener)."""
from __future__ import annotations

from custom_components.kokoro_tts import async_reload_entry, async_setup_entry


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
        self.listener = None
        self.unload_callbacks = []

    def add_update_listener(self, callback):
        self.listener = callback
        return "remove-handle"

    def async_on_unload(self, handle):
        self.unload_callbacks.append(handle)


async def test_setup_entry_registers_options_reload_listener():
    hass = _FakeHass()
    entry = _FakeEntry()

    result = await async_setup_entry(hass, entry)

    assert result is True
    # The options-update listener must be wired to async_reload_entry so that
    # changing options rebuilds the TTS entity without a restart...
    assert entry.listener is async_reload_entry
    # ...and its removal must be registered for cleanup on unload.
    assert "remove-handle" in entry.unload_callbacks
    # The TTS platform is forwarded (Platform.TTS == "tts").
    assert hass.config_entries.forwarded == ["tts"]
