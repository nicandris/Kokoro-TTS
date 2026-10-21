"""Tests for the discovery calls (connection test, model and voice lists)."""
from __future__ import annotations

import aiohttp
import pytest

from custom_components.kokoro_tts import config_flow as cf
from custom_components.kokoro_tts.api import async_fetch_models, async_fetch_voices


class _Response:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, **kwargs):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("headers")))
        if self.error:
            raise self.error
        return self.response


async def test_models_listed_and_key_sent():
    session = _Session(_Response(data={"data": [{"id": "kokoro"}, {"id": "tts-1"}, "junk"]}))
    assert await async_fetch_models(session, "http://h", "secret") == ({}, ["kokoro", "tts-1"])
    assert session.calls == [("http://h/v1/models", {"Authorization": "Bearer secret"})]


@pytest.mark.parametrize(
    ("status", "errors"),
    [
        (401, {"api_key": "auth_failed"}),
        (404, {"base_url": "server_not_found"}),
        (503, {"base_url": "server_error"}),
        (403, {}),
    ],
)
async def test_models_status_maps_to_form_errors(status, errors):
    assert await async_fetch_models(_Session(_Response(status)), "http://h", "x") == (errors, [])


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (aiohttp.ClientSSLError.__new__(aiohttp.ClientSSLError), "ssl_error"),
        (TimeoutError(), "timeout"),
        (aiohttp.ClientError(), "cannot_connect"),
    ],
)
async def test_models_transport_errors(error, code):
    result = await async_fetch_models(_Session(error=error), "http://h", "x")
    assert result == ({"base_url": code}, [])


async def test_models_bad_json_is_reachable_but_empty():
    assert await async_fetch_models(_Session(_Response(data=ValueError())), "http://h", "x") == ({}, [])


async def test_voices_accept_strings_dicts_and_both_keys():
    session = _Session(_Response(data={"voices": ["af_heart", {"id": "am_adam"}, {"x": 1}]}))
    assert await async_fetch_voices(session, "http://h", "x") == ["af_heart", "am_adam"]
    assert await async_fetch_voices(_Session(_Response(data=["bf_emma"])), "http://h", "x") == ["bf_emma"]
    assert await async_fetch_voices(_Session(_Response(data={"personas": ["jf_alpha"]})), "http://h", "x") == ["jf_alpha"]


async def test_voices_failure_is_empty_and_flow_falls_back_to_builtin():
    session = _Session(error=aiohttp.ClientError())
    assert await async_fetch_voices(session, "http://h", "x") == []
    assert await cf._discover_voices(session, "http://h", "x") == list(cf.PERSONA_MAPPINGS)
