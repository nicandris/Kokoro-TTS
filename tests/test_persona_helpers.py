"""Tests for persona classification, filtering and unique-id helpers."""
from __future__ import annotations

from custom_components.kokoro_tts.config_flow import (
    _calc_unique_id,
    derive_persona_info,
    filter_personas_by_language_and_sex,
    get_persona_display_name,
    get_persona_select_options,
)


def test_derive_persona_info_known_prefixes():
    assert derive_persona_info("af_heart") == ("American English", "Female", "Heart")
    assert derive_persona_info("bm_george") == ("British English", "Male", "George")
    assert derive_persona_info("zf_xiaoyi") == ("Mandarin Chinese", "Female", "Xiaoyi")
    assert derive_persona_info("pm_alex") == ("Brazilian Portuguese", "Male", "Alex")


def test_derive_persona_info_multiword_name():
    assert derive_persona_info("af_new_voice") == (
        "American English",
        "Female",
        "New Voice",
    )


def test_derive_persona_info_invalid():
    assert derive_persona_info("qf_foo") is None  # unknown language letter
    assert derive_persona_info("ax_foo") is None  # unknown sex letter
    assert derive_persona_info("a") is None  # too short
    assert derive_persona_info("") is None
    assert derive_persona_info("af") is None  # no name component (no underscore)
    assert derive_persona_info("af_") is None  # empty name component


def test_filter_all_includes_unmapped():
    personas = ["af_heart", "af_unknownvoice", "zz_weird"]
    out = filter_personas_by_language_and_sex(personas, "All Languages", "All")
    assert set(out) == {"af_heart", "af_unknownvoice", "zz_weird"}


def test_filter_unmapped_voice_visible_under_language_filter():
    # Regression: a server voice that is NOT in PERSONA_MAPPINGS must still show
    # when a language/sex filter is active. Previously it was hidden unless both
    # filters were "All".
    personas = ["af_heart", "af_brandnew", "bm_george"]
    out = filter_personas_by_language_and_sex(personas, "American English", "Female")
    assert "af_brandnew" in out
    assert "af_heart" in out
    assert "bm_george" not in out


def test_filter_excludes_other_language_and_sex():
    personas = ["af_heart", "am_adam", "bm_george"]
    out = filter_personas_by_language_and_sex(personas, "American English", "Male")
    assert out == ["am_adam"]


def test_filter_unclassifiable_hidden_when_filtered():
    personas = ["af_heart", "zz_weird"]
    out = filter_personas_by_language_and_sex(personas, "American English", "All")
    assert out == ["af_heart"]


def test_persona_display_name_mapped_and_unmapped():
    assert get_persona_display_name("af_heart", "American English", "Female") == "Heart"
    # Unmapped codes fall back to the raw code.
    assert get_persona_display_name("af_brandnew") == "af_brandnew"


def test_select_options_use_code_as_value():
    options = get_persona_select_options(["af_heart", "am_adam"], "All Languages", "All")
    by_label = {o["label"]: o["value"] for o in options}
    assert by_label["Heart (American English, Female)"] == "af_heart"
    assert by_label["Adam (American English, Male)"] == "am_adam"


def test_select_options_resolve_shared_display_names():
    # jf_alpha and hf_alpha both display as "Alpha" but must keep distinct values.
    options = get_persona_select_options(["jf_alpha", "hf_alpha"], "All Languages", "All")
    labels = {o["value"]: o["label"] for o in options}
    assert set(labels) == {"jf_alpha", "hf_alpha"}
    assert labels["jf_alpha"] == "Alpha (Japanese, Female)"
    assert labels["hf_alpha"] == "Alpha (Hindi, Female)"


def test_select_options_filtered_by_language_includes_unmapped():
    options = get_persona_select_options(
        ["af_heart", "jf_alpha", "af_brandnew"], "American English", "All"
    )
    assert {o["value"] for o in options} == {"af_heart", "af_brandnew"}


def test_select_options_empty_uses_blank_value_placeholder():
    options = get_persona_select_options(["jf_alpha"], "American English", "Female")
    assert len(options) == 1
    assert options[0]["value"] == ""


def test_calc_unique_id_stable_and_distinct():
    first = _calc_unique_id("http://host:8880")
    assert first == _calc_unique_id("http://host:8880")
    assert len(first) == 12
    assert first != _calc_unique_id("http://other:8880")
