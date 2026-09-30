"""Imou Talkback — loa camera Imou/Dahua trong Home Assistant.

Nói thẳng vào camera qua cổng 8086 (AAC 16 kHz) hoặc 37777 (PCM 8 kHz),
không qua add-on trung gian. Phát nhạc dài không bị vấp.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .const import DEFAULT_PORT, DOMAIN
from .http_talk import MoPhienImou
from .speaker import Speaker

PLATFORMS = [Platform.MEDIA_PLAYER]


@dataclass
class ImouTalkbackData:
    speaker: Speaker
    cam_name: str
    cam_host: str


type ImouTalkbackConfigEntry = ConfigEntry[ImouTalkbackData]


async def async_setup_entry(hass: HomeAssistant, entry: ImouTalkbackConfigEntry) -> bool:
    d = entry.data
    mo_phien = MoPhienImou(
        host=d[CONF_HOST],
        username=d[CONF_USERNAME],
        password=d[CONF_PASSWORD],
        port=int(d.get(CONF_PORT, DEFAULT_PORT)),
        ffmpeg=get_ffmpeg_manager(hass).binary,
    )
    entry.runtime_data = ImouTalkbackData(
        speaker=Speaker(hass, mo_phien),
        cam_name=entry.title,
        cam_host=d[CONF_HOST],
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImouTalkbackConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.speaker.async_close()
    return ok
