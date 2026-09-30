"""Config Flow — thêm camera qua UI của HA (Settings → Integrations → Add)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .const import DEFAULT_PORT, DEFAULT_USERNAME, DOMAIN
from .talk import AuthError, TalkError, check_login

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({
    vol.Required(CONF_NAME):                              str,
    vol.Required(CONF_HOST):                              str,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    vol.Required(CONF_PASSWORD):                          str,
    vol.Optional(CONF_PORT,     default=DEFAULT_PORT):    int,
})


class ImouTalkbackConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            user = user_input.get(CONF_USERNAME, DEFAULT_USERNAME).strip()
            pw   = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            try:
                await self.hass.async_add_executor_job(
                    check_login, host, user, pw, port
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except (TalkError, OSError) as exc:
                _LOGGER.debug("imou_talkback login check failed: %s", exc)
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_HOST:     host,
                        CONF_USERNAME: user,
                        CONF_PASSWORD: pw,
                        CONF_PORT:     port,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sửa IP / tài khoản / mật khẩu — để trống mật khẩu là giữ cũ."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        schema_sua = vol.Schema({
            vol.Required(CONF_HOST,     default=entry.data[CONF_HOST]):     str,
            vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]):  str,
            vol.Optional(CONF_PASSWORD, default=""):                         str,
            vol.Optional(CONF_PORT,     default=entry.data[CONF_PORT]):      int,
        })

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            user = user_input.get(CONF_USERNAME, DEFAULT_USERNAME).strip()
            pw   = user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]

            try:
                await self.hass.async_add_executor_job(
                    check_login, host, user, pw, port
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except (TalkError, OSError) as exc:
                _LOGGER.debug("imou_talkback reconfigure check failed: %s", exc)
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{port}",
                    data_updates={
                        CONF_HOST:     host,
                        CONF_USERNAME: user,
                        CONF_PASSWORD: pw,
                        CONF_PORT:     port,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema_sua,
            errors=errors,
        )
