"""Tests for the two-step Filters -> Persona config and options flows.

The wizard was split into two steps upstream while this fork independently
changed the persona selector to carry technical codes as option *values*.
These tests pin the merged contract: filters persist across the step boundary,
the persona is stored as the code the user picked (never a reverse-mapped
display name), and the "Change Voice Accent / Sex" round trip keeps the audio
settings while dropping a persona that may no longer match.
"""
from __future__ import annotations

import pytest

from custom_components.kokoro_tts import config_flow as cf
from custom_components.kokoro_tts.config_flow import (
    CONF_CHANGE_FILTERS,
    KokoroConfigFlow,
    KokoroOptionsFlow,
)
from custom_components.kokoro_tts.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_FORMAT,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_PERSONA,
    CONF_SEX,
    CONF_SPEED,
    CONF_VOLUME_MULTIPLIER,
)

PERSONAS = ["af_heart", "am_adam", "bf_emma", "jf_alpha", "hf_alpha", "af_brandnew"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Discovery is exercised elsewhere; here it always succeeds instantly."""

    async def _fake_models(session, base_url, api_key):
        return {}, ["kokoro"]

    async def _fake_voices(session, base_url, api_key):
        return list(PERSONAS)

    monkeypatch.setattr(cf, "async_fetch_models", _fake_models)
    monkeypatch.setattr(cf, "async_fetch_voices", _fake_voices)
    monkeypatch.setattr(cf, "async_get_clientsession", lambda hass: None)


# ---------------------------------------------------------------------------
# Flow harnesses: the conftest HA stubs are bare classes, so the form/entry
# helpers the flows call are recorded here instead.
# ---------------------------------------------------------------------------

class _FlowRecorder:
    hass = None

    def __init__(self):
        self.forms = []
        self.created = None

    def async_show_form(self, **kwargs):
        self.forms.append(kwargs)
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        self.created = kwargs
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


class _ConfigFlow(_FlowRecorder, KokoroConfigFlow):
    def __init__(self):
        _FlowRecorder.__init__(self)
        KokoroConfigFlow.__init__(self)
        self._base_info = {
            CONF_BASE_URL: "http://host:8880",
            CONF_API_KEY: "not-needed",
        }

    async def async_set_unique_id(self, unique_id):
        # Real ConfigFlow exposes unique_id as a read-only property, so record
        # it under a different name rather than shadowing the attribute.
        self.assigned_unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None


class _FakeEntry:
    def __init__(self, data, options=None):
        self.data = data
        self.options = options or {}


class _OptionsFlow(_FlowRecorder, KokoroOptionsFlow):
    def __init__(self, entry):
        _FlowRecorder.__init__(self)
        KokoroOptionsFlow.__init__(self)
        self._entry_obj = entry

    @property
    def config_entry(self):
        return self._entry_obj


def _entry(**overrides):
    data = {
        CONF_BASE_URL: "http://host:8880",
        CONF_API_KEY: "not-needed",
        CONF_MODEL: "kokoro",
        CONF_LANGUAGE: "American English",
        CONF_SEX: "Female",
        CONF_PERSONA: "af_heart",
        CONF_SPEED: 1.0,
        CONF_FORMAT: "mp3",
        CONF_VOLUME_MULTIPLIER: 1.0,
    }
    data.update(overrides)
    return _FakeEntry(data)


def _schema_defaults(form):
    """Map field name -> default from a recorded form's schema."""
    return form["data_schema"]


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

async def test_filters_step_advances_to_persona_step():
    flow = _ConfigFlow()
    result = await flow.async_step_filters(
        {CONF_MODEL: "kokoro", CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"}
    )
    assert result["step_id"] == "persona"
    # Filters must survive the step boundary; the persona list depends on them.
    assert flow._filters[CONF_LANGUAGE] == "Japanese"
    assert flow._filters[CONF_SEX] == "Female"


async def test_persona_step_stores_filters_and_technical_code():
    flow = _ConfigFlow()
    await flow.async_step_filters(
        {CONF_MODEL: "kokoro", CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"}
    )
    await flow.async_step_persona(
        {CONF_PERSONA: "jf_alpha", CONF_SPEED: 1.5, CONF_FORMAT: "opus"}
    )

    data = flow.created["data"]
    # Persona is stored exactly as selected - no display-name round trip, which
    # is what previously mapped "Alpha" onto the wrong language's voice.
    assert data[CONF_PERSONA] == "jf_alpha"
    # Filters chosen in the previous step are persisted onto the entry.
    assert data[CONF_LANGUAGE] == "Japanese"
    assert data[CONF_SEX] == "Female"
    # Base connection info is still merged in.
    assert data[CONF_BASE_URL] == "http://host:8880"


async def test_persona_step_rejects_empty_persona():
    flow = _ConfigFlow()
    await flow.async_step_filters({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    result = await flow.async_step_persona({CONF_PERSONA: "", CONF_SPEED: 1.0})

    assert result["errors"] == {CONF_PERSONA: "persona_required"}
    assert flow.created is None


async def test_change_filters_returns_to_filters_and_drops_persona():
    flow = _ConfigFlow()
    await flow.async_step_filters({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    result = await flow.async_step_persona(
        {
            CONF_CHANGE_FILTERS: True,
            CONF_PERSONA: "jf_alpha",
            CONF_SPEED: 2.0,
            CONF_FORMAT: "flac",
        }
    )

    assert result["step_id"] == "filters"
    assert flow.created is None
    # Audio settings are kept for when the user comes back...
    assert flow._persona_prefill[CONF_SPEED] == 2.0
    assert flow._persona_prefill[CONF_FORMAT] == "flac"
    # ...but the persona is dropped: it may not match the next filter choice.
    assert CONF_PERSONA not in flow._persona_prefill


async def test_change_filters_flag_never_saved_to_entry():
    flow = _ConfigFlow()
    await flow.async_step_filters({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    await flow.async_step_persona(
        {CONF_CHANGE_FILTERS: False, CONF_PERSONA: "jf_alpha", CONF_SPEED: 1.0}
    )
    assert CONF_CHANGE_FILTERS not in flow.created["data"]


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

def test_options_flow_constructs_without_config_entry():
    # Home Assistant supplies config_entry on the base class; passing it to
    # __init__ is deprecated, and async_get_options_flow calls this with no args.
    assert KokoroOptionsFlow() is not None


async def test_options_init_step_advances_to_persona():
    flow = _OptionsFlow(_entry())
    result = await flow.async_step_init(
        {CONF_MODEL: "kokoro", CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"}
    )
    assert result["step_id"] == "persona"


async def test_options_init_prefills_without_sample_rate_keyerror():
    # Regression: the persona prefill read DEFAULTS[CONF_SAMPLE_RATE], a key
    # that does not exist, so simply opening the options dialog raised KeyError.
    flow = _OptionsFlow(_entry())
    await flow.async_step_init(
        {CONF_MODEL: "kokoro", CONF_LANGUAGE: "American English", CONF_SEX: "Female"}
    )
    result = await flow.async_step_persona()
    assert result["step_id"] == "persona"


async def test_options_saves_technical_code_and_filters():
    flow = _OptionsFlow(_entry())
    await flow.async_step_init(
        {CONF_MODEL: "kokoro", CONF_LANGUAGE: "Hindi", CONF_SEX: "Female"}
    )
    await flow.async_step_persona({CONF_PERSONA: "hf_alpha", CONF_SPEED: 1.0})

    data = flow.created["data"]
    assert data[CONF_PERSONA] == "hf_alpha"
    assert data[CONF_LANGUAGE] == "Hindi"


async def test_options_change_filters_round_trip():
    flow = _OptionsFlow(_entry())
    await flow.async_step_init({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    result = await flow.async_step_persona(
        {CONF_CHANGE_FILTERS: True, CONF_PERSONA: "jf_alpha", CONF_SPEED: 3.0}
    )
    assert result["step_id"] == "init"
    assert flow._persona_prefill[CONF_SPEED] == 3.0
    assert CONF_PERSONA not in flow._persona_prefill


async def test_options_keeps_stored_persona_when_it_matches_filters():
    flow = _OptionsFlow(_entry(**{CONF_PERSONA: "af_heart"}))
    await flow.async_step_init(
        {CONF_LANGUAGE: "American English", CONF_SEX: "Female"}
    )
    await flow.async_step_persona()
    prefill = flow.forms[-1]["data_schema"]
    assert prefill is not None  # schema built without error
    # The stored persona matches the filters, so it is offered as the default.
    assert flow._filters[CONF_LANGUAGE] == "American English"


async def test_options_drops_stored_persona_when_filters_change(monkeypatch):
    """A Japanese filter must not pre-select an American English voice."""
    captured = {}

    def _fake_schema(personas, language, sex, user_input=None):
        captured["prefill"] = user_input or {}
        return {}

    monkeypatch.setattr(cf, "_persona_schema", _fake_schema)

    flow = _OptionsFlow(_entry(**{CONF_PERSONA: "af_heart"}))
    await flow.async_step_init({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    await flow.async_step_persona()

    assert CONF_PERSONA not in captured["prefill"]
    # Audio settings are still carried over.
    assert captured["prefill"][CONF_FORMAT] == "mp3"


async def test_options_offers_back_unclassifiable_blended_voice(monkeypatch):
    """A blended voice ("af_bella+af_sky") has no language; never hide it."""
    captured = {}

    def _fake_schema(personas, language, sex, user_input=None):
        captured["prefill"] = user_input or {}
        return {}

    monkeypatch.setattr(cf, "_persona_schema", _fake_schema)

    flow = _OptionsFlow(_entry(**{CONF_PERSONA: "af_bella+af_sky"}))
    await flow.async_step_init({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    await flow.async_step_persona()

    assert captured["prefill"][CONF_PERSONA] == "af_bella+af_sky"


async def test_options_keeps_derived_voice_matching_filters(monkeypatch):
    """A server voice absent from PERSONA_MAPPINGS is classified by its prefix.

    Without deriving, af_brandnew would be treated as unclassifiable and always
    offered back - even under a Japanese filter it does not belong to.
    """
    captured = {}

    def _fake_schema(personas, language, sex, user_input=None):
        captured["prefill"] = user_input or {}
        return {}

    monkeypatch.setattr(cf, "_persona_schema", _fake_schema)

    flow = _OptionsFlow(_entry(**{CONF_PERSONA: "af_brandnew"}))
    await flow.async_step_init(
        {CONF_LANGUAGE: "American English", CONF_SEX: "Female"}
    )
    await flow.async_step_persona()
    assert captured["prefill"][CONF_PERSONA] == "af_brandnew"

    flow = _OptionsFlow(_entry(**{CONF_PERSONA: "af_brandnew"}))
    await flow.async_step_init({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    await flow.async_step_persona()
    assert CONF_PERSONA not in captured["prefill"]


async def test_options_volume_multiplier_prefilled(monkeypatch):
    # The configured default volume must survive reopening the options dialog.
    captured = {}

    def _fake_schema(personas, language, sex, user_input=None):
        captured["prefill"] = user_input or {}
        return {}

    monkeypatch.setattr(cf, "_persona_schema", _fake_schema)

    flow = _OptionsFlow(_entry(**{CONF_VOLUME_MULTIPLIER: 2.5}))
    await flow.async_step_init(
        {CONF_LANGUAGE: "American English", CONF_SEX: "Female"}
    )
    await flow.async_step_persona()

    assert captured["prefill"][CONF_VOLUME_MULTIPLIER] == 2.5


# ---------------------------------------------------------------------------
# Reconfigure
# ---------------------------------------------------------------------------

class _ReconfigEntry(_FakeEntry):
    def __init__(self, data, title, entry_id="e1"):
        super().__init__(data)
        self.title = title
        self.entry_id = entry_id
        self.unique_id = cf._calc_unique_id(data[CONF_BASE_URL])


class _ReconfigureFlow(_FlowRecorder, KokoroConfigFlow):
    def __init__(self, entry, others=()):
        _FlowRecorder.__init__(self)
        KokoroConfigFlow.__init__(self)
        self._entry_obj = entry
        self._others = list(others)
        self.updated = None

    def _get_reconfigure_entry(self):
        return self._entry_obj

    def _async_current_entries(self, include_ignore=True):
        return [self._entry_obj, *self._others]

    def async_update_reload_and_abort(self, entry, **kwargs):
        self.updated = kwargs
        return {"type": "abort", "reason": "reconfigure_successful"}


@pytest.fixture
def _connection(monkeypatch):
    errors = {}

    async def _fake_models(session, base_url, api_key):
        return dict(errors), ["kokoro"]

    monkeypatch.setattr(cf, "async_fetch_models", _fake_models)
    return errors


def _reconfig_entry(title="Kokoro TTS (host)"):
    return _ReconfigEntry(_entry().data, title)


async def test_reconfigure_updates_url_key_and_generated_title(_connection):
    flow = _ReconfigureFlow(_reconfig_entry())
    result = await flow.async_step_reconfigure(
        {CONF_BASE_URL: " http://newhost:8880/ ", CONF_API_KEY: "secret"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert flow.updated == {
        # Regression: the entry kept the old URL's unique_id.
        "unique_id": cf._calc_unique_id("http://newhost:8880"),
        "title": "Kokoro TTS (newhost)",
        "data_updates": {CONF_BASE_URL: "http://newhost:8880", CONF_API_KEY: "secret"},
    }


async def test_reconfigure_keeps_a_custom_title(_connection):
    flow = _ReconfigureFlow(_reconfig_entry(title="Living room voice"))
    await flow.async_step_reconfigure({CONF_BASE_URL: "http://newhost:8880", CONF_API_KEY: "x"})
    assert flow.updated["title"] == "Living room voice"


async def test_reconfigure_rejects_a_url_another_entry_uses(_connection):
    other = _ReconfigEntry({**_entry().data, CONF_BASE_URL: "http://other:8880"}, "o", "e2")
    flow = _ReconfigureFlow(_reconfig_entry(), others=[other])
    result = await flow.async_step_reconfigure({CONF_BASE_URL: "http://other:8880", CONF_API_KEY: "x"})
    assert result == {"type": "abort", "reason": "already_configured"}
    assert flow.updated is None


async def test_reconfigure_shows_connection_errors_and_saves_nothing(_connection):
    _connection[CONF_BASE_URL] = "cannot_connect"
    flow = _ReconfigureFlow(_reconfig_entry())
    result = await flow.async_step_reconfigure({CONF_BASE_URL: "http://down:8880", CONF_API_KEY: "x"})
    assert result["type"] == "form" and result["step_id"] == "reconfigure"
    assert result["errors"] == {CONF_BASE_URL: "cannot_connect"}
    assert flow.updated is None


async def test_reauth_saves_the_new_key_through_update_reload_and_abort(_connection):
    entry = _reconfig_entry()
    flow = _ReconfigureFlow(entry)
    flow._get_reauth_entry = lambda: entry
    await flow.async_step_reauth(dict(entry.data))
    result = await flow.async_step_reauth_confirm({CONF_API_KEY: "new-key"})
    assert result["reason"] == "reconfigure_successful"  # recorder's canned reply
    assert flow.updated == {"data_updates": {CONF_API_KEY: "new-key"}}


async def test_setup_asks_the_server_for_models_once(monkeypatch):
    # Regression: the connection test and discovery each hit /v1/models.
    calls = []

    async def _fake_models(session, base_url, api_key):
        calls.append(base_url)
        return {}, ["kokoro", "tts-1"]

    monkeypatch.setattr(cf, "async_fetch_models", _fake_models)
    flow = _ConfigFlow()
    result = await flow.async_step_user({CONF_BASE_URL: "http://h:8880/", CONF_API_KEY: "x"})
    assert result["step_id"] == "filters"
    await flow.async_step_filters({CONF_LANGUAGE: "Japanese", CONF_SEX: "Female"})
    assert calls == ["http://h:8880"]
    assert flow._discovered["models"] == ["kokoro", "tts-1"]


def test_yaml_import_step_is_gone():
    # CONFIG_SCHEMA is config-entry-only, so the import step could never run.
    assert not hasattr(KokoroConfigFlow, "async_step_import")


async def test_options_prefill_turns_a_stored_pcm_into_wav(monkeypatch):
    # Regression: pre-filling "pcm", no longer a choice, made submit fail.
    captured = {}

    def _fake_schema(personas, language, sex, user_input=None):
        captured["prefill"] = user_input or {}
        return {}

    monkeypatch.setattr(cf, "_persona_schema", _fake_schema)
    flow = _OptionsFlow(_entry(**{CONF_FORMAT: "pcm"}))
    await flow.async_step_init({CONF_LANGUAGE: "American English", CONF_SEX: "Female"})
    await flow.async_step_persona()
    assert captured["prefill"][CONF_FORMAT] == "wav"
    assert captured["prefill"][CONF_FORMAT] in cf._FORMAT_OPTIONS
