"""Kokoro FastAPI discovery calls, shared by the config flow and the entity."""
from __future__ import annotations

import logging

import aiohttp

from .const import CONF_API_KEY, CONF_BASE_URL, auth_headers

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)


async def async_fetch_models(
    session: aiohttp.ClientSession, base_url: str, api_key: str
) -> tuple[dict[str, str], list[str]]:
    """GET /v1/models: return (form errors, model ids).

    Doubles as the connection test, so setup asks the server once, not twice.
    """
    try:
        async with session.get(
            f"{base_url}/v1/models", headers=auth_headers(api_key), timeout=_TIMEOUT
        ) as resp:
            if resp.status == 401:
                return {CONF_API_KEY: "auth_failed"}, []
            if resp.status == 404:
                return {CONF_BASE_URL: "server_not_found"}, []
            if resp.status >= 500:
                return {CONF_BASE_URL: "server_error"}, []
            if resp.status != 200:
                # Reachable, just no model list.
                return {}, []
            data = await resp.json(content_type=None)
    except aiohttp.ClientSSLError:
        return {CONF_BASE_URL: "ssl_error"}, []
    except TimeoutError:
        return {CONF_BASE_URL: "timeout"}, []
    except aiohttp.ClientError:
        return {CONF_BASE_URL: "cannot_connect"}, []
    except ValueError:
        _LOGGER.debug("Unparseable /v1/models response from %s", base_url)
        return {}, []

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}, []
    return {}, [
        str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")
    ]


async def async_fetch_voices(
    session: aiohttp.ClientSession, base_url: str, api_key: str
) -> list[str]:
    """GET /v1/audio/voices: the voice codes the server reports ([] on failure)."""
    try:
        async with session.get(
            f"{base_url}/v1/audio/voices", headers=auth_headers(api_key), timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        _LOGGER.debug("Failed to discover voices from %s", base_url)
        return []

    if isinstance(data, dict):
        voices = data.get("voices", data.get("personas", []))
    else:
        voices = data if isinstance(data, list) else []
    codes: list[str] = []
    for voice in voices:
        if isinstance(voice, str):
            codes.append(voice)
        elif isinstance(voice, dict) and voice.get("id"):
            codes.append(str(voice["id"]))
    return codes
