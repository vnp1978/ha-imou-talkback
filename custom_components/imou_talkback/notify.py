"""
Imou Talkback — notify platform cho Home Assistant.

Cấu hình trong configuration.yaml:
    notify:
      - platform: imou_talkback
        name: imou_talkback
        host: homeassistant.local   # hoặc IP
        port: 8765
        api_key: "your-api-key"

Dùng trong automation:
    service: notify.imou_talkback
    data:
      message: "Có người ở cổng"
      target: "cam_cong"            # tên camera (bỏ trống = dùng camera đầu tiên)
"""
from __future__ import annotations

import logging
import requests

import voluptuous as vol
from homeassistant.components.notify import (
    ATTR_TARGET,
    PLATFORM_SCHEMA,
    BaseNotificationService,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_API_KEY
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8765
TIMEOUT      = 10  # seconds

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_API_KEY): cv.string,
    }
)


def get_service(hass, config, discovery_info=None):
    """Khởi tạo notify service."""
    host    = config[CONF_HOST]
    port    = config[CONF_PORT]
    api_key = config[CONF_API_KEY]
    return ImouTalkbackNotificationService(host, port, api_key)


class ImouTalkbackNotificationService(BaseNotificationService):
    """Gửi TTS tới Add-on qua HTTP."""

    def __init__(self, host: str, port: int, api_key: str) -> None:
        self._url     = f"http://{host}:{port}/speak"
        self._api_key = api_key

    def send_message(self, message: str = "", **kwargs) -> None:
        """
        message = nội dung TTS
        kwargs["target"] = tên camera (string hoặc list[str])
        """
        targets = kwargs.get(ATTR_TARGET)

        # target có thể là list hoặc string
        if isinstance(targets, list):
            camera = targets[0] if targets else ""
        elif isinstance(targets, str):
            camera = targets
        else:
            camera = ""

        payload = {"text": message, "camera": camera}

        try:
            resp = requests.post(
                self._url,
                json=payload,
                headers={"X-API-Key": self._api_key},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            _LOGGER.debug("Imou Talkback OK: %s", resp.json())
        except requests.exceptions.ConnectionError:
            _LOGGER.error(
                "Không kết nối được Imou Talkback tại %s — Add-on đang chạy không?",
                self._url,
            )
        except requests.exceptions.Timeout:
            _LOGGER.error("Imou Talkback timeout sau %ss", TIMEOUT)
        except requests.exceptions.HTTPError as err:
            _LOGGER.error("Imou Talkback lỗi HTTP: %s", err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Imou Talkback lỗi: %s", err)
