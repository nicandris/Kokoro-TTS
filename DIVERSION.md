# Where this fork diverges from upstream

This fork ([nicandris/Kokoro-TTS](https://github.com/nicandris/Kokoro-TTS)) tracks
[beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) and was last synced with upstream
`2026.08.15` ([`48014b9`](https://github.com/beecho01/Kokoro-TTS/commit/48014b9)).

**This document exists so upstream can take any of it.** Everything below is offered freely —
no attribution needed, no PR required, no need to ask. Cherry-pick a commit, copy a function, or
just read it as a bug report and write your own fix. Each entry links straight to the
implementation and to the test that proves it.

Some of these are plain bugs still live upstream today; others are deliberate design
disagreements where upstream's choice is defensible and ours simply differs. The table says
which is which, so nobody has to guess what's a fix and what's taste.

Upstream's streaming synthesis and the two-step Filters → Persona flow are **not** listed here:
both are upstream's own work, merged into this fork and kept.

All links are pinned to
[`adf45cf`](https://github.com/nicandris/Kokoro-TTS/commit/adf45cfb2b37e527eafd35547d5009fddc7cfe07)
so they won't rot.

---

## Summary

| # | Difference | Kind | Porting effort |
|---|---|---|---|
| 1 | [Reuse Home Assistant's shared aiohttp session](#1-reuse-home-assistants-shared-aiohttp-session) | Bug | Trivial |
| 2 | [Raise `ConfigEntryAuthFailed` on HTTP 401](#2-raise-configentryauthfailed-on-http-401) | Bug | Trivial |
| 3 | [Derive `unique_id` from the config entry](#3-derive-unique_id-from-the-config-entry) | Bug | One line |
| 4 | [Store the voice code, not the display name](#4-store-the-voice-code-not-the-display-name) | Bug | Moderate |
| 5 | [Classify voices the server reports but the table doesn't list](#5-classify-voices-the-server-reports-but-the-table-doesnt-list) | Feature | Small |
| 6 | [Treat blended voices as unclassifiable](#6-treat-blended-voices-as-unclassifiable) | Bug | Trivial |
| 7 | [Default language follows the voice, not the filter](#7-default-language-follows-the-voice-not-the-filter) | Bug | Small |
| 8 | [Configurable default volume](#8-configurable-default-volume) | Feature | Small |
| 9 | [Sample rate is read-only](#9-sample-rate-is-read-only) | Design | Small |
| 10 | [Reload on any entry change, not just options](#10-reload-on-any-entry-change-not-just-options) | Design | Trivial |
| 11 | [`integration_type` in the manifest](#11-integration_type-in-the-manifest) | Polish | One line |
| 12 | [A test suite that runs without Home Assistant](#12-a-test-suite-that-runs-without-home-assistant) | Infra | Self-contained |
| 13 | [CI and automatic releases](#13-ci-and-automatic-releases) | Infra | Self-contained |

Items 1, 2, 3, 6, 10 and 11 are self-contained and apply cleanly against upstream's current
tree. Item 4 is the one that matters most for correctness and the one that needs real work,
because upstream's two-step flow is built around display names.

---

## 1. Reuse Home Assistant's shared aiohttp session

**Bug.** Upstream opens `aiohttp.ClientSession(timeout=timeout)` for every TTS call, and again
for every stream. A fresh session means no connection reuse, and it bypasses the client Home
Assistant has already configured (proxies, SSL context, cleanup on unload). Home Assistant's
own developer docs call this out — integrations are expected to use the shared session.

The cost is highest on the streaming path, where it is one entire session per spoken reply.

**Ours:** [`tts.py:268`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L262-L275)
(buffered) and [`tts.py:350`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L344-L356)
(streaming), both via `async_get_clientsession(self.hass)`. The per-request timeout moves onto
the `session.post(...)` call, since the session is shared and outlives any single request.

**Test:** [`test_stream_uses_shared_session`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_streaming.py#L231-L251)
— fails the test outright if `aiohttp.ClientSession` is constructed at all.

---

## 2. Raise `ConfigEntryAuthFailed` on HTTP 401

**Bug.** Upstream has a complete reauth flow — `async_step_reauth`, `async_step_reauth_confirm`,
the translation strings, all of it. Nothing can ever trigger it.

Home Assistant starts a reauth flow when an integration raises `ConfigEntryAuthFailed`. Upstream
raises a plain `RuntimeError` for every status code, 401 included, so when a Kokoro server's API
key is rotated the integration simply logs errors forever and the user is never prompted.

**Ours:** one [`_raise_for_status`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L172-L189)
shared by the buffered and streaming paths, so both behave identically. Everything that isn't a
401 still raises `RuntimeError` with upstream's existing friendly message map.

**Tests:** [`test_get_tts_audio_401_raises_auth_failed`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_request.py#L82-L88)
and [`test_stream_401_raises_auth_failed`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_streaming.py#L213-L221).

> Worth noting even if nothing else here is adopted: the reauth flow upstream already wrote is
> dead code until this changes.

---

## 3. Derive `unique_id` from the config entry

**Bug.** Upstream sets `self._attr_unique_id = f"kokoro_tts_{name}"`, and `name` is always the
module constant `"kokoro"` — no name field is collected anywhere in the config flow. So the
unique ID is the same string for every config entry, and a second Kokoro server collides with
the first.

**Ours:** the entry ID is passed in at
[`tts.py:100`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L99-L111)
and used directly at
[`tts.py:134`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L132-L135).
Unique by construction, and stable across restarts.

**Test:** [`test_unique_id_comes_from_entry_id`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_entity.py#L63-L66).

---

## 4. Store the voice code, not the display name

**Bug, and the most consequential one here.**

Upstream shows personas in the dropdown by display name, then converts back with
`get_technical_persona_name(display_name)` on save. That reverse lookup is not injective:

| Code | Language | Display name |
|---|---|---|
| `jf_alpha` | Japanese | Alpha |
| `hf_alpha` | Hindi | Alpha |
| `am_santa` | American English | Santa |
| `em_santa` | Spanish | Santa |
| `pm_santa` | Brazilian Portuguese | Santa |
| `ef_dora` | Spanish | Dora |
| `pf_dora` | Brazilian Portuguese | Dora |
| `em_alex` | Spanish | Alex |
| `pm_alex` | Brazilian Portuguese | Alex |

Pick the Hindi "Alpha" and the reverse lookup returns whichever entry it finds first — you get
the Japanese voice, silently, with no error. The Santa collision is three-way.

Upstream's two-step flow narrows this in practice (the accent filter usually disambiguates), but
it doesn't remove it: the persona field accepts a custom value, and "All Languages" is the
default filter.

**Ours:** the selector's option *value* is the code and the *label* is the friendly name, so no
reverse mapping is needed or exists — see
[`get_persona_select_options`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L127-L156).
The save paths just store what was picked
([config flow](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L516-L520),
[options flow](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L687-L691)).

**Test:** [`test_select_options_resolve_shared_display_names`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_persona_helpers.py#L86-L93)
pins `jf_alpha` and `hf_alpha` to distinct values with distinct labels.

**Porting note:** this is the one that needs real work upstream, since the two-step flow assumes
display names in both `_persona_schema` and the prefill logic. The change is mechanical
(`{"value": code, "label": name}` options, then delete `get_technical_persona_name` and its call
sites) but it touches most of `config_flow.py`. Migration for existing entries is not needed —
entries already store codes; only the in-flow representation changes.

---

## 5. Classify voices the server reports but the table doesn't list

**Feature.** `PERSONA_MAPPINGS` is a static table. A Kokoro server running a newer voice pack, a
custom build, or anything else not in that table reports voices the integration can't classify —
and upstream drops them from the list whenever a language or sex filter is active.

**Ours:** [`derive_persona_info`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L78-L107)
parses the `<lang><sex>_<name>` convention that every Kokoro code follows, so an unknown
`af_brandnew` still classifies as American English / Female and filters correctly. The static
table stays authoritative where it has an entry; this is only the fallback, used by
[`filter_personas_by_language_and_sex`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L105-L125).

**Test:** [`test_filter_unmapped_voice_visible_under_language_filter`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_persona_helpers.py#L50-L58).

---

## 6. Treat blended voices as unclassifiable

**Bug** (in our own earlier version of item 5 — upstream never had this, but will inherit it if
it takes item 5).

Kokoro supports blends: `af_bella+af_sky`, or weighted `af_bella(2)+af_sky(1)`. Both the README
and the UI hint advertise this. A blend has no single language or sex, but the prefix parser
happily reads the *first* component and calls the whole thing American English / Female — so a
configured blend disappears from the form the moment a different accent filter is selected.

**Ours:** an explicit guard at the top of `derive_persona_info` —
[`config_flow.py:90`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L86-L92).
Unclassifiable voices are always offered back rather than filtered out, at
[`config_flow.py:707`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L705-L722).

**Test:** [`test_derive_persona_info_blend_is_unclassifiable`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_persona_helpers.py#L37-L42).

---

## 7. Default language follows the voice, not the filter

**Bug.** Upstream derives `_attr_default_language` from the configured *accent filter*
(`LANGUAGE_HA_CODE_MAP.get(language, ...)`). That filter defaults to `"All Languages"`, which
isn't in the map — so it falls through to `en` even when the selected voice is `jf_alpha`.

Upstream's change to advertise **all** supported languages is correct and this fork adopted it;
this is only about which one is the *default*.

**Ours:** derived from the voice code instead, at
[`tts.py:150`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L144-L153),
using [`LANG_CODE_TO_HA_LOCALE`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/const.py#L163-L177).
It reuses the existing `_get_lang_code` helper, which already prefers the filter and falls back
to the code prefix — so an explicitly chosen accent still wins.

One constraint worth copying: the map's values use `en-GB` and `pt-BR` to match
`SUPPORTED_LANGUAGES`. A default outside the advertised set makes Home Assistant drop the entity
from pipelines.

**Tests:** [`test_default_language_follows_persona`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_entity.py#L34-L42)
and `test_default_language_is_always_advertised` just below it, which enforces that constraint.

---

## 8. Configurable default volume

**Feature.** Upstream supports `volume_multiplier` as a per-call option only; the default is
hardcoded to `1.0`, so getting consistent loudness means passing it on every single service call.

**Ours:** a slider (0.1–5.0) in both the setup and options flows —
[`config_flow.py:386`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L386-L394)
— resolved as the fallback for the per-call option at
[`tts.py:204`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L204-L217).
Per-call still overrides, exactly as before.

Two details that differ from upstream's handling:

- It is sent **always**, not only when `!= 1.0`
  ([`tts.py:239`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/tts.py#L236-L240)),
  so an explicit `1.0` can override a server-side default rather than being silently omitted.
- The streaming path resolves it through the same `_resolve_options`, so the configured default
  applies there too. Upstream's streaming code reads `DEFAULT_VOLUME_MULTIPLIER` directly and
  would ignore any configured value.

**Tests:** [`test_default_volume_multiplier_applied`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_request.py#L104-L109)
and [`test_stream_applies_default_volume_multiplier`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_streaming.py#L193-L201).

---

## 9. Sample rate is read-only

**Design disagreement.** Kokoro FastAPI's output is fixed at 24000 Hz. Upstream presents
`sample_rate` as an editable config field and advertises it in `SUPPORTED_OPTIONS`, but the value
is never put in the request payload — so changing it does nothing, silently.

**Ours:** kept visible for transparency but rendered read-only
([`config_flow.py:396`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L396-L401)),
sourced from a named constant
([`const.py:30`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/const.py#L29-L31)),
and dropped from `SUPPORTED_OPTIONS`. The field's description states plainly that it is fixed and
not sent.

Removing the field entirely would be just as valid — the point is only that a control which does
nothing shouldn't look adjustable.

---

## 10. Reload on any entry change, not just options

**Design disagreement.** Upstream subclasses `OptionsFlowWithReload`, which reloads the entry
when options change. That covers the common case, but not a reauth that writes a new API key into
`entry.data` — after a successful reauth the entity keeps using the old key until Home Assistant
restarts.

**Ours:** an update listener registered in
[`__init__.py:28`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/__init__.py#L25-L31),
reloading via
[`async_reload_entry`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/__init__.py#L38-L45).
It fires on any entry update, options and data alike.

**Do not use both.** The options flow here is deliberately plain `OptionsFlow`
([`config_flow.py:600`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/config_flow.py#L600-L607));
combining the listener with `OptionsFlowWithReload` reloads the entry twice on every options save.

**Test:** [`test_setup_entry_registers_options_reload_listener`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_setup.py#L35-L48).

---

## 11. `integration_type` in the manifest

**Polish.** One line —
[`manifest.json:9`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/custom_components/kokoro_tts/manifest.json#L9).
Without it Home Assistant falls back to a default classification, which affects how the
integration is grouped and described in the UI. Upstream already sets `iot_class`.

---

## 12. A test suite that runs without Home Assistant

**Infrastructure**, and entirely self-contained — it adds files, changes none.

Home Assistant is a heavy dependency to install just to test a custom component, so
[`tests/conftest.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/conftest.py)
registers minimal stub modules in `sys.modules` before the integration is imported. The stubs
install *only* when the real package isn't importable, so adding `homeassistant` to the test
environment later transparently runs everything against the real thing instead.

63 tests, well under a second, no HA install:

| File | Covers |
|---|---|
| [`test_persona_helpers.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_persona_helpers.py) | Classification, filtering, display names, unique-id hashing |
| [`test_tts_entity.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_entity.py) | Language advertising, lang-code resolution, error map |
| [`test_tts_request.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_request.py) | The buffered request path, 401 handling, volume |
| [`test_tts_streaming.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_tts_streaming.py) | Sentence splitting, tail flush, format fallback, streaming errors |
| [`test_config_flow_steps.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_config_flow_steps.py) | The two-step flow: filter persistence, back-navigation, prefill |
| [`test_setup.py`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/tests/test_setup.py) | Entry setup wiring and the reload listener |

`test_config_flow_steps.py` and `test_tts_streaming.py` cover upstream's own features and should
port with little more than an import change.

Run with `python -m pytest`
([`pytest.ini`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/pytest.ini),
[`requirements_test.txt`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/requirements_test.txt)).

---

## 13. CI and automatic releases

**Infrastructure**, self-contained.

- [`tests.yml`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/.github/workflows/tests.yml)
  — pytest on Python 3.13 and 3.14.
- [`release.yml`](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/.github/workflows/release.yml)
  — on a push to `main`, reads the version from `manifest.json`, and if no release with that tag
  exists, cuts one using the matching `CHANGELOG.md` section as the release notes. Bumping the
  manifest version is the entire release process; HACS picks it up with no manual step.

---

## Licensing

This fork carries [CC-BY-NC-SA-4.0](https://github.com/nicandris/Kokoro-TTS/blob/adf45cfb2b37e527eafd35547d5009fddc7cfe07/LICENSE),
matching the badge upstream displays. Upstream has no `LICENSE` file in the repository, which
leaves the terms ambiguous for anyone packaging or forking it — worth adding regardless of
anything else in this document.

To be unambiguous about the code above: consider it available to upstream under whatever terms
suit the project, including relicensing to match. Nothing here needs to be negotiated.
