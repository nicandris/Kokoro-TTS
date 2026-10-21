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


class _TtsEntry:
    entry_id = "entry-1"
    title = "Kokoro TTS (host)"
    options = {}

    def __init__(self, base_url, api_key):
        self.data = {"base_url": base_url, "api_key": api_key, "persona": "af_heart"}

    def async_create_background_task(self, hass, target, name):
        target.close()


async def test_api_key_over_plain_http_is_warned_about(caplog):
    from custom_components.kokoro_tts import tts

    added = []
    await tts.async_setup_entry(None, _TtsEntry("http://host:8880", "secret"), added.extend)
    assert "cleartext" in caplog.text and len(added) == 1

    caplog.clear()
    await tts.async_setup_entry(None, _TtsEntry("https://host:8880", "secret"), added.extend)
    await tts.async_setup_entry(None, _TtsEntry("http://host:8880", "not-needed"), added.extend)
    assert "cleartext" not in caplog.text


async def test_cleartext_warning_logs_the_host_only(caplog):
    from custom_components.kokoro_tts import tts

    await tts.async_setup_entry(None, _TtsEntry("http://bob:hunter2@host:8880", "secret"), [].extend)
    assert "host" in caplog.text
    assert "hunter2" not in caplog.text and "bob" not in caplog.text
