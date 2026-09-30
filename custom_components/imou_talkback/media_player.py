"""Loa camera Imou — media_player entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ImouTalkbackConfigEntry
from .talk import TalkError

_LOGGER = logging.getLogger(__name__)

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouTalkbackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ImouTalkbackPlayer(entry)])


class ImouTalkbackPlayer(MediaPlayerEntity):
    """Loa của một camera Imou."""

    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features  = SUPPORTED_FEATURES
    _attr_should_poll         = False
    _attr_device_class        = "speaker"

    def __init__(self, entry: ImouTalkbackConfigEntry) -> None:
        data = entry.runtime_data
        self._entry   = entry
        self._speaker = data.speaker
        self._attr_name      = f"Imou {data.cam_name}"
        self._attr_unique_id = f"imou_talkback_{entry.entry_id}"
        self._attr_state     = MediaPlayerState.IDLE

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Nhận URL audio từ HA TTS / media source, phát thẳng ra loa camera."""
        if media_source.is_media_source_id(media_id):
            play = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = play.url
        url = async_process_play_media_url(self.hass, media_id)

        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()
        try:
            await self._speaker.async_play_url(url)
        except (TalkError, OSError) as exc:
            _LOGGER.warning("%s: không phát được: %s", self.entity_id, exc)
        finally:
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Dừng phát — đóng phiên nói ngay lập tức."""
        # Speaker không có stop riêng; async_play_url đang chạy trong lock →
        # cách duy nhất là cancel task; tạm thời để HA tự handle qua lock.
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )
