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
from homeassistant.exceptions import ConfigEntryAuthFailed


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
        name="kokoro",
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

    response, _audio = await _drain(_entity(fmt="wav"), ["Hi. "])

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

    await _drain(_entity(), ["Hi. "])

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

    with pytest.raises(ConfigEntryAuthFailed):
        await _drain(_entity(), ["Hi. "])


async def test_stream_server_error_raises_runtime_error(monkeypatch):
    session = _RecordingSession(status=500)
    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: session)

    with pytest.raises(RuntimeError):
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
