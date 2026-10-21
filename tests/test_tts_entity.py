"""Tests for the TTS entity's pure helpers (lang-code, error map, unique-id)."""
from __future__ import annotations

from custom_components.kokoro_tts.const import DEFAULT_HA_LANGUAGE, SUPPORTED_LANGUAGES
from custom_components.kokoro_tts.tts import KokoroTTSEntity


def _entity(**overrides):
    kwargs = dict(
        unique_id="entry-abc",
        base_url="http://host:8880",
        api_key="not-needed",
        model="kokoro",
        persona="af_heart",
        speed=1.0,
        fmt="mp3",
        language=None,
    )
    kwargs.update(overrides)
    return KokoroTTSEntity(**kwargs)


def test_supported_languages_covers_every_kokoro_language():
    # Every language must be advertised regardless of the configured voice:
    # HA hides the entity from any pipeline whose language is not listed.
    entity = _entity(persona="af_heart")
    assert entity._attr_supported_languages == SUPPORTED_LANGUAGES
    assert {"en", "en-GB", "ja", "zh", "pt-BR"} <= set(entity._attr_supported_languages)
    # A Japanese voice must not narrow the advertised set.
    assert _entity(persona="jf_alpha")._attr_supported_languages == SUPPORTED_LANGUAGES


def test_default_language_follows_persona():
    # The *default* still tracks the configured voice, so a Japanese persona
    # lands on "ja" rather than the generic "en".
    assert _entity(persona="af_heart")._attr_default_language == "en"
    assert _entity(persona="jf_alpha")._attr_default_language == "ja"
    # British and Brazilian voices use the regional codes HA advertises.
    assert _entity(persona="bf_emma")._attr_default_language == "en-GB"
    assert _entity(persona="pf_dora")._attr_default_language == "pt-BR"


def test_default_language_follows_voice_over_accent_filter():
    # The accent filter only narrows the picker; the voice decides.
    entity = _entity(persona="af_heart", language="Mandarin Chinese")
    assert entity._attr_default_language == "en"
    # With no classifiable voice the filter is the fallback.
    assert _entity(persona="custom", language="Mandarin Chinese")._attr_default_language == "zh"


def test_default_language_is_always_advertised():
    # A default outside supported_languages would make HA drop the entity.
    for persona in ("af_heart", "bf_emma", "jf_alpha", "pf_dora", "zm_yunxi"):
        entity = _entity(persona=persona)
        assert entity._attr_default_language in entity._attr_supported_languages


def test_default_language_falls_back_when_persona_unknown():
    # A blended or unrecognised voice must still yield a usable default.
    assert _entity(persona=None)._attr_default_language == DEFAULT_HA_LANGUAGE
    assert _entity(persona="qq_nonsense")._attr_default_language == DEFAULT_HA_LANGUAGE


def test_unique_id_comes_from_entry_id():
    # Two entities from different entries must not collide.
    assert _entity(unique_id="entry-1")._attr_unique_id == "entry-1"
    assert _entity(unique_id="entry-2")._attr_unique_id == "entry-2"


def test_lang_code_prefers_the_voice_over_the_accent_filter():
    # Regression: a Japanese voice under an English filter got English phonemes.
    entity = _entity(language="American English")
    assert entity._get_lang_code("jf_alpha") == "j"
    assert entity._get_lang_code("af_bella+af_sky") == "a"


def test_lang_code_uses_request_language_then_filter_for_unknown_voices():
    entity = _entity(language="Spanish")
    assert entity._get_lang_code("custom_voice", "ja") == "j"
    assert entity._get_lang_code("custom_voice", "de") == "e"
    assert entity._get_lang_code("custom_voice") == "e"


def test_lang_code_never_sends_an_unknown_letter():
    # Regression: the first letter was sent unchecked ("custom" -> "c").
    entity = _entity(language=None)
    assert entity._get_lang_code("custom") is None
    assert entity._get_lang_code("qf_foo") is None


def test_lang_code_falls_back_to_persona_prefix():
    entity = _entity(language=None)
    assert entity._get_lang_code("jf_alpha") == "j"


def test_lang_code_none_when_unknown():
    entity = _entity(language=None)
    assert entity._get_lang_code(None) is None


def test_lang_code_ignores_all_languages_default():
    # "All Languages" is not a real lang_code; should fall back to the prefix.
    entity = _entity(language="All Languages")
    assert entity._get_lang_code("zm_yunxi") == "z"


def test_handle_http_error_known_and_unknown():
    assert "Authentication failed" in KokoroTTSEntity._handle_http_error(401, "")
    assert KokoroTTSEntity._handle_http_error(418, "teapot").startswith("HTTP 418")


def test_voice_is_a_supported_option():
    # Assist pipelines send their voice under HA's standard "voice" key;
    # HA rejects any option not listed here before the entity sees it.
    assert "voice" in _entity()._attr_supported_options


def test_supported_voices_lists_only_the_requested_language():
    voices = _entity().async_get_supported_voices("en-GB")
    assert voices and all(v.voice_id.startswith("b") for v in voices)


def test_supported_voices_puts_configured_persona_first():
    # HA hands a new pipeline voices[0]; it must be the configured persona.
    assert _entity(persona="am_michael").async_get_supported_voices("en")[0].voice_id == "am_michael"
    # A persona from another language leaves the plain name order.
    voices = _entity(persona="jf_alpha").async_get_supported_voices("en")
    assert [v.name for v in voices] == sorted(v.name for v in voices)


def test_supported_voices_none_for_unknown_language():
    assert _entity().async_get_supported_voices("de") is None


def test_default_options_feed_the_cache_key():
    # HA hashes default_options merged with per-call options into the cache
    # key; a different voice/speed/format must give a different key.
    a = _entity(persona="af_heart", speed=1.0, fmt="mp3")._attr_default_options
    assert a == {"voice": "af_heart", "speed": 1.0, "format": "mp3", "volume_multiplier": 1.0}
    assert _entity(persona="am_adam")._attr_default_options != a
    assert _entity(speed=1.5)._attr_default_options != a
    assert _entity(fmt="wav")._attr_default_options != a


def test_pipeline_voice_overrides_default_voice():
    # HA merges a pipeline's "voice" over default_options["voice"].
    entity = _entity(persona="af_heart")
    merged = {**entity._attr_default_options, "voice": "jf_alpha"}
    assert entity._resolve_options(merged)["persona"] == "jf_alpha"
    # An explicit per-call persona still wins.
    assert entity._resolve_options({**merged, "persona": "bf_emma"})["persona"] == "bf_emma"


def test_supported_voices_put_a_configured_blend_first():
    # Regression: a blend is not in the static list, so Assist got "Adam".
    voices = _entity(persona="af_bella+af_sky").async_get_supported_voices("en")
    assert voices[0].voice_id == "af_bella+af_sky"
    # Not offered under an unrelated language.
    assert "af_bella+af_sky" not in [v.voice_id for v in _entity(persona="af_bella+af_sky").async_get_supported_voices("ja")]


def test_supported_voices_include_server_only_voices():
    entity = _entity(persona="af_brandnew")
    entity._server_voices = ["af_brandnew", "jf_newvoice", "weird"]
    en = [v.voice_id for v in entity.async_get_supported_voices("en")]
    assert en[0] == "af_brandnew"
    assert "jf_newvoice" in [v.voice_id for v in entity.async_get_supported_voices("ja")]
    assert "weird" not in en


async def test_refresh_voices_uses_shared_session(monkeypatch):
    from custom_components.kokoro_tts import tts

    async def _fake_fetch(session, base_url, api_key):
        assert session == "shared"
        return ["af_brandnew"]

    monkeypatch.setattr(tts, "async_get_clientsession", lambda hass: "shared")
    monkeypatch.setattr(tts, "async_fetch_voices", _fake_fetch)
    entity = _entity()
    entity.hass = object()
    await entity.async_refresh_voices()
    assert entity._server_voices == ["af_brandnew"]


def test_entity_is_named_after_a_per_entry_device():
    # Regression: no has_entity_name/device_info, so a second server became
    # tts.kokoro_2 instead of being named after its entry.
    class _Entry:
        entry_id = "entry-9"
        title = "Kokoro TTS (kitchen)"

    entity = _entity(config_entry=_Entry())
    assert entity._attr_has_entity_name is True
    assert entity._attr_name is None
    info = entity._attr_device_info
    assert info["identifiers"] == {("kokoro_tts", "entry-9")}
    assert info["name"] == "Kokoro TTS (kitchen)"
