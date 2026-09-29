"""
Imou Talkback — media_player platform v1.3.0

Cấu hình configuration.yaml:
    media_player:
      - platform: imou_talkback
        host: 192.168.10.247
        port: 8765
        api_key: "your-api-key"

Features:
  - Play/Pause/Stop/Volume
  - Browse Media (Cast)
  - Auto-detect camera mới thêm từ Web UI
  - Camera bị xóa → unavailable
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    BrowseMedia,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_API_KEY
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT  = 8765
SCAN_INTERVAL = timedelta(seconds=30)
TIMEOUT_SHORT = aiohttp.ClientTimeout(total=5)
TIMEOUT_LONG  = aiohttp.ClientTimeout(total=60)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Required(CONF_API_KEY): cv.string,
})

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.BROWSE_MEDIA
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    host     = config[CONF_HOST]
    port     = config[CONF_PORT]
    api_key  = config[CONF_API_KEY]
    base_url = f"http://{host}:{port}"

    known: dict[str, ImouTalkbackMediaPlayer] = {}

    async def _fetch_status() -> list[dict] | None:
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT_SHORT) as session:
                async with session.get(
                    f"{base_url}/api/status",
                    headers={"X-API-Key": api_key},
                ) as resp:
                    return await resp.json()
        except Exception as e:
            _LOGGER.warning("Không kết nối Imou Talkback: %s", e)
            return None

    async def _sync_cameras(now=None):
        cameras = await _fetch_status()

        if cameras is None:
            for entity in known.values():
                entity.set_unavailable()
            return

        current_names = {cam["name"] for cam in cameras}

        # Camera mới → tạo entity
        new_entities = []
        for cam in cameras:
            if cam["name"] not in known:
                entity = ImouTalkbackMediaPlayer(hass, cam, base_url, api_key)
                known[cam["name"]] = entity
                new_entities.append(entity)
                _LOGGER.info("Imou Talkback: camera mới — %s", cam["name"])

        if new_entities:
            async_add_entities(new_entities, update_before_add=True)

        # Cập nhật state
        for cam in cameras:
            entity = known.get(cam["name"])
            if entity:
                entity.update_from_api(cam)

        # Camera bị xóa → unavailable
        for name in list(known.keys()):
            if name not in current_names:
                known[name].set_unavailable()

    await _sync_cameras()
    async_track_time_interval(hass, _sync_cameras, SCAN_INTERVAL)


class ImouTalkbackMediaPlayer(MediaPlayerEntity):
    """Đại diện cho 1 camera Imou trong HA."""

    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features  = SUPPORTED_FEATURES
    _attr_should_poll         = False

    def __init__(self, hass, cam: dict, base_url: str, api_key: str):
        self.hass      = hass
        self._cam      = cam
        self._base_url = base_url
        self._api_key  = api_key
        self._state    = MediaPlayerState.IDLE
        self._volume   = 1.0
        self._muted    = False

        slug = cam["name"].lower().replace("-", "_").replace(" ", "_")
        self._attr_name      = f"Imou {cam['name']}"
        self._attr_unique_id = f"imou_talkback_{slug}"

    @property
    def state(self) -> MediaPlayerState:
        return self._state

    @property
    def volume_level(self) -> float:
        return 0.0 if self._muted else self._volume

    @property
    def is_volume_muted(self) -> bool:
        return self._muted

    def set_unavailable(self):
        self._state = MediaPlayerState.UNAVAILABLE
        self.schedule_update_ha_state()

    def update_from_api(self, cam_data: dict):
        state_str = cam_data.get("state", "idle")
        volume    = cam_data.get("volume", 1.0)

        new_state = {
            "playing": MediaPlayerState.PLAYING,
            "paused":  MediaPlayerState.PAUSED,
            "idle":    MediaPlayerState.IDLE,
        }.get(state_str, MediaPlayerState.IDLE)

        changed = (self._state != new_state or self._volume != volume)
        self._state  = new_state
        self._volume = volume

        if changed:
            self.schedule_update_ha_state()

    async def _post(self, endpoint: str, data: dict = None):
        """Helper POST lên Add-on."""
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT_SHORT) as session:
                async with session.post(
                    f"{self._base_url}{endpoint}",
                    json={**(data or {}), "camera": self._cam["name"]},
                    headers={"X-API-Key": self._api_key},
                ) as resp:
                    return await resp.json()
        except Exception as e:
            _LOGGER.error("POST %s lỗi: %s", endpoint, e)
            return None

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Nhận URL audio từ HA → gửi lên Add-on queue."""
        _LOGGER.debug("play_media: cam=%s url=%s", self._cam["name"], media_id[:80])

        # Resolve media-source:// thành URL thực
        if media_id.startswith("media-source://"):
            from homeassistant.components.media_source import async_resolve_media
            play_item = await async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = async_process_play_media_url(self.hass, play_item.url)

        await self._post("/play_audio", {
            "url":    media_id,
            "camera": self._cam["name"],
        })

        self._state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        await self._post("/api/stop")
        self._state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        await self._post("/api/pause")
        self._state = MediaPlayerState.PAUSED
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        await self._post("/api/resume")
        self._state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        self._volume = volume
        self._muted  = False
        await self._post("/api/volume", {"volume": volume})
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self._muted = mute
        vol = 0.0 if mute else self._volume
        await self._post("/api/volume", {"volume": vol})
        self.async_write_ha_state()

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        from homeassistant.components.media_source import (
            async_browse_media as source_browse,
        )
        return await source_browse(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio"),
        )
