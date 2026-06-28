# Changelog

Newest changes first. This integration is a fork of [beecho01/Kokoro-TTS](https://github.com/beecho01/Kokoro-TTS) (baseline **2026.05.23**).

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
