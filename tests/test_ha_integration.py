"""End to end through real Home Assistant core against a fake Kokoro server.

Only runs in the real-HA CI job: the stubs in conftest.py cannot catch what
HA's TTS manager checks (it refused every message when the entity had no name).
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant.bootstrap")

from aiohttp import web  # noqa: E402

from homeassistant import auth, bootstrap, config_entries, core, loader  # noqa: E402
from homeassistant.components import tts  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture
async def kokoro_server(unused_tcp_port_factory):
    calls: list[dict] = []

    async def speech(request):
        body = await request.json()
        calls.append(body)
        return web.Response(body=b"ID3" + b"\x01" * 2000)

    async def models(request):
        return web.json_response({"data": [{"id": "kokoro"}]})

    async def voices(request):
        return web.json_response({"voices": ["af_heart", "bf_emma"]})

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_get("/v1/audio/voices", voices)
    app.router.add_post("/v1/audio/speech", speech)
    runner = web.AppRunner(app)
    await runner.setup()
    port = unused_tcp_port_factory()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    yield f"http://127.0.0.1:{port}", calls
    await runner.cleanup()


@pytest.fixture
async def hass(tmp_path, unused_tcp_port_factory):
    (tmp_path / "custom_components").mkdir()
    (tmp_path / "custom_components" / "kokoro_tts").symlink_to(
        ROOT / "custom_components" / "kokoro_tts"
    )
    hass = core.HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await bootstrap.async_load_base_functionality(hass)
    hass.auth = await auth.auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    port = unused_tcp_port_factory()
    assert await async_setup_component(hass, "http", {"http": {"server_port": port}})
    assert await async_setup_component(hass, "tts", {})
    await hass.async_start()
    yield hass
    await hass.async_stop(force=True)


async def _add_entry(hass, base_url):
    flow = await hass.config_entries.flow.async_init(
        "kokoro_tts", context={"source": "user"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"base_url": base_url, "api_key": "not-needed"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"model": "kokoro", "language": "All Languages", "sex": "All"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"persona": "bf_emma", "speed": 1.0, "format": "mp3", "volume_multiplier": 1.0},
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()


async def _speak(hass, entity_id, message=None, chunks=None) -> bytes:
    stream = tts.async_create_stream(hass, entity_id, None, {})
    if chunks:
        async def gen():
            for chunk in chunks:
                yield chunk
                await asyncio.sleep(0)

        stream.async_set_message_stream(gen())
    else:
        stream.async_set_message(message)
    return b"".join([chunk async for chunk in stream.async_stream_result()])


async def test_message_and_stream_through_home_assistant(hass, kokoro_server):
    # Regression: has_entity_name with no name left entity.name None, so HA's
    # TTS manager raised "TTS engine name is not set" on every message.
    base_url, calls = kokoro_server
    await _add_entry(hass, base_url)
    entity_id = next(
        state.entity_id for state in hass.states.async_all("tts")
        if state.entity_id.startswith("tts.kokoro")
    )

    audio = await _speak(hass, entity_id, message="Hello there. How are you?")
    assert audio.startswith(b"ID3")
    assert [call["stream"] for call in calls] == [False]
    assert calls[0]["lang_code"] == "b"

    calls.clear()
    audio = await _speak(hass, entity_id, chunks=["Dr. Smith said hi. ", "The end."])
    assert audio
    assert [call["input"] for call in calls] == ["Dr. Smith said hi.", "The end."]
