"""Config flow for Kokoro TTS with dynamic model/persona discovery."""
from __future__ import annotations

from typing import Any
import hashlib
import logging
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_fetch_models, async_fetch_voices

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_FORMAT,
    CONF_LANGUAGE,
    CONF_MODEL,
    CONF_PERSONA,
    CONF_SEX,
    CONF_SPEED,
    CONF_VOLUME_MULTIPLIER,
    DEFAULTS,
    DOMAIN,
    LANGUAGE_CODE_MAP,
    LANGUAGE_OPTIONS,
    PERSONA_MAPPINGS,
    SEX_OPTIONS,
    playable_format,
)

# Reverse of LANGUAGE_CODE_MAP: first-letter code -> language display name.
_CODE_TO_LANGUAGE = {code: lang for lang, code in LANGUAGE_CODE_MAP.items()}

_LOGGER = logging.getLogger(__name__)

_FORMAT_OPTIONS = ["mp3", "wav", "opus", "flac"]

# The API key is a secret: masked in the form.
_PASSWORD_SELECTOR = selector.selector({"text": {"type": "password"}})

# Transient, wizard-only field - never stored in the config entry. Lets the
# persona step send the user back to the filter step.
CONF_CHANGE_FILTERS = "change_filters"


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

    Blends ("af_bella+af_sky") are deliberately unclassifiable: they have no
    single language or sex, and deriving one from the first component would
    hide a configured blend behind an unrelated filter.
    """
    if "+" in code:
        return None
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

async def _discover_voices(session, base_url: str, api_key: str) -> list[str]:
    """Server-reported voices, or the built-in list when discovery fails."""
    return await async_fetch_voices(session, base_url, api_key) or list(
        PERSONA_MAPPINGS
    )


# ---------------------------------------------------------------------------
# Unique ID
# ---------------------------------------------------------------------------

def _calc_unique_id(base_url: str) -> str:
    """Generate stable unique ID from base URL."""
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()[:12]


def _entry_title(base_url: str) -> str:
    """Default entry title, named after the server's host."""
    return f"Kokoro TTS ({urlparse(base_url).hostname or base_url})"


def _validate_base_url(base: str) -> dict[str, str]:
    """Return form errors for a normalised base URL (empty dict = valid)."""
    if not base:
        return {CONF_BASE_URL: "base_url_required"}
    if not base.startswith(("http://", "https://")):
        return {CONF_BASE_URL: "invalid_base_url"}
    try:
        parsed = urlparse(base)
        # Userinfo ("http://user:pw@host") would be stored and logged in the
        # URL; the API key field is the place for credentials.
        if not parsed.hostname or parsed.username or parsed.password:
            return {CONF_BASE_URL: "invalid_base_url"}
    except ValueError:
        return {CONF_BASE_URL: "invalid_base_url"}
    return {}


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
            ): _PASSWORD_SELECTOR,
        }
    )


def _filters_schema(models: list[str], user_input: dict | None = None) -> vol.Schema:
    """Schema for the model/accent/sex filter step.

    This is a separate wizard step (rather than fields alongside Persona)
    because Home Assistant's config-flow forms are not reactive: changing a
    dropdown does nothing until the form is submitted. Splitting filtering
    from persona selection into two steps makes that submit boundary an
    explicit "Next", instead of the same "Submit" sometimes finalizing and
    sometimes silently just refreshing the persona list.
    """
    ui = user_input or {}
    schema: dict[vol.Optional | vol.Required, Any] = {}

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

    schema[
        vol.Optional(CONF_LANGUAGE, default=ui.get(CONF_LANGUAGE, DEFAULTS[CONF_LANGUAGE]))
    ] = selector.selector({"select": {"options": LANGUAGE_OPTIONS, "mode": "dropdown"}})

    schema[
        vol.Optional(CONF_SEX, default=ui.get(CONF_SEX, DEFAULTS[CONF_SEX]))
    ] = selector.selector({"select": {"options": SEX_OPTIONS, "mode": "dropdown"}})

    return vol.Schema(schema)


def _persona_schema(
    personas: list[str],
    selected_language: str,
    selected_sex: str,
    user_input: dict | None = None,
) -> vol.Schema:
    """Schema for the persona + audio settings step.

    selected_language/selected_sex come from the filters step that already
    ran, so the persona list here is always pre-filtered - there is no
    "was this a filter change or a final submit" ambiguity to resolve.
    """
    ui = user_input or {}
    schema: dict[vol.Optional | vol.Required, Any] = {}

    # "Back" affordance: HA config-flow forms have no native back button, so
    # this checkbox is how a user returns to the filter step to change accent
    # or sex without restarting the whole flow.
    schema[vol.Optional(CONF_CHANGE_FILTERS, default=False)] = bool

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

    # Default volume multiplier: boosts/attenuates output (1.0 = unchanged).
    schema[
        vol.Optional(
            CONF_VOLUME_MULTIPLIER,
            default=ui.get(CONF_VOLUME_MULTIPLIER, DEFAULTS[CONF_VOLUME_MULTIPLIER]),
        )
    ] = selector.selector(
        {"number": {"min": 0.1, "max": 5.0, "step": 0.1, "mode": "slider"}}
    )

    return vol.Schema(schema)


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------

class _VoiceStepsMixin:
    """Filter + persona step handling shared by the config and options flows."""

    _filters: dict[str, Any]
    _persona_prefill: dict[str, Any]

    def _store_filters(self, user_input: dict) -> None:
        self._filters = {
            key: user_input.get(key, DEFAULTS[key])
            for key in (CONF_MODEL, CONF_LANGUAGE, CONF_SEX)
        }

    def _selected_filters(self) -> tuple[str, str]:
        return (
            self._filters.get(CONF_LANGUAGE, DEFAULTS[CONF_LANGUAGE]),
            self._filters.get(CONF_SEX, DEFAULTS[CONF_SEX]),
        )

    async def _async_check_persona_input(
        self, user_input: dict, personas: list[str], back_step
    ):
        """Return the next flow result for persona-step input, or None if valid."""
        if user_input.pop(CONF_CHANGE_FILTERS, False):
            # Keep whatever speed/format/volume the user already set; the
            # persona itself is dropped, since it may not match whatever
            # accent/sex they pick next.
            self._persona_prefill = {
                k: v for k, v in user_input.items() if k != CONF_PERSONA
            }
            return await back_step()

        selected_persona = user_input.get(CONF_PERSONA)
        if not selected_persona or not str(selected_persona).strip():
            return self.async_show_form(
                step_id="persona",
                data_schema=_persona_schema(
                    personas, *self._selected_filters(), user_input
                ),
                errors={CONF_PERSONA: "persona_required"},
            )
        # CONF_PERSONA is already the technical code (the selector option
        # value), so no display-name reverse-mapping is needed.
        return None


class KokoroConfigFlow(_VoiceStepsMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kokoro TTS with dynamic discovery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._base_info: dict[str, Any] = {}
        self._discovered: dict[str, list[str]] = {}
        self._filters: dict[str, Any] = {}
        self._persona_prefill: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow."""
        return KokoroOptionsFlow()

    async def _async_check_server(self, base_url: str, api_key: str) -> dict[str, str]:
        """Test the server, keeping its model list for the filters step."""
        errors, models = await async_fetch_models(
            async_get_clientsession(self.hass), base_url, api_key
        )
        if not errors:
            self._discovered["models"] = models or [DEFAULTS[CONF_MODEL]]
        return errors

    async def async_step_user(self, user_input: dict | None = None):
        """Handle base connection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base = (user_input.get(CONF_BASE_URL) or "").strip().rstrip("/")
            api_key = user_input.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            errors = _validate_base_url(base) or await self._async_check_server(
                base, api_key
            )
            if not errors:
                self._base_info = {CONF_BASE_URL: base, CONF_API_KEY: api_key}
                return await self.async_step_filters()

        return self.async_show_form(
            step_id="user", data_schema=_base_schema(user_input), errors=errors
        )

    async def async_step_filters(self, user_input: dict | None = None):
        """Handle model/accent/sex filter selection with dynamic discovery."""
        if "personas" not in self._discovered:
            self._discovered["personas"] = await _discover_voices(
                async_get_clientsession(self.hass),
                self._base_info[CONF_BASE_URL],
                self._base_info.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY]),
            )

        if user_input is not None:
            self._store_filters(user_input)
            return await self.async_step_persona()

        # Pre-fill from the last-submitted filters (e.g. when the user comes
        # back here via the persona step's "Change Voice Accent / Sex").
        return self.async_show_form(
            step_id="filters",
            data_schema=_filters_schema(
                self._discovered.get("models", []), self._filters
            ),
        )

    async def async_step_persona(self, user_input: dict | None = None):
        """Handle persona and audio settings, filtered by the previous step."""
        personas = self._discovered.get("personas", [])

        if user_input is not None:
            if result := await self._async_check_persona_input(
                user_input, personas, self.async_step_filters
            ):
                return result

            # Merge base info, filters and persona/audio settings.
            data = {**self._base_info, **self._filters, **user_input}
            base_url = self._base_info[CONF_BASE_URL]
            await self.async_set_unique_id(_calc_unique_id(base_url))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=_entry_title(base_url), data=data)

        return self.async_show_form(
            step_id="persona",
            data_schema=_persona_schema(
                personas, *self._selected_filters(), self._persona_prefill
            ),
        )

    async def async_step_reconfigure(self, user_input: dict | None = None):
        """Change the server URL or API key of an existing entry.

        The entry's unique ID follows the new URL (so adding that server
        again is refused); its entities are bound to the entry ID and stay.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base = (user_input.get(CONF_BASE_URL) or "").strip().rstrip("/")
            api_key = user_input.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            errors = _validate_base_url(base) or await self._async_check_server(
                base, api_key
            )
            if not errors:
                new_id = _calc_unique_id(base)
                if any(
                    other.unique_id == new_id
                    for other in self._async_current_entries(include_ignore=False)
                    if other.entry_id != entry.entry_id
                ):
                    return self.async_abort(reason="already_configured")
                # Only retitle entries still carrying the generated title.
                title = entry.title
                if title == _entry_title(entry.data[CONF_BASE_URL]):
                    title = _entry_title(base)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_id,
                    title=title,
                    data_updates={CONF_BASE_URL: base, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_base_schema(user_input or dict(entry.data)),
            errors=errors,
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
            errors = await self._async_check_server(
                self._base_info[CONF_BASE_URL], api_key
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_API_KEY, default=self._base_info.get(CONF_API_KEY, "")
                    ): _PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )


class KokoroOptionsFlow(_VoiceStepsMixin, config_entries.OptionsFlowWithReload):
    """Handle options flow for Kokoro TTS; saving reloads the entry once."""

    def __init__(self) -> None:
        """Initialize options flow.

        No config_entry argument: Home Assistant supplies self.config_entry on
        the base class, and passing it explicitly has been deprecated.
        """
        self._filters: dict[str, Any] = {}
        self._discovered: dict[str, list[str]] = {}
        self._persona_prefill: dict[str, Any] = {}

    async def _async_discover(self) -> tuple[list[str], list[str]]:
        """Discover models/personas once per flow session, cached."""
        if "models" not in self._discovered:
            session = async_get_clientsession(self.hass)
            base_url = self.config_entry.data[CONF_BASE_URL]
            api_key = self.config_entry.data.get(CONF_API_KEY, DEFAULTS[CONF_API_KEY])
            _errors, models = await async_fetch_models(session, base_url, api_key)
            self._discovered = {
                "models": models or [DEFAULTS[CONF_MODEL]],
                "personas": await _discover_voices(session, base_url, api_key),
            }
        return self._discovered["models"], self._discovered["personas"]

    async def async_step_init(self, user_input: dict | None = None):
        """Handle the model/accent/sex filter step."""
        models, _personas = await self._async_discover()

        if user_input is not None:
            self._store_filters(user_input)
            return await self.async_step_persona()

        # Pre-fill from the in-session filters if we're back here via the
        # persona step's "Change Voice Accent / Sex", otherwise from the
        # stored entry.
        if self._filters:
            prefill = self._filters
        else:
            data = {**self.config_entry.data, **(self.config_entry.options or {})}
            prefill = {
                key: data.get(key, DEFAULTS[key])
                for key in (CONF_MODEL, CONF_LANGUAGE, CONF_SEX)
            }
        return self.async_show_form(
            step_id="init", data_schema=_filters_schema(models, prefill)
        )

    async def async_step_persona(self, user_input: dict | None = None):
        """Handle persona and audio settings, filtered by the previous step."""
        _models, personas = await self._async_discover()
        selected_language, selected_sex = self._selected_filters()

        if user_input is not None:
            if result := await self._async_check_persona_input(
                user_input, personas, self.async_step_init
            ):
                return result
            return self.async_create_entry(title="", data={**self._filters, **user_input})

        # Pre-fill audio settings from the stored entry. Only pre-fill the
        # persona itself if it still matches the filters just chosen -
        # otherwise leave it blank so the list forces an explicit pick.
        data = {**self.config_entry.data, **(self.config_entry.options or {})}
        prefill: dict[str, Any] = {
            CONF_SPEED: data.get(CONF_SPEED, DEFAULTS[CONF_SPEED]),
            # A stored "pcm" is no longer a choice; offering it fails the form.
            CONF_FORMAT: playable_format(data.get(CONF_FORMAT) or DEFAULTS[CONF_FORMAT]),
            CONF_VOLUME_MULTIPLIER: data.get(
                CONF_VOLUME_MULTIPLIER, DEFAULTS[CONF_VOLUME_MULTIPLIER]
            ),
        }
        stored_persona = data.get(CONF_PERSONA)
        if stored_persona:
            # Classify with the same helper the list filter uses, so a voice
            # the server reports but PERSONA_MAPPINGS doesn't know still gets
            # matched against the filters rather than always being offered.
            info = PERSONA_MAPPINGS.get(stored_persona) or derive_persona_info(
                stored_persona
            )
            if info is None:
                # Unclassifiable (e.g. a blended voice) - always offer it back.
                prefill[CONF_PERSONA] = stored_persona
            else:
                persona_language, persona_sex, _name = info
                if selected_language in ("All Languages", persona_language) and (
                    selected_sex in ("All", persona_sex)
                ):
                    prefill[CONF_PERSONA] = stored_persona

        # Overlay any in-session edits from a "Change Voice Accent / Sex"
        # round trip - these are fresher than what's stored on the entry.
        prefill.update(self._persona_prefill)

        return self.async_show_form(
            step_id="persona",
            data_schema=_persona_schema(personas, selected_language, selected_sex, prefill),
        )
