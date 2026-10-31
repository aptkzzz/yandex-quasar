# Источники

| Источник | Что взято |
|---|---|
| https://hackalice.me/start/ (и `internals/*`, `recipes/*`) — вики yandex-station-extended, github.com/Marker284/yandex-station-extended | архитектура прошивки (maind, роли, IPC-порты), `quasar.cfg` и подмена `backendUrl`/`uniProxyUrl` через `http://`/`ws://`, CA-бандл, список хостов, директивы, LED/кнопки/mute, OTA, автозапуск, toolchain |
| https://github.com/amirilivice/quasard | полный аналог адаптера на Python: HTTP-моки, `get_sync_info`, JSON- и AAPI-протокол uniproxy (`protocol.py`, `uniproxy.py`, `aliceapi.py`, `aliceapi.desc`), Ogg/Opus в обе стороны (`audio.py`), Silero VAD, `audio_play`, генерация CA, симуляторы станции |
| https://github.com/v3rt3p/quasar-server | эмулятор бэкенда на TypeScript (AAPI): роутеры по хостам, Push и SyncPush, бумеранг `@@mm_semantic_frame` для TTS по инициативе сервера, директивы громкости и плеера, glagol JWT |
| https://github.com/v3rt3p/quasar-protos | protobuf из `maind` Станции 2: `alicekit` (client/server message), `TDeviceState`, `alice_state.proto`, `quasar_proto.proto`, `remoting.proto`, capabilities (button, volume, animation) |
| https://contributors.home-assistant.io/core/entities/assist-satellite/ | `AssistSatelliteEntity`, состояния, announce/start_conversation |
| HA core: `components/wyoming/assist_satellite.py`, `esphome/assist_satellite.py`, `voip/assist_satellite.py`, `assist_pipeline/select.py` | шаблоны реализации: аудио-очередь, обработка событий pipeline, TTS по URL, continue_conversation, select'ы pipeline/VAD |
| https://contributors.home-assistant.io/core/entities/media-player/ | `MediaPlayerEntityFeature`, `play_media` с `announce`/`enqueue`, media_source |
| https://www.music-assistant.io/player-support/ha/, `music_assistant/providers/hass_players` | требования MA к HA-плеерам: `PLAY_MEDIA`, flow-стрим, `media_content_id` |
| https://github.com/AlexxIT/YandexStation | формат glagol-протокола (`wss://:1961`, `conversationToken`, команды и `state`) |
| https://github.com/skrashevich/quasar-bridge | идея гибридного режима (проксирование uniproxy) |

Лицензии: перед копированием кода из quasard и quasar-server проверить их лицензии. Протоколы описываем своими словами,
код пишем заново.
