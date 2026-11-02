# Протокол станции (сторона «облака»)

Справочник по тому, что адаптер должен отвечать станции. Собран по quasard (JSON и AAPI), quasar-server (AAPI),
quasar-protos и вики hackalice.me. Всё, что помечено **[проверить]**, нужно подтвердить на реальной прошивке на этапе 0
и записать в `docs/device-notes.md`.

## 1. HTTP-бэкенд

Маршрутизация идёт по пути. Хост берётся из `Host` или из префикса `/<host>/…` (режим nginx-прокси v3rt3p). На любой
неизвестный путь отвечаем `200 {"status":"ok"}` и пишем в лог: так станция не зацикливается на ретраях.

| Хост | Метод и путь | Ответ |
|---|---|---|
| `scbh.yandex.net` | `GET /generate_204` | `204`. **Обязателен**: без него станция считает, что интернета нет |
| `oauth.yandex.ru`, `mobileproxy.passport.yandex.net` | `POST /token`, `POST /1/token` | `{access_token, refresh_token, token_type:"bearer", expires_in, uid}`; для `x-token` возвращаем тот же токен |
| `mobileproxy.passport.yandex.net` | `GET /1/bundle/account/short_info` | фейковый профиль |
| `startup.mobile.yandex.net` | `GET /analytics/startup` | `{device_id:{hash}, uuid:{value}, query_hosts:{…}}` (или 500) |
| `report.appmetrica.yandex.net` | `POST /report` | `{"status":"accepted"}` (или 500) |
| `clck.yandex.net` | `POST /quasar_metrics/write/batch` | protobuf `0A 02 4F 4B`; json-вариант `[{"ResponseStatus":"OK"}]` |
| `rpc.alice.yandex.net` | `POST /gproxy/push_events_batch` | `200`, пустое тело |
| `quasar.yandex.net` | `POST /register` | `{"status":"ok","hasCriticalUpdate":false}`, сохраняем platform/firmware |
| | `GET /check_updates` | `{"hasUpdate":false}` |
| | `GET /allow_update`, `POST /tv/api/v4\|v5/updates` | отказ (`{"is_allowed":false}`, пустые списки) |
| | `POST /push_subscribe` | `{"status":"ok","sign":…,"ts":…}` |
| | `GET /get_sync_info` | конфиг станции, см. ниже |
| | `GET /get_timezone`, `GET /get_user_details` | заглушки |
| | `POST /update_device_state` | `{"status":"ok"}`, сохраняем `networkInfo` (IP станции нужен glagol-клиенту) |
| | `GET /glagol/token` | `{"status":"ok","token": JWT HS256 {sub:device_id, plt, iss:"quasar-backend", aud:"glagol", exp}}` |
| | `POST /glagol/check_token`, `/glagol/v2.0/check_token` | `{"status":"ok","valid":true,"owner":true,"guest":false}`. Content-type странный, тело читаем как raw |
| | `GET /glagol/device_list`, `/glagol/family_device_list` | список наших станций с `glagol.security` |

### 1.1 `get_sync_info`

Главный рычаг управления станцией. Каркас:

```json
{"status":"ok",
 "glagol":{"security":{"server_certificate":"…","server_private_key":"…"}},
 "config":{
  "account_config":{"spotter":"alisa","jingle":false,"smartActivation":true},
  "device_config":{"locale":"ru-RU","name":"Кухня","location":{"latitude":0,"longitude":0},"dndMode":{"enabled":false}},
  "system_config":{
    "uniProxyUrl":"ws://<adapter>:<port>/uni.ws",
    "voiceDialogSettings":{"useProtoAliceApi":false},
    "onlineSpotterEnabled":false,
    "unbound":{"forwarders":[],"serverParams":[]},
    "com.yandex.launcher.updaterapp":{"settings":{"allowFirmwareUpdate":{"ota":false}}}}},
 "subscription":{"mode":"transaction"},"tags":[]}
```

- `glagol.security` — самоподписанный сертификат `localhost` (RSA 2048) для glagol-сервера станции. Генерируется один
  раз на станцию и хранится в data-dir адаптера.
- `useProtoAliceApi:false` пробует удержать станцию на JSON-протоколе. Прошивки 3.x, похоже, всё равно говорят AAPI
  **[проверить]**.
- Изменение конфига применяется директивой `push_update_config`: станция перечитывает `get_sync_info`.
- Лучший источник полей — реальный `get_sync_info`, снятый со своей станции (HAR/mitm). Адаптер умеет мержить
  captured-JSON с нашими переопределениями.

## 2. uniproxy (`/uni.ws`)

Заголовки апгрейда: `X-UPRX-DEVICE-ID`, `X-UPRX-DEVICE-MODEL`, `X-UPRX-UUID`, `X-UPRX-AUTH-TOKEN` (не проверяем).
`device_id` — ключ сессии. У одной станции может быть несколько соединений, активно последнее.

### 2.1 Кадры

| Вариант | Определение | Формат |
|---|---|---|
| JSON (~1.x) | текстовые WS-кадры | `{"event":{"header":{namespace,name,messageId[,streamId]},"payload":{…}}}`; ответ — `{"directive":{…}}`; управление потоком — `{"streamcontrol":{action,reason,streamId,messageId}}` |
| AAPI (3.x) | бинарный кадр с префиксом `b"AAPI"` | `AAPI` + `NAlice.NAliceApi.TClientMessage` / `TServerMessage` (`alice/protos/api/alicekit/protocol/…`). Текстовые кадры игнорируются |
| Аудио (оба) | бинарный кадр без префикса `AAPI` | `[streamId: uint32 BE][данные]`. Потоки клиента нечётные (микрофон — 1), сервера — чётные (JSON: 2, 4, …; quasar-server начинает с 1025) |

Протокол сессии определяется по первому кадру. Кодек приводит всё к внутренней модели (`uniproxy/model.py`):
`VoiceInput`, `TextInput`, `ServerActionCallback`, `StreamControl`, `SynchronizeState`, `LogSpotter`, `DeviceState`;
в обратную сторону — `AsrResult`, `AliceResponse`, `TtsSpeak`, `Push`, `SyncPush`, `StreamClose`.

### 2.2 События станции и реакция

| Событие | Ответ |
|---|---|
| `System.SynchronizeState` | `System.SynchronizeStateResponse {SessionId}` + `System.Push(notify)` (JSON). В AAPI quasar-server не отвечает, и это работает |
| `System.EchoRequest` | `System.EchoResponse` с тем же payload |
| `System.Ack`, `System.Ping`, `Log.*`, `Spotter.*` | игнор |
| `Log.Spotter` / AAPI `LogSpotter` | **обязательно** `Log.Ack` / `LogAck` |
| `*.RequestStat` | `Log.RequestStatAck`, иначе повторяет |
| `Vins.VoiceInput` / AAPI `VoiceInput` | открыть голосовой ход (§3) |
| `Vins.TextInput`/`VinsRequest`/`CustomInput` с `event.type=server_action` | колбэк: бумеранг, `@@mm_stack_engine_get_next`, `on_started`/… плеера, кнопка Play на корпусе (`@@mm_semantic_frame` MusicPlay/PlayerContinue) |
| `Vins.CancelRequest` | отменить текущий ход |
| неизвестный `Vins.*` | пустой `VinsResponse` |

### 2.3 StreamControl

- `action`: `Close=0`, `SpotterEnd=2`, `LazyTTS=3` (JSON).
- `reason` (JSON-коды quasard): `OK=0`, `INTERNAL_ERROR=2`, `STOP_RECORDING=5`, `AUDIO_SOURCE_STOPPED=6`, `CANCEL=7`,
  `NO_SPEECH=8`. В AAPI enum порядковый: `OK, INTERNAL_ERROR, STOP_RECORDING, AUDIO_SOURCE_STOPPED, CANCEL, NO_SPEECH`.
  **Маппинг держать в кодеке, не смешивать.**
- `Close` с `CANCEL` — ход отменён (HA: abort pipeline). С `NO_SPEECH` — пустой ответ. С остальными — завершаем
  распознавание тем, что есть. В quasar-server `Close(OK)` игнорируется из-за бага, у нас так быть не должно.

## 3. Голосовой ход

```
-> VoiceInput (streamId=1, messageId=M, header{request_id, sequence_number, dialog_id}, device_state)
<- InputStartAck                                   (JSON)
-> [1][Ogg/Opus 16k] ... пре-ролл «Алиса» ~2 с
-> streamcontrol SpotterEnd                        (JSON; в AAPI — [проверить])
-> [1][Ogg/Opus 16k] ... команда (новый Ogg BOS в том же streamId)
<- ASR.Result endOfUtt=false (каждые ~300 мс)
<- ASR.Result endOfUtt=true {recognition:[{normalized:text, words:[…]}]}
<- streamcontrol close(1)
<- Spotter.Validation {valid:1, result:1}          (если enable_spotter_validation)
<- Vins.VinsResponse / AliceResponse  (refMessageId=M, request_id, sequence_number, dialog_id — зеркалим)
<- TTS.Speak {format:"audio/opus", streamId=2}
<- [2][Ogg/Opus 48k mono] ... (куски ≤32 КБ по границам Ogg-страниц)
<- streamcontrol close(2)
```

Тонкости:

- Сигнал после VQE тихий (пик около −40 dBFS). Нормализацию делать в адаптере перед отправкой в HA
  (настраиваемый gain/AGC). Иначе STT в HA проседает.
- Пре-ролл с wake word до `SpotterEnd` в HA не отправлять (плюс ~300 мс хвоста), иначе STT распознает «Алиса …».
  Дополнительно вырезаем ведущее «Алиса»/«Яндекс» из текста STT, если он попадёт в ответ.
- Ogg-страницы могут не совпадать с границами WS-кадров. Нужен инкрементальный демуксер, а не разбор «кадр = страница».
  Пакеты Opus собирать с учётом lacing (сегменты 255 продолжают пакет).
- В `VinsResponse` при наличии речи **обязательна** директива `tts_play_placeholder {channel:"Dialog"}`, иначе станция
  не проиграет TTS. `directives_execution_policy: "BeforeSpeech"`.
- `should_listen` / `ShouldListen` → станция после речи снова откроет микрофон (follow-up).
- `VinsResponse` строго до `TTS.Speak`.
- Если ответ не готов долго (медленная LLM), есть приём quasar-server с промежуточным ответом и
  `continue_session_stage1/2` через `@@mm_semantic_frame`. В MVP не нужен.

Пример JSON `VinsResponse` (минимум):

```json
{"directive":{"header":{"namespace":"Vins","name":"VinsResponse","messageId":"…","refMessageId":"<M>"},
 "payload":{"header":{"response_id":"…","request_id":"<req>","sequence_number":0,"session_id":"<ssid>"},
  "response":{"directives_execution_policy":"BeforeSpeech",
   "directives":[{"name":"tts_play_placeholder","type":"client_action","payload":{"channel":"Dialog"},"is_led_silent":true}],
   "card":{"type":"simple_text","text":"Включаю свет на кухне"}},
  "voice_response":{"has_voice_response":true,"should_listen":false,
   "output_speech":{"type":"simple","text":"Включаю свет на кухне"}}}}}
```

## 4. Push и SyncPush (инициатива сервера)

- **Push** — `System.Push` (JSON) / `Event.Push {Directives, PushIds, DeduplicationPushId}` (AAPI) в открытой
  uniproxy-сессии. Через него выполняется любая client_action-директива без голосового запроса.
- **Бумеранг для TTS**: push `@@mm_semantic_frame {typed_semantic_frame:{tts_semantic_frame:{text}}}` (server_action).
  Станция присылает его обратно как TextInput, и в ответ на него можно отдать VinsResponse + TTS.Speak. Мы подставляем
  свой `turn_id` в payload и по нему находим подготовленный ход. **[проверить на JSON-прошивке]**
- **SyncPush** (AAPI): `SyncRequest.GetDeviceState` → `SyncPushResponse.GetDeviceStateResponse` (сопоставление по
  `RefMessageId`, таймаут 5 с). `ExecuteDirective` с политикой OnStarted/OnFinished даёт подтверждение выполнения.

## 5. Директивы (client_action)

| Директива | Payload | Для чего |
|---|---|---|
| `tts_play_placeholder` | `{channel:"Dialog"}` | место TTS в ответе |
| `audio_play` | `{stream:{id,type:"Track",url,format:"MP3",offset_ms}, metadata:{title,subtitle,art_image_url}, callbacks:{on_started,on_stopped,on_finished,on_failed}, background_mode:"Ducking", screen_type:"Music"}` | воспроизведение URL. Редирект 308 станция не проходит, адаптер раскрывает редиректы сам |
| `@@mm_stack_engine_get_next` (server_action) | наш payload | станция вернёт его, когда трек закончится или скажут «дальше» |
| `player_pause` | `{smooth:true}` | пауза |
| `player_continue` | — | ❌ на Midi не работает для `audio_play` (см. device-notes). Resume = `audio_play` с `offset_ms` |
| `player_next_track` / `player_previous_track` | — | next/prev |
| `clear_queue` | — | стоп |
| `audio_player_rewind` | `{amount_ms, type:"Forward"\|"Backward"\|"Absolute"[проверить]}` | seek |
| `sound_set_level` | `{new_level:0..10, new_percent_level}` | громкость (грубо, шаг 10%) |
| `sound_louder` / `sound_quiter` | — | шаг громкости («quiter» — так у Яндекса) |
| `sound_mute` / `sound_unmute` | — | mute |
| `local_audio_file_play` | `{sound_to_play:"…", play_times:1}` | штатные звуки |
| `start_bluetooth` / `stop_bluetooth` | — | BT |
| `push_update_config` | — | перечитать `get_sync_info` |

Typed-директивы (таймеры, будильники, DND) передаются только как `PayloadRaw` proto, JSON-вариант прошивка не принимает.
Нативные таймеры (`NAlice.TAlarmCapability`): `timer_set_directive` 112, `timer_cancel_directive` 113,
`timer_pause_directive` 114, `timer_resume_directive` 115, `timer_stop_directive` 276. В `TTimerSetDirective.Timer`
заданы `TimerId`, `StartTimestamp` (unix, с), `Duration`, `Remaining`, `Title`. Станция показывает отсчёт на часах и
звонит сама, о срабатывании не сообщает (см. device-notes). Будильники: add 32, update 34, snooze 116, stop 118,
cancel 132 (`TAlarmAddDirective.Alarm`: `AlarmId`, `LocalTime`, `ActivationDays`, `Timezone`); билдеры есть в
`aapi.py`, на станции пока не проверены.

## 6. Состояние станции

- `device_state` (JSON) / `Request.DeviceStateRaw` → `TDeviceState` (AAPI) приходит в каждом VoiceInput/TextInput:
  `SoundLevel`, `SoundMuted`, `SoundMaxLevel`, `MicsMuted`, `AudioPlayer{PlayerState, OffsetMs, DurationMs,
  CurrentlyPlaying{StreamId,Title,SubTitle}}`.
- Колбэки `audio_play.callbacks` возвращаются server_action'ами.
- glagol (`wss://<ip>:1961`, mDNS `_yandexio._tcp`): после подключения станция шлёт `state` (`aliceState`,
  `playerState{playing, progress, duration, title, subtitle, id}`, `volume` 0..1, …), команды —
  `{"conversationToken": JWT, "id", "sentTime", "payload":{"command":"setVolume"|"play"|"stop"|"next"|"prev"|"rewind"|"ping"…}}`.
  Формат взят из AlexxIT/YandexStation **[проверить]**. На Станции 3 (orion), по данным вики, старые команды не
  отвечают.
- Локальный IPC (только с устройства, station-agent): `127.0.0.1:9875` `aliced` — `AliceCapability` (подписка на
  состояние, `START_VOCALIZE`), кадр = `uint32 LE длина` + `QuasarMessage`.
