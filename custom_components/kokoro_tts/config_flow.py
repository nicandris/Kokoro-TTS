"""Config flow for Kokoro TTS with dynamic model/persona discovery."""
from __future__ import annotations

from typing import Any
import asyncio
import hashlib
import logging
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_FORMAT,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_PERSONA,
    CONF_SAMPLE_RATE,
    CONF_SEX,
    CONF_SPEED,
    DEFAULTS,
    DOMAIN,
    FIXED_SAMPLE_RATE,
    LANGUAGE_CODE_MAP,
    LANGUAGE_OPTIONS,
    PERSONA_MAPPINGS,
    SEX_OPTIONS,
)

# Reverse of LANGUAGE_CODE_MAP: first-letter code -> language display name.
_CODE_TO_LANGUAGE = {code: lang for lang, code in LANGUAGE_CODE_MAP.items()}

_LOGGER = logging.getLogger(__name__)

_FORMAT_OPTIONS = ["mp3", "wav", "opus", "flac", "pcm"]


# ---------------------------------------------------------------------------
# Persona helpers
# ---------------------------------------------------------------------------

def get_persona_display_name(
    technical_name: str,
    selected_language: str | None = None,
    selected_sex: str | None = None,
) -> str:
    """Convert technical persona name to user-friendly display name."""
    if technical_name not in PERSONA_MAPPINGS:
        return technical_name

    language, sex, name = PERSONA_MAPPINGS[technical_name]

    if (
        selected_language
        and selected_language != "All Languages"
        and selected_sex
        and selected_sex != "All"
    ):
        return name
    if selected_language and selected_language != "All Languages":
        return f"{name} ({sex})"
    if selected_sex and selected_sex != "All":
        return f"{name} ({language})"
    return f"{name} ({language}, {sex})"


def derive_persona_info(code: str) -> tuple[str, str, str] | None:
    """Best-effort (language, sex, display_name) for a persona not in the static map.

    Kokoro voice codes follow ``<lang><sex>_<name>`` (e.g. ``af_heart`` ->
    American English / Female / "Heart"). This lets voices the server reports
    but that aren't in PERSONA_MAPPINGS still be classified and filtered,
    instead of only appearing when no filters are active.
    """
    if "_" not in code:
        return None
    prefix, _, raw_name = code.partition("_")
    if len(prefix) < 2 or not raw_name:
        return None
    language = _CODE_TO_LANGUAGE.get(prefix[0].lower())
    sex = {"f": "Female", "m": "Male"}.get(prefix[1].lower())
    if not language or not sex:
        return None
    display = raw_name.replace("_", " ").title()
    return language, sex, display


def filter_personas_by_language_and_sex(
    personas: list[str], selected_language: str, selected_sex: str
) -> list[str]:
    """Filter persona list by selected language and sex."""
    filtered: list[str] = []
    lang_all = selected_language in ("All Languages", "", None)
    sex_all = selected_sex in ("All", "", None)

    for persona in personas:
        info = PERSONA_MAPPINGS.get(persona) or derive_persona_info(persona)
        if info is not None:
            language, sex, _ = info
            if (lang_all or language == selected_language) and (
                sex_all or sex == selected_sex
            ):
                filtered.append(persona)
        elif lang_all and sex_all:
            # Truly unclassifiable codes: only when no filters active
            filtered.append(persona)
    return filtered


def get_persona_select_options(
    personas: list[str], selected_language: str, selected_sex: str
) -> list[dict[str, str]]:
    """Build {value, label} options for the persona selector.

    The option *value* is the technical code (e.g. ``af_heart``) and the
    *label* is the friendly display name. Using the code as the value means
    voices that share a display name across languages (e.g. ``jf_alpha`` and
    ``hf_alpha`` both shown as "Alpha") no longer collide when reverse-mapped
    on save — the exact selected code is stored directly.
    """
    filtered = filter_personas_by_language_and_sex(personas, selected_language, selected_sex)
    options = [
        {"value": code, "label": get_persona_display_name(code, selected_language, selected_sex)}
        for code in filtered
    ]
    options.sort(key=lambda option: option["label"])
    if not options:
        if selected_language != "All Languages" and selected_sex != "All":
            label = f"No {selected_sex.lower()} personas available for {selected_language}"
        elif selected_language != "All Languages":
            label = f"No personas available for {selected_language}"
        elif selected_sex != "All":
            label = f"No {selected_sex.lower()} personas available"
        else:
            label = "No personas available"
        # Empty value so this placeholder fails the "persona selected" check.
        options = [{"value": "", "label": label}]
    return options


# ---------------------------------------------------------------------------
# API discovery
# ---------------------------------------------------------------------------

async def _discover_models_and_personas(
    base_url: str, api_key: str
) -> tuple[list[str], list[str]]:
    """Discover models and personas from Kokoro API endpoints."""
    headers: dict[str, str] = {}
    if api_key and api_key not in ("x", "not-needed", ""):
        headers["Authorization"] = f"Bearer {api_key}"

    models: list[str] = []
    personas: list[str] = []
    timeout = aiohttp.ClientTimeout(total=8)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Discover models from /v1/models
            try:
                async with session.get(f"{base_url}/v1/models", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and isinstance(data.get("data"), list):
                            models = [
                                str(item.get("id"))
                                for item in data["data"]
                                if isinstance(item, dict) and item.get("id")
                            ]
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                _LOGGER.debug("Failed to discover models from %s/v1/models", base_url)

            # Discover personas from /v1/audio/voices
            try:
                async with session.get(
                    f"{base_url}/v1/audio/voices", headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict):
                            voices = data.get("voices", data.get("personas", []))
                        elif isinstance(data, list):
                            voices = data
                        else:
                            voices = []

                        for voice in voices:
                            if isinstance(voice, str):
                                personas.append(voice)
                            elif isinstance(voice, dict) and voice.get("id"):
                                personas.append(str(voice["id"]))
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                _LOGGER.debug("Failed to discover personas from %s/v1/audio/voices", base_url)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        _LOGGER.debug("Error in discovery session for %s", base_url)

    # Fallback to static mappings if API discovery failed
    if not personas:
        personas = list(PERSONA_MAPPINGS.keys())
    if not models:
        models = ["kokoro"]

    return models, personas


async def _test_connection(base_url: str, api_key: str) -> dict[str, str]:
    """Test connection to the Kokoro FastAPI server.

    Returns a dict of errors (empty dict = success).
    """
    headers: dict[str, str] = {}
    if api_key and api_key not in ("x", "not-needed", ""):
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = aiohttp.ClientTimeout(total=10, connect=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(f"{base_url}/v1/models", headers=headers) as resp:
                    if resp.status == 401:
                        return {CONF_API_KEY: "auth_failed"}
                    if resp.status == 404:
                        return {CONF_BASE_URL: "server_not_found"}
                    if resp.status >= 500:
                        return {CONF_BASE_URL: "server_error"}
                    # 200 or other - server is reachable
            except aiohttp.ClientSSLError:
                return {CONF_BASE_URL: "ssl_error"}
            except aiohttp.ClientConnectorError:
                return {CONF_BASE_URL: "cannot_connect"}
            except asyncio.TimeoutError:
                return {CONF_BASE_URL: "timeout"}
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return {CONF_BASE_URL: "cannot_connect"}
    return {}


# ---------------------------------------------------------------------------
# Unique ID
# ---------------------------------------------------------------------------

def _calc_unique_id(base_url: str) -> str:
    """Generate stable unique ID from base URL."""
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Schema builders
# ---------------------------------------------------------------------------

def _base_schema(user_input: dict | None = None) -> vol.Schema:
    """Schema for base connection step."""
    ui = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL, default=ui.get(CONF_BASE_URL, "")
            ): str,
            vol.Optional(
                CONF_API_KEY, default=ui.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            ): str,
        }
    )


def _details_schema(
    models: list[str],
    personas: list[str],
    user_input: dict | None = None,
) -> vol.Schema:
    """Schema for details step with dynamic selectors."""
    ui = user_input or {}
    schema: dict[vol.Optional | vol.Required, Any] = {}

    # Model selector
    if models:
        schema[
            vol.Optional(CONF_MODEL, default=ui.get(CONF_MODEL, DEFAULTS[CONF_MODEL]))
        ] = selector.selector(
            {
                "select": {
                    "options": sorted(models),
                    "mode": "dropdown",
                    "custom_value": True,
                }
            }
        )
    else:
        schema[
            vol.Optional(CONF_MODEL, default=ui.get(CONF_MODEL, DEFAULTS[CONF_MODEL]))
        ] = str

    # Language filter
    selected_language = ui.get(CONF_LANGUAGE, DEFAULTS[CONF_LANGUAGE])
    schema[vol.Optional(CONF_LANGUAGE, default=selected_language)] = selector.selector(
        {"select": {"options": LANGUAGE_OPTIONS, "mode": "dropdown"}}
    )

    # Sex filter
    selected_sex = ui.get(CONF_SEX, DEFAULTS[CONF_SEX])
    schema[vol.Optional(CONF_SEX, default=selected_sex)] = selector.selector(
        {"select": {"options": SEX_OPTIONS, "mode": "dropdown"}}
    )

    # Persona selector (filtered). Option values are technical codes.
    if personas:
        persona_options = get_persona_select_options(
            personas, selected_language, selected_sex
        )
        current_persona = ui.get(CONF_PERSONA, DEFAULTS[CONF_PERSONA]) or ""
        # Ensure the currently-selected code is present as an option.
        if current_persona and not any(
            option["value"] == current_persona for option in persona_options
        ):
            persona_options.append(
                {
                    "value": current_persona,
                    "label": get_persona_display_name(
                        current_persona, selected_language, selected_sex
                    ),
                }
            )

        schema[vol.Optional(CONF_PERSONA, default=current_persona)] = selector.selector(
            {
                "select": {
                    "options": persona_options,
                    "mode": "dropdown",
                    "custom_value": True,
                }
            }
        )
    else:
        persona_default = ui.get(CONF_PERSONA, DEFAULTS[CONF_PERSONA]) or ""
        schema[vol.Optional(CONF_PERSONA, default=persona_default)] = str

    # Speed slider
    schema[
        vol.Optional(CONF_SPEED, default=ui.get(CONF_SPEED, DEFAULTS[CONF_SPEED]))
    ] = selector.selector({"number": {"min": 0.25, "max": 4.0, "step": 0.05, "mode": "slider"}})

    # Format dropdown
    schema[
        vol.Optional(CONF_FORMAT, default=ui.get(CONF_FORMAT, DEFAULTS[CONF_FORMAT]))
    ] = selector.selector({"select": {"options": _FORMAT_OPTIONS, "mode": "dropdown"}})

    # Sample rate: read-only. Kokoro FastAPI always outputs 24 kHz, so this is
    # surfaced for transparency but cannot be changed (and is never sent).
    schema[
        vol.Optional(CONF_SAMPLE_RATE, default=str(FIXED_SAMPLE_RATE))
    ] = selector.selector({"text": {"read_only": True}})

    return vol.Schema(schema)


# ---------------------------------------------------------------------------
# Config Flow
# ---------------------------------------------------------------------------

class KokoroConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kokoro TTS with dynamic discovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._base_info: dict[str, Any] = {}
        self._discovered: dict[str, list[str]] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow."""
        return KokoroOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None):
        """Handle base connection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base = (user_input.get(CONF_BASE_URL) or "").strip().rstrip("/")

            # Validate URL
            if not base:
                errors[CONF_BASE_URL] = "base_url_required"
            elif not base.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_base_url"
            else:
                try:
                    p = urlparse(base)
                    if not p.hostname:
                        errors[CONF_BASE_URL] = "invalid_base_url"
                except ValueError:
                    errors[CONF_BASE_URL] = "invalid_base_url"

            if not errors:
                # Test connection to the server
                api_key = user_input.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
                conn_errors = await _test_connection(base, api_key)
                if conn_errors:
                    errors.update(conn_errors)
                else:
                    self._base_info = {
                        CONF_BASE_URL: base,
                        CONF_API_KEY: api_key,
                    }
                    return await self.async_step_details()

        return self.async_show_form(
            step_id="user", data_schema=_base_schema(user_input), errors=errors
        )

    async def async_step_details(self, user_input: dict | None = None):
        """Handle model/persona selection with dynamic discovery."""
        base_url = self._base_info[CONF_BASE_URL]
        api_key = self._base_info.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])

        # Discover models and personas if not cached
        if "models" not in self._discovered:
            models, personas = await _discover_models_and_personas(base_url, api_key)
            self._discovered = {"models": models, "personas": personas}
        else:
            models = self._discovered["models"]
            personas = self._discovered["personas"]

        if user_input is not None:
            # Check if this is a filter change (language or sex changed, no persona selected)
            has_persona = bool(
                user_input.get(CONF_PERSONA)
                and str(user_input.get(CONF_PERSONA)).strip()
            )

            if not has_persona:
                # Re-render with updated filters
                schema = _details_schema(models, personas, user_input)
                return self.async_show_form(step_id="details", data_schema=schema)

            # Validate persona selection
            selected_persona = user_input.get(CONF_PERSONA)
            if not selected_persona or not str(selected_persona).strip():
                return self.async_show_form(
                    step_id="details",
                    data_schema=_details_schema(models, personas, user_input),
                    errors={CONF_PERSONA: "persona_required"},
                )

            # CONF_PERSONA is already the technical code (the selector option
            # value), so no display-name reverse-mapping is needed.
            data = {**self._base_info, **user_input}

            # Create entry with unique ID
            unique_id = _calc_unique_id(base_url)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            hostname = urlparse(base_url).hostname or base_url
            title = f"Kokoro TTS ({hostname})"
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="details", data_schema=_details_schema(models, personas, user_input)
        )

    async def async_step_reauth(self, entry_data: dict):
        """Handle re-authentication."""
        self._base_info = {
            CONF_BASE_URL: entry_data[CONF_BASE_URL],
            CONF_API_KEY: entry_data.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY]),
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ):
        """Handle re-auth confirmation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            base_url = self._base_info[CONF_BASE_URL]
            conn_errors = await _test_connection(base_url, api_key)
            if conn_errors:
                errors.update(conn_errors)
            else:
                # Update the existing entry
                entry_id = self.context.get("entry_id")
                if entry_id:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            data={**entry.data, CONF_API_KEY: api_key},
                        )
                        return self.async_abort(reason="reauth_successful")
                return self.async_abort(reason="reauth_failed")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_API_KEY, default=self._base_info.get(CONF_API_KEY, "")): str,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict):
        """Support YAML import."""
        base = (user_input.get(CONF_BASE_URL) or "").strip().rstrip("/")
        if not base:
            return self.async_abort(reason="base_url_required")

        unique_id = _calc_unique_id(base)
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        hostname = urlparse(base).hostname or base
        title = f"Kokoro TTS ({hostname})"
        return self.async_create_entry(title=title, data=user_input)


# ---------------------------------------------------------------------------
# Options Flow
# ---------------------------------------------------------------------------

class KokoroOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Kokoro TTS."""

    async def async_step_init(self, user_input: dict | None = None):
        """Handle options step with dynamic discovery."""
        if user_input is not None:
            # Check if this is a filter change (no persona selected)
            has_persona = bool(
                user_input.get(CONF_PERSONA)
                and str(user_input.get(CONF_PERSONA)).strip()
            )

            if not has_persona:
                # Re-render with updated filters
                data = {**self.config_entry.data, **(self.config_entry.options or {})}
                data.pop(CONF_BASE_URL, None)
                form_data = {**data, **user_input}

                base_url = self.config_entry.data[CONF_BASE_URL]
                api_key = self.config_entry.data.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
                models, personas = [], []
                try:
                    models, personas = await _discover_models_and_personas(base_url, api_key)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass

                return self.async_show_form(
                    step_id="init",
                    data_schema=_details_schema(models, personas, form_data),
                )

            # Validate persona
            selected_persona = user_input.get(CONF_PERSONA)
            if not selected_persona or not str(selected_persona).strip():
                data = {**self.config_entry.data, **(self.config_entry.options or {})}
                data.pop(CONF_BASE_URL, None)
                form_data = {**data, **user_input}

                base_url = self.config_entry.data[CONF_BASE_URL]
                api_key = self.config_entry.data.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
                try:
                    models, personas = await _discover_models_and_personas(base_url, api_key)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    models, personas = [], []

                return self.async_show_form(
                    step_id="init",
                    data_schema=_details_schema(models, personas, form_data),
                    errors={CONF_PERSONA: "persona_required"},
                )

            # CONF_PERSONA is already the technical code (the selector value).
            return self.async_create_entry(title="", data=user_input)

        # Initial form display. The stored CONF_PERSONA is the technical code,
        # which is exactly what the selector uses as its option value/default.
        data = {**self.config_entry.data, **(self.config_entry.options or {})}
        base_url = data.pop(CONF_BASE_URL, None)

        # Discover models/personas
        models, personas = [], []
        if base_url:
            api_key = self.config_entry.data.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            try:
                models, personas = await _discover_models_and_personas(base_url, api_key)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass

        return self.async_show_form(
            step_id="init", data_schema=_details_schema(models, personas, data)
        )
