"""Tests for the TTS entity's pure helpers (lang-code, error map, unique-id)."""
from __future__ import annotations

from custom_components.kokoro_tts.tts import KokoroTTSEntity


def _entity(**overrides):
    kwargs = dict(
        unique_id="entry-abc",
        name="kokoro",
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


def test_supported_language_follows_persona():
    # An English persona advertises "en"; a Japanese persona advertises "ja".
    assert _entity(persona="af_heart")._attr_supported_languages == ["en"]
    entity = _entity(persona="jf_alpha")
    assert entity._attr_supported_languages == ["ja"]
    assert entity._attr_default_language == "ja"


def test_supported_language_uses_configured_accent_override():
    # The configured Voice Accent wins over the persona prefix.
    entity = _entity(persona="af_heart", language="Mandarin Chinese")
    assert entity._attr_supported_languages == ["zh"]
    assert entity._attr_default_language == "zh"


def test_unique_id_comes_from_entry_id():
    # Two entities from different entries must not collide.
    assert _entity(unique_id="entry-1")._attr_unique_id == "entry-1"
    assert _entity(unique_id="entry-2")._attr_unique_id == "entry-2"


def test_lang_code_prefers_configured_language():
    entity = _entity(language="British English")
    # Configured language wins over the persona prefix ("af_" -> "a").
    assert entity._get_lang_code("af_heart") == "b"


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
