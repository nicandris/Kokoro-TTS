# Changelog

Newest changes first. This integration is a fork of [beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) (baseline **2026.05.23**).

## 2026.10.04.1 — Assist pipeline voice selection

- An Assist pipeline can now pick a Kokoro voice. HA sends the pipeline's voice under
  its standard `voice` option, which was missing from the supported options, so HA
  rejected every request from a pipeline with a voice set ("Invalid options found:
  ['voice']") ([#14](https://github.com/nicandris/Kokoro-TTS/pull/14), thanks
  @davidcoulson).
- The pipeline voice picker is populated: Kokoro now lists its voices per language
  instead of leaving the dropdown empty.
- The persona configured in this integration's options is listed first, because HA
  gives a newly created pipeline the first voice in that list.
- `tests/conftest.py` stubs `homeassistant.components.tts.Voice`, mirroring the real
  frozen dataclass.

## 2026.09.06.1 — Upstream sync 2026.08.19 + test hardening

No change to the integration's own code — this releases the maintenance work that
landed after `2026.08.16.1`.

- Synced [beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) up to
  `2026.08.19`. Everything upstream added in between is README wording and its own
  version bump, so nothing functional came across.
- Added [`DIVERSION.md`](DIVERSION.md), which maps every deliberate difference from
  upstream back to the origin file it replaces.
- CI now also runs the suite against the **real** Home Assistant release, not only the
  stubs in `tests/conftest.py`. The stubs make imports succeed; they cannot catch a
  signature that has drifted from the real API.
- That job immediately paid for itself: `TTSAudioRequest`/`TTSAudioResponse` stubs now
  mirror the real dataclass field order, and the config-flow test stopped shadowing
  `ConfigFlow.unique_id`, which is read-only upstream.

## 2026.08.16.1 — Merge upstream 2026.08.15

Syncs [beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) up to `2026.08.15`. Several upstream commits re-fix things this fork had already fixed independently (options reload, entry reload, in-repo brand images); those were resolved in favour of the existing implementation.

Taken from upstream:

- **Streaming TTS.** `async_stream_tts_audio` synthesises one request per finished sentence, so Assist starts speaking before the conversation agent has finished writing. Formats that cannot be concatenated (`wav`, `flac`) fall back to `mp3`.
- **Two-step config/options flow.** Model/accent/sex now live in a "Filter Voices" step, persona and audio settings in a "Select Persona" step, with a `Change Voice Accent / Sex` checkbox to go back. Home Assistant forms are not reactive, so this removes the old ambiguity where Submit sometimes saved and sometimes just refreshed the persona list.
- **Advertise every supported language.** Home Assistant hides a TTS entity from any pipeline whose language it does not advertise, so all nine are now listed instead of only the configured voice's.
- Minimum Home Assistant version declared (`2025.8.0`).

Fork behaviour kept over upstream's version:

- Persona selector still stores the **technical voice code** as the option value. Upstream reverse-maps the display name, which picks the wrong voice whenever two share a name (`jf_alpha` / `hf_alpha`, both "Alpha").
- Shared aiohttp session and `ConfigEntryAuthFailed` on HTTP 401 — now applied to upstream's new streaming path too, which opened its own session per stream and raised a plain `RuntimeError` on 401 (so a bad API key never triggered reauth).
- Configurable default volume; upstream's streaming path read a hardcoded `1.0` and ignored the setting.
- `unique_id` derived from the config entry, read-only 24 kHz sample rate, narrowed exception handling.

Fixed while merging:

- The entity's **default** language now follows the configured voice (Japanese voice → `ja`) while the advertised set stays complete; `en-GB` and `pt-BR` are used so the default is always a member of the advertised set.
- Blended voices (`af_bella+af_sky`) are treated as unclassifiable again. Deriving a language from the first component hid a configured blend behind an unrelated accent filter.
- Kept a single reload mechanism: the update listener in `__init__.py` covers both options changes and a reauth that swaps the API key, where `OptionsFlowWithReload` covers only the former.
- Options prefill no longer reads a `sample_rate` default that does not exist.

## 2026.08.01.1 — CI maintenance

- Bump `actions/checkout` v4 → v7 and `actions/setup-python` v5 → v7.
- Test matrix now Python 3.13 and 3.14; drop 3.12, which no supported Home Assistant core runs on (core requires >=3.14.2).
- No functional change to the integration.

## 2026.07.12.5 — Add license

- Add LICENSE

## 2026.06.28.4 — Default volume control

- Add a configurable default volume multiplier in the setup and options menus (slider). Boosts or attenuates output loudness for every message; still overridable per call via `volume_multiplier`.

## 2026.06.28.3 — Tests & CI

- Add a unit-test suite (persona helpers, TTS entity, async request flow, options-reload listener) with Home Assistant stubbed so it runs without HA installed.
- Add a GitHub Actions workflow running pytest on Python 3.12 and 3.13.

## 2026.06.28.2 — Integration fixes & voice handling

- Rebuild the TTS entity when options change (register the update listener that previously existed but was never wired up).
- Derive the entity `unique_id` from the config entry so configuring multiple Kokoro servers no longer collides.
- Raise `ConfigEntryAuthFailed` on HTTP 401 so Home Assistant drives the reauth flow instead of a generic error.
- Reuse Home Assistant's shared aiohttp session instead of opening one per request.
- Surface all server-reported voices by deriving language/sex from the code prefix; reject malformed codes.
- Store the technical voice code as the selector option value, so voices sharing a display name (e.g. `jf_alpha`/`hf_alpha`, both "Alpha") no longer map back to the wrong voice.
- Advertise the configured voice's language (e.g. Japanese → `ja`) instead of always `en`.
- Show sample rate as a read-only 24 kHz field — Kokoro FastAPI output is fixed at 24000 Hz, so it was collected but never sent.
- Manifest: add `integration_type`, set `iot_class: local_polling`, drop unused `http`/`frontend` deps, fix repo URLs. Modernize the options flow and narrow broad exception handling.

## 2026.06.28.1 — HACS brand images

- Add in-repo brand images (`icon`, `logo`, and `dark_` variants) under `custom_components/kokoro_tts/brand/`, served by Home Assistant 2026.3+ — overrides the brands CDN, the intended path for custom integrations.

## Forked from beecho01/Kokoro-TTS

- Forked from [beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) at upstream version 2026.05.23.
