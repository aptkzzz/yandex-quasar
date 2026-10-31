"""Media player: playback of URLs on the station (Music Assistant, media sources, TTS announcements)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import YandexQuasarConfigEntry
from .client import AdapterClient, AdapterError
from .entity import YandexQuasarEntity, setup_station_entities

PARALLEL_UPDATES = 0

STATES = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
    "idle": MediaPlayerState.IDLE,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: YandexQuasarConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_station_entities(entry, async_add_entities, lambda client, device_id: [
        YandexQuasarMediaPlayer(client, device_id)
    ])


class YandexQuasarMediaPlayer(YandexQuasarEntity, MediaPlayerEntity):
    _attr_translation_key = "player"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(self, client: AdapterClient, device_id: str) -> None:
        super().__init__(client, device_id, "media_player")

    @property
    def _player(self) -> dict[str, Any]:
        return self.station_state.get("player") or {}

    @property
    def state(self) -> MediaPlayerState:
        return STATES.get(self._player.get("state", "idle"), MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        return self.station_state.get("volume")

    @property
    def volume_step(self) -> float:
        return 0.1

    @property
    def is_volume_muted(self) -> bool:
        return bool(self.station_state.get("muted"))

    @property
    def media_content_id(self) -> str | None:
        return self._player.get("url")

    @property
    def media_title(self) -> str | None:
        return self._player.get("title")

    @property
    def media_artist(self) -> str | None:
        return self._player.get("artist")

    @property
    def media_image_url(self) -> str | None:
        return self._player.get("image")

    @property
    def media_duration(self) -> float | None:
        return self._player.get("duration")

    @property
    def media_position(self) -> float | None:
        return self._player.get("position") if self._player.get("url") else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        ts = self._player.get("position_ts")
        return datetime.fromtimestamp(ts, UTC) if ts and self._player.get("url") else None

    async def _request(self, type_: str, data: dict[str, Any] | None = None) -> dict:
        try:
            return await self._client.request(type_, self._device_id, data)
        except AdapterError as err:
            raise HomeAssistantError(f"{type_} failed: {err}") from err

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        url = async_process_play_media_url(self.hass, media_id)
        if kwargs.get("announce"):
            await self._request("voice.announce", {"media_url": url, "text": "", "listen": False})
            return
        meta = (kwargs.get("extra") or {}).get("metadata") or {}
        await self._request("media.play", {
            "url": url,
            "metadata": {
                "title": meta.get("title"),
                "artist": meta.get("artist"),
                "image": meta.get("imageUrl") or meta.get("image"),
                "duration": meta.get("duration"),
            },
        })

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass, media_content_id, content_filter=lambda item: item.media_content_type.startswith("audio/")
        )

    async def async_media_play(self) -> None:
        await self._request("media.resume")

    async def async_media_pause(self) -> None:
        await self._request("media.pause")

    async def async_media_stop(self) -> None:
        await self._request("media.stop")

    async def async_media_next_track(self) -> None:
        # Works while Music Assistant streams over Sendspin (its queue); plain URLs have no next track.
        await self._request("media.next")

    async def async_media_previous_track(self) -> None:
        await self._request("media.previous")

    async def async_media_seek(self, position: float) -> None:
        await self._request("media.seek", {"position": position})

    async def async_set_volume_level(self, volume: float) -> None:
        await self._request("volume.set", {"level": volume})

    async def async_volume_up(self) -> None:
        await self._request("volume.step", {"direction": "up"})

    async def async_volume_down(self) -> None:
        await self._request("volume.step", {"direction": "down"})

    async def async_mute_volume(self, mute: bool) -> None:
        await self._request("volume.mute", {"muted": mute})
