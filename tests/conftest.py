"""Test bootstrap for the Kokoro TTS integration.

The integration modules import ``homeassistant`` (and a few third-party libs) at
module load time. Home Assistant is a very heavy dependency, so instead of
installing it we register minimal stub modules in ``sys.modules`` before the
integration is imported. The pure helper logic under test (persona
classification/filtering, language-code resolution, unique-id hashing) does not
touch any real Home Assistant behaviour, so the stubs only need to make the
imports succeed.

Each stub group is only installed when the real top-level package is NOT
importable, so adding ``homeassistant``/``aiohttp``/``voluptuous`` to the test
environment later transparently runs the tests against the real packages
instead of being silently clobbered by these stubs.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

# Make the repo root importable as `custom_components.kokoro_tts...`.
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _importable(name: str) -> bool:
    """True if a real (non-stub) top-level package is installed."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _mod(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _stub_homeassistant() -> None:
    ha = _mod("homeassistant")

    config_entries = _mod("homeassistant.config_entries")

    class ConfigFlow:
        # Absorb `class X(ConfigFlow, domain=...)` keyword.
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    class OptionsFlow:
        pass

    class ConfigEntry:
        pass

    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    config_entries.ConfigEntry = ConfigEntry
    ha.config_entries = config_entries

    const = _mod("homeassistant.const")

    class Platform:
        TTS = "tts"

    const.Platform = Platform

    core = _mod("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    core.callback = lambda func: func

    exceptions = _mod("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

    helpers = _mod("homeassistant.helpers")

    cv = _mod("homeassistant.helpers.config_validation")
    cv.config_entry_only_config_schema = lambda domain: {}
    helpers.config_validation = cv

    selector = _mod("homeassistant.helpers.selector")
    selector.selector = lambda spec: spec
    helpers.selector = selector

    aiohttp_client = _mod("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(hass):
        raise RuntimeError("async_get_clientsession must be patched in tests")

    aiohttp_client.async_get_clientsession = async_get_clientsession
    helpers.aiohttp_client = aiohttp_client

    components = _mod("homeassistant.components")
    tts_pkg = _mod("homeassistant.components.tts")
    tts_entity = _mod("homeassistant.components.tts.entity")

    class TextToSpeechEntity:
        def __init__(self, *args, **kwargs):
            pass

    tts_entity.TextToSpeechEntity = TextToSpeechEntity
    tts_entity.TtsAudioType = tuple
    tts_pkg.entity = tts_entity
    components.tts = tts_pkg
    ha.components = components


def _stub_voluptuous() -> None:
    vol = _mod("voluptuous")

    class _Schema:
        def __init__(self, *args, **kwargs):
            pass

    class _Marker:
        def __init__(self, *args, **kwargs):
            pass

    vol.Schema = _Schema
    vol.Required = _Marker
    vol.Optional = _Marker


def _stub_aiohttp() -> None:
    aiohttp = _mod("aiohttp")

    class ClientTimeout:
        def __init__(self, *args, **kwargs):
            pass

    class ClientError(Exception):
        pass

    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ClientSession = object
    aiohttp.ClientError = ClientError
    aiohttp.ClientSSLError = type("ClientSSLError", (ClientError,), {})
    aiohttp.ClientConnectorError = type("ClientConnectorError", (ClientError,), {})


def _install_stubs() -> None:
    if not _importable("homeassistant"):
        _stub_homeassistant()
    if not _importable("voluptuous"):
        _stub_voluptuous()
    if not _importable("aiohttp"):
        _stub_aiohttp()


_install_stubs()
