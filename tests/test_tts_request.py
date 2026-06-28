"""Async tests for the TTS request flow (shared session, 401 -> reauth)."""
from __future__ import annotations

import pytest

from custom_components.kokoro_tts import tts
from custom_components.kokoro_tts.tts import KokoroTTSEntity

# Resolves to the real package or the conftest stub, whichever is installed;
# tts.py raises the same class, so isinstance/pytest.raises matches either way.
from homeassistant.exceptions import ConfigEntryAuthFailed


class _FakeResponse:
    def __init__(self, status=200, body=b"AUDIO", content_type="audio/mpeg",
                 json_data=None, text="error-body"):
        self.status = status
        self._body = body
        self.headers = {"content-type": content_type}
        self._json = json_data
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text

    async def read(self):
        return self._body

    async def json(self):
        return self._json


class _FakeSession:
    """Mimics the subset of aiohttp.ClientSession the entity uses."""

    def __init__(self, response):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response

    def get(self, *args, **kwargs):
        return self._response


def _entity():
    entity = KokoroTTSEntity(
        unique_id="entry-abc",
        name="kokoro",
        base_url="http://host:8880",
        api_key="secret",
        model="kokoro",
        persona="af_heart",
        speed=1.0,
        fmt="mp3",
        language=None,
    )
    # self.hass is only handed to the (patched) async_get_clientsession.
    entity.hass = object()
    return entity


async def test_get_tts_audio_happy_path(monkeypatch):
    session = _FakeSession(_FakeResponse(status=200, body=b"AUDIO"))
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    fmt, audio = await _entity().async_get_tts_audio("hello", "en")

    assert fmt == "mp3"
    assert audio == b"AUDIO"


async def test_get_tts_audio_401_raises_auth_failed(monkeypatch):
    session = _FakeSession(_FakeResponse(status=401, text="nope"))
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    with pytest.raises(ConfigEntryAuthFailed):
        await _entity().async_get_tts_audio("hello", "en")


async def test_get_tts_audio_server_error_raises_runtime_error(monkeypatch):
    session = _FakeSession(_FakeResponse(status=500, text="boom"))
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    with pytest.raises(RuntimeError):
        await _entity().async_get_tts_audio("hello", "en")


async def test_get_tts_audio_empty_message_rejected():
    # Validated before any network call, so no session patching needed.
    with pytest.raises(ValueError):
        await _entity().async_get_tts_audio("   ", "en")
