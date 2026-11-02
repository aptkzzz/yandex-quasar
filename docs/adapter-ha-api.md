# Протокол Adapter ↔ Home Assistant

WebSocket `ws://<adapter>:<port>/ha`. Адаптер — сервер, интеграция — клиент: переподключается с backoff
и ping/pong раз в 15 с. Обнаружение: zeroconf `_quasar-adapter._tcp.local.`, TXT `id=<adapter_id>`, `version=<api>`,
`name=<name>`.

Версия протокола: `1`. Несовместимая версия приводит к `ConfigEntryNotReady` и repair issue.

## 1. Кадры

- **Текстовые** — JSON-объект:
  ```json
  {"type": "<тип>", "id": 12, "station": "<device_id>", "data": {…}}
  ```
  `id` указывается только у запросов, ответ на них — `{"type":"result","id":12,"ok":true,"data":{…}}` или
  `{"type":"result","id":12,"ok":false,"error":{"code":"…","message":"…"}}`. События приходят без `id`.
- **Бинарные** — аудио:
  ```
  [1 байт kind][4 байта turn_seq uint32 BE][payload]
  kind=0x01: PCM s16le 16 kHz mono от станции (микрофон)
  ```
  `turn_seq` — короткий номер хода, который выдаётся в `voice.start`. Он нужен, чтобы не тащить строку в каждом кадре.

## 2. Рукопожатие

```
HA → {"type":"hello","id":1,"data":{"token":"…","api":1,"client":"ha/2026.10"}}
A  → {"type":"result","id":1,"ok":true,"data":{"adapter_id":"…","version":"0.1.0","api":1,
       "stations":[ <StationInfo>, … ]}}
```

`StationInfo`:

```json
{"device_id":"…","platform":"yandexstation_2","name":"Кухня","firmware":"…","mac":"…",
 "protocol":"aapi","capabilities":["voice","media","glagol","buttons","led","mic_mute"],
 "wake_words":[{"id":"alisa","name":"Алиса"},{"id":"yandex","name":"Яндекс"}],"active_wake_word":"alisa",
 "state": <StationState>}
```

`capabilities` зависит от того, что доступно: glagol подключён, station-agent подключён и т. п. Интеграция создаёт
сущности по ним.

## 3. События адаптер → HA

| type | data | Смысл |
|---|---|---|
| `station.added` / `station.removed` | `StationInfo` / `{}` | динамические устройства |
| `station.state` | `StationState` (полный снимок или patch с `"partial":true`) | связь, плеер, громкость, aliceState |
| `voice.start` | `{turn_id, turn_seq, wake_word: "Алиса"\|null, reason:"wake_word"\|"follow_up"\|"conversation"\|"button"}` | станция начала слушать; дальше пойдут бинарные PCM-кадры |
| `voice.stop` | `{turn_id, reason:"end"\|"cancel"\|"no_speech"\|"timeout"\|"disconnected"}` | поток микрофона закончен (не по нашей команде) |
| `voice.playback_finished` | `{turn_id}` | станция договорила ответ / announce |
| `media.event` | `{event:"started"\|"stopped"\|"finished"\|"failed", url, error?}` | колбэки `audio_play` |
| `button` | `{button:"action"\|"play"\|"volume_up"\|"volume_down"\|"mute", event:"short_press"\|"long_press"\|"double_press"}` | этап 4 |

`StationState`:

```json
{"connection":"online","uniproxy":true,"glagol":true,"agent":false,"ip":"192.168.1.50",
 "alice_state":"idle",
 "volume":0.4,"muted":false,"mic_muted":false,
 "player":{"state":"playing","url":"http://…","title":"…","artist":"…","image":"…",
           "duration":215.0,"position":12.3,"position_ts":1730000000.123},
 "led":{"on":false,"rgb":[255,255,255],"brightness":255,"effect":null}}
```

`connection`: `online` (uniproxy-сессия есть), `connecting`, `offline`. `alice_state`: `idle`, `listening`,
`thinking`, `speaking`.

## 4. Команды HA → адаптер (запросы с `id`)

### Голос

| type | data | Действие адаптера |
|---|---|---|
| `voice.stt_end` | `{turn_id, text}` | `ASR.Result endOfUtt=true` + close(1). Перестать пересылать PCM |
| `voice.respond` | `{turn_id, text, tts_url\|null, listen: bool}` | VinsResponse(+`should_listen`) + TTS.Speak + Ogg/Opus из `tts_url`. Когда звук доиграл, шлёт `voice.playback_finished` |
| `voice.error` | `{turn_id, code, message}` | закрыть ход, пустой ответ и звук ошибки |
| `voice.abort` | `{turn_id}` | `Vins.CancelRequest`-эквивалент: закрыть поток, без ответа |
| `voice.announce` | `{turn_id, media_url, preannounce_url\|null, text, listen: bool}` | ход по инициативе сервера (бумеранг). `listen=true` даёт start_conversation. Ответ на запрос приходит сразу, окончание — событием `voice.playback_finished` |

### Медиа

| type | data |
|---|---|
| `media.play` | `{url, metadata:{title, artist, album, image, duration}, offset: 0}` |
| `media.pause` / `media.resume` / `media.stop` | `{}` |
| `media.next` / `media.previous` | `{}` |
| `media.seek` | `{position: seconds}` |
| `volume.set` | `{level: 0..1}` |
| `volume.step` | `{direction: "up"\|"down"}` |
| `volume.mute` | `{muted: bool}` |

### Таймеры

Нативные таймеры станции: обратный отсчёт на часах, свой звонок, «Алиса, стоп» гасит звонок локально.
Станция ничего не сообщает о срабатывании, поэтому источник правды — таймеры HA, адаптер только зеркалит их.

| type | data | Директива станции |
|---|---|---|
| `timer.set` | `{timer_id, duration: seconds, title?}` | `timer_set_directive` (отсчёт от текущего времени) |
| `timer.cancel` / `timer.pause` / `timer.resume` | `{timer_id}` | `timer_{cancel,pause,resume}_directive` |
| `timer.stop` | `{timer_id}` | `timer_stop_directive` — заглушить звонящий таймер |

### Этап 4

| type | data |
|---|---|
| `led.set` | `{on, rgb?, brightness?, effect?}` |
| `mic.mute` | `{muted: bool}` |
| `config.wake_word` | `{wake_word: "alisa"\|"yandex"}` → `account_config.spotter` + `push_update_config` |

### Служебные

| type | data |
|---|---|
| `station.get` | `{}` → `StationInfo` |
| `diagnostics` | `{}` → снимок сессий и последних N событий протокола (секреты вырезаны) |

## 5. Правила

- Все команды идемпотентны по `turn_id`: повторный `voice.respond` для закрытого хода возвращает
  `ok:false, code:"turn_closed"`.
- Если станция offline, медиа- и голосовые команды отвечают `code:"station_offline"`. Интеграция превращает это
  в `HomeAssistantError` с переводом.
- После переподключения HA адаптер присылает полный `station.state` по каждой станции. Активные голосовые ходы
  при разрыве соединения с HA отменяются: станция получает пустой ответ.
- URL в командах — абсолютные адреса HA (`internal_url`). Адаптер (TTS) и станция (медиа) должны иметь до них
  сетевой доступ.
