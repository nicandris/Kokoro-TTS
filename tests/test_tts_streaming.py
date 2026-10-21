"""Tests for the streaming TTS path merged in from upstream.

Streaming synthesises one request per sentence and concatenates the audio, so
the things worth pinning down are: where sentence boundaries fall, that the
format stays concatenatable, that the tail is never dropped, and that a 401
drives reauth here exactly as it does on the buffered path.
"""
from __future__ import annotations

import pytest

from custom_components.kokoro_tts import tts
from custom_components.kokoro_tts.const import STREAM_SAFE_FORMATS
from custom_components.kokoro_tts.tts import KokoroTTSEntity, split_sentences

from homeassistant.components.tts.entity import TTSAudioRequest
from homeassistant.exceptions import HomeAssistantError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class _FakeResponse:
    def __init__(self, body=b"AUDIO", status=200, text="error-body"):
        self.status = status
        self.content = _FakeContent(body)
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text


class _RecordingSession:
    """Records every POST so per-sentence requests can be asserted on."""

    def __init__(self, status=200, body=b"AUDIO"):
        self.status = status
        self.body = body
        self.posts: list[dict] = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse(body=self.body, status=self.status)

    @property
    def messages(self) -> list[str]:
        return [post["json"]["input"] for post in self.posts]


def _entity(**overrides):
    kwargs = dict(
        unique_id="entry-abc",
        base_url="http://host:8880",
        api_key="secret",
        model="kokoro",
        persona="af_heart",
        speed=1.0,
        fmt="mp3",
        language=None,
    )
    kwargs.update(overrides)
    entity = KokoroTTSEntity(**kwargs)
    entity.hass = object()
    return entity


async def _agen(*chunks: str):
    for chunk in chunks:
        yield chunk


async def _drain(entity, chunks, options=None):
    """Run the streaming path over `chunks` and return the concatenated audio."""
    response = await entity.async_stream_tts_audio(
        TTSAudioRequest(language="en", options=options or {}, message_gen=_agen(*chunks))
    )
    audio = b"".join([chunk async for chunk in response.data_gen])
    return response, audio


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------

def test_split_sentences_returns_complete_and_remainder():
    sentences, remainder = split_sentences("One. Two! Three")
    assert sentences == ["One.", "Two!"]
    assert remainder == "Three"


def test_split_sentences_keeps_decimals_intact():
    # "12.5" must not be treated as a boundary: no whitespace follows the dot.
    sentences, remainder = split_sentences("It costs 12.5 euros")
    assert sentences == []
    assert remainder == "It costs 12.5 euros"


def test_split_sentences_handles_trailing_quotes_and_brackets():
    sentences, _ = split_sentences('He said "go!" and left. ')
    assert sentences == ['He said "go!"', "and left."]


def test_split_sentences_empty_buffer():
    assert split_sentences("") == ([], "")


# ---------------------------------------------------------------------------
# Streaming behaviour
# ---------------------------------------------------------------------------

def test_entity_advertises_streaming_input():
    assert _entity().async_supports_streaming_input() is True


async def test_stream_synthesises_one_request_per_sentence(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    _response, audio = await _drain(_entity(), ["Hello there. ", "How are you? "])

    assert session.messages == ["Hello there.", "How are you?"]
    assert audio == b"AUDIO" * 2


async def test_stream_flushes_unterminated_tail(monkeypatch):
    # The final sentence usually has no trailing whitespace; dropping it would
    # silently truncate the spoken reply.
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    await _drain(_entity(), ["Done. ", "No trailing punctuation"])

    assert session.messages == ["Done.", "No trailing punctuation"]


async def test_stream_reassembles_sentence_split_across_chunks(monkeypatch):
    # The agent streams arbitrary fragments, not whole sentences.
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    await _drain(_entity(), ["Hel", "lo the", "re. Bye."])

    assert session.messages == ["Hello there.", "Bye."]


async def test_stream_falls_back_to_safe_format_for_wav(monkeypatch):
    # wav carries a per-file header, so concatenated sentences would be junk.
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    response, _audio = await _drain(_entity(fmt="wav"), ["Hi. ", "Bye. "])

    assert response.extension in STREAM_SAFE_FORMATS
    assert response.extension == "mp3"
    assert session.posts[0]["json"]["response_format"] == "mp3"


async def test_stream_keeps_safe_format_untouched(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    response, _audio = await _drain(_entity(fmt="opus"), ["Hi. "])

    assert response.extension == "opus"


async def test_stream_requests_set_stream_flag(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    await _drain(_entity(), ["Hi. ", "Bye. "])

    assert session.posts[0]["json"]["stream"] is True


async def test_stream_applies_default_volume_multiplier(monkeypatch):
    # Regression: upstream's streaming path read a hardcoded 1.0 default,
    # which ignored the configured volume slider.
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    await _drain(_entity(volume_multiplier=2.5), ["Hi. "])

    assert session.posts[0]["json"]["volume_multiplier"] == 2.5


async def test_stream_per_call_volume_overrides_default(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    await _drain(_entity(volume_multiplier=2.5), ["Hi. "], {"volume_multiplier": 0.5})

    assert session.posts[0]["json"]["volume_multiplier"] == 0.5


async def test_stream_401_raises_auth_failed(monkeypatch):
    # Regression: upstream's streaming path raised a plain RuntimeError, so a
    # bad API key never triggered Home Assistant's reauth flow.
    session = _RecordingSession(status=401)
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)
    started = []
    entry = type(
        "Entry",
        (),
        {
            "entry_id": "entry-abc",
            "title": "Kokoro TTS (host)",
            "async_start_reauth": lambda self, hass: started.append(hass),
        },
    )()

    with pytest.raises(HomeAssistantError):
        await _drain(_entity(config_entry=entry), ["Hi. "])
    assert len(started) == 1


async def test_stream_server_error_raises_ha_error(monkeypatch):
    session = _RecordingSession(status=500)
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    with pytest.raises(HomeAssistantError):
        await _drain(_entity(), ["Hi. "])


async def test_stream_uses_shared_session(monkeypatch):
    # Regression: streaming must not open its own aiohttp.ClientSession.
    session = _RecordingSession()
    calls: list[object] = []

    def _get_session(hass):
        calls.append(hass)
        return session

    monkeypatch.setattr(tts, "async_get_clientsession", _get_session)
    monkeypatch.setattr(
        tts.aiohttp,
        "ClientSession",
        lambda *a, **k: pytest.fail("streaming opened its own ClientSession"),
    )

    await _drain(_entity(), ["One. ", "Two. "])

    # One shared session for the whole stream, reused across both sentences.
    assert len(calls) == 1
    assert len(session.posts) == 2


# ---------------------------------------------------------------------------
# Full message vs real stream (HA sends both through async_stream_tts_audio)
# ---------------------------------------------------------------------------

async def test_full_message_uses_single_request_in_configured_format(monkeypatch):
    # Regression: HA routes a plain tts.speak here as a one-chunk stream, which
    # forced wav/flac to mp3 and split the message into per-sentence requests.
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    response, audio = await _drain(_entity(fmt="wav"), ["One. Two. Three."])

    assert response.extension == "wav"
    assert session.messages == ["One. Two. Three."]
    assert session.posts[0]["json"]["stream"] is False
    assert audio == b"AUDIO"


async def test_real_stream_still_goes_per_sentence(monkeypatch):
    session = _RecordingSession()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    response, _audio = await _drain(_entity(fmt="flac"), ["One. Tw", "o."])

    assert response.extension == "mp3"
    assert session.messages == ["One.", "Two."]


def test_split_sentences_keeps_abbreviations_and_list_numbers():
    sentences, remainder = split_sentences("Ask Dr. Smith today. Steps: 1. Mix it. 2. Bake it. ")
    assert sentences == ["Ask Dr. Smith today.", "Steps: 1. Mix it.", "2. Bake it."]
    assert remainder == ""


def test_split_sentences_splits_cjk_and_newline_lists():
    sentences, remainder = split_sentences("今日は晴れです。明日は雨！- milk\n- eggs\nbread")
    assert sentences == ["今日は晴れです。", "明日は雨！", "- milk", "- eggs"]
    assert remainder == "bread"


async def test_stream_synthesises_next_sentence_while_current_plays(monkeypatch):
    # Regression: sentences were synthesised strictly in sequence. Sentence 1's
    # audio only arrives once sentence 2's request has been sent.
    import asyncio

    second_posted = asyncio.Event()

    class _GatedContent:
        async def iter_chunked(self, size):
            await second_posted.wait()
            yield b"ONE"

    class _Session(_RecordingSession):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            if len(self.posts) == 1:
                response.content = _GatedContent()
            else:
                second_posted.set()
            return response

    session = _Session()
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    _response, audio = await asyncio.wait_for(
        _drain(_entity(), ["One. ", "Two. ", "Three."]), timeout=2
    )
    assert audio == b"ONE" + b"AUDIO" * 2
    assert session.messages == ["One.", "Two.", "Three."]


async def test_stream_with_no_audio_is_an_ha_error(monkeypatch):
    # Regression: a stream of empty responses "succeeded" with no audio.
    session = _RecordingSession(body=b"")
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)
    with pytest.raises(HomeAssistantError, match="empty audio"):
        await _drain(_entity(), ["One. ", "Two."])


async def test_failed_look_ahead_after_consumer_stops_is_retrieved(monkeypatch):
    # Regression: a look-ahead request that failed after the consumer stopped
    # left "Task exception was never retrieved" in the log.
    import asyncio
    import gc

    import aiohttp

    class _Session(_RecordingSession):
        def post(self, url, **kwargs):
            if self.posts:
                raise aiohttp.ClientError("second request fails")
            return super().post(url, **kwargs)

    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: _Session())
    loop = asyncio.get_running_loop()
    unretrieved = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
    try:
        response = await _entity().async_stream_tts_audio(
            TTSAudioRequest(language="en", options={}, message_gen=_agen("One. ", "Two. ", "Three."))
        )
        data_gen = response.data_gen
        await anext(data_gen)
        for _ in range(5):
            await asyncio.sleep(0)
        await data_gen.aclose()
        del data_gen, response
        for _ in range(5):
            await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)
    assert unretrieved == []
