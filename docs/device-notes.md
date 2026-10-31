# Заметки по устройствам

Здесь только факты, подтверждённые на реальных станциях. Сырые копии конфигов лежат в `captures/device/` (gitignored,
там персональные данные).

## Яндекс Станция Миди (`cucumber`)

Снято 2026-10-28, доступ — `ssh alice` (dropbear `/data/opt/bin/dropbear -p 2222`). ADB `:5555` тоже открыт.

| Параметр | Значение |
|---|---|
| Прошивка | `0.369.1.50.4794663100.20261021.160` (Build 21.10.2026) |
| Device ID | скрыт (`q full-info`) |
| `deviceType` | `cucumber` |
| Ядро | Linux 5.4.180 aarch64 |
| Userspace | `maind` — ELF 32-bit ARM (armhf) |
| Разделы | `/` ext4 **ro** (458 МБ, свободно 90 МБ); `/data` ext4 rw 2.5 ГБ; `/ota` 1.4 ГБ; `/etc/unbound` — tmpfs |
| Роли `launch.json` | `--mediad`, `--telemetry`, `--platformd`, `--fluent-bitd`, `bulbik` (`localhost:9337`) |
| Открытые порты | 1961 (glagol), 2222 (ssh), 5555 (adb) |
| CA-бандл | `/system/vendor/quasar/ca-certificates.crt` (mediad получает его через `CA_CERTIFICATES`) |

### Адреса бэкендов в `quasar.cfg`

| Ключ (секция) | Значение по умолчанию |
|---|---|
| `aliced.uniProxyUrl` | `wss://uniproxy.alice.yandex.net/uni.ws` |
| `aliced.uniProxyQuicUrl` | `uniproxy-quic.alice.yandex.net:8443/uni.ws` |
| `common.backendUrl` | `https://quasar.yandex.net` |
| `authd.codeUrl` / `loginUrl` / `passportUrl` | `https://mobileproxy.passport.yandex.net` / `https://login.yandex.ru` / `https://oauth.yandex.ru` |
| `arpc_client.server_url` | `https://rpc.alice.yandex.net` |
| `common.robotBackendHost` | `scbh.yandex.net` (только хост) |
| `…timeOverHttpUrl` | `http://scbh.yandex.net/generate_204` |
| `fluent-bit.variables.clickdaemonHost` | `clck.yandex.net:443` |
| `teleme3d.appMetricaStartupHost` / `appmetricaFallbackHosts` / `clickdaemon.endpointUri` | `startup.mobile.yandex.net` / `report.appmetrica.yandex.net` / `clck.yandex.net/…` |
| `…pinger.pings[].host` | `quasar.yandex.ru` |
| `DnsFallbackMap` | `quasar.yandex.net → 213.180.193.230` |

Голосовые настройки (`aliced.voiceDialogSettings`): `useProtoAliceApi: true`, **`wsUseZstd: true`**,
`quicZstdDictFile: ${QUASAR}/zstd.dict`, `vadEnabled: false`.
→ Ожидаем протокол **AAPI** и, возможно, zstd-сжатие кадров со словарём **[проверить при захвате]**.

### Облачный конфиг (`/data/quasar/data/user_config.dat`)

Это контейнер: несколько байт заголовка, затем JSON `{account_config, device_config, system_config}`. Важно:

- `system_config` **не переопределяет** `uniProxyUrl`/`backendUrl`, так что правка `quasar.cfg` должна сработать.
- `system_config.unbound.checkHosts = [quasar.yandex.net, uniproxy.alice.yandex.net, scbh.yandex.net]`, `forwarders` — DNS Яндекса.
- `system_config.onlineSpotterEnabled: true`. В нашем `get_sync_info` выключаем.
- `syncd.pollPeriodSec: 1800`: станция перечитывает конфиг каждые 30 мин.
- Этот JSON — хорошая база для нашего `get_sync_info` (мерж с переопределениями).

### LED

- sysfs `/sys/class/leds/cucumber:{red,green,blue}:ring{0..23}`: 24 RGB-светодиода кольца. `max_brightness=255`,
  штатный trigger `ledfb-ring`.
- `cucumber:{red,green,blue}:logo{0..5}`: 6 RGB логотипа, trigger `ledfb-logo`.
- `cucumber:white:clock-*`: сегменты часов.
- Framebuffer: `/dev/fb0` 24×1 (кольцо, 24 bpp), `/dev/fb1` 6×1 (лого). Штатная анимация пишет в fb, а `ledfb-*`
  транслирует её в LED.
- Паттерны: `/system/vendor/quasar/ledpatterns/{clock,oknix,ring}/`.

### Подсветка и кнопки: подтверждено (2026-10-31)

- **Кольцо через framebuffer.** `/dev/fb0` — 24 пикселя по 3 байта, порядок **BGR** (`00 00 FF` = красный).
  После записи кадра нужно «дёрнуть» sync: `echo 1 > /sys/class/leds/cucumber:ringA:sync/brightness` (и `ringB`),
  иначе кольцо не обновляется. Штатный триггер (`ledfb-ring`) не трогаем: анимации Алисы продолжают работать
  поверх нашего кадра.
- **Кнопки.** Все события — 16-байтные `struct input_event` (compat: ядро aarch64, userspace armhf):

  | event | Код | Кнопка |
  |---|---|---|
  | event3 `awinic_sar` | KEY 115 `VOLUMEUP` | «+» (сенсор): короткое ~0,1 с, удержание — повтор громкости в прошивке |
  | event3 | KEY 114 `VOLUMEDOWN` | «−» |
  | event3 | KEY 582 `KEY_VOICECOMMAND` | есть в битовой маске, нажатий не зафиксировано **[проверить]** |
  | event2 `mute` | KEY 113 `MUTE` + SW 14 `SW_MUTE_DEVICE` (1 — микрофон выключен, 0 — включён) | кнопка мьюта, состояние в SW |

  evdev допускает нескольких читателей: maind продолжает обрабатывать кнопки, агент только слушает.

### Входные устройства (`/proc/bus/input/devices`)

| event | Name | Что это |
|---|---|---|
| event0 | `input_btrcu` | BT-пульт |
| event1 | `vad_keypad` | аппаратный VAD |
| event2 | `mute` (gpio-keys) | кнопка/переключатель мьюта: `KEY` + `SW` (бит 14 = `SW_MUTE_DEVICE`) |
| event3 | `awinic_sar` | сенсорные кнопки сверху (коды уточнить через захват) |

### uniproxy: что подтверждено захватом (2026-10-28)

Перенаправление: в `quasar.cfg` правим только `aliced.uniProxyUrl`. До 2026-10-28 20:42 он указывал на Mac
(`ws://192.168.1.132:8080/uni.ws`), теперь на аддон в HA OS: **`ws://192.168.0.50:8080/uni.ws`**.
Бэкап оригинала — `/data/quasar.cfg.orig`. Станция без TLS подключается примерно через 1–2 мин после `q restart`.
Остальной трафик пока идёт в облако.

- **Протокол — AAPI.** Только бинарные кадры `AAPI` + `TClientMessage`, текстовых кадров нет. zstd не используется,
  хотя `wsUseZstd: true`. Станция предлагает `permessage-deflate`, мы его не принимаем, и это работает.
- Заголовки апгрейда: `Accept: application/protobuf`, `User-Agent: WebSocket++/0.8.2`, `X-UPRX-APP-ID: aliced`,
  `X-UPRX-DEVICE-ID`, `X-UPRX-AUTH-TOKEN`, `X-UPRX-CONNECTION-PRIORITY: BackgroundRequest`.
- Первые события после подключения: `SynchronizeStateRequest` (с OAuthToken, Application, SupportedFeatures,
  Experiments) и `TextInput` server_action `@@mm_typed_callback` (`sync_timezone`). Без ответа каждые ~15 с приходит
  `RequestStat` с ошибкой `Vins request timeout elapsed: 15000 ms`.
- **Голосовой запрос:**
  1. `VoiceInput` (`Header.StreamId` = 1, 3, 5, … нечётные, растут на 2; `VoiceInput.Header.RequestId`,
     `SequenceNumber`) + почти сразу `MatchedUser` (биометрия, игнорируем).
  2. Около 64 бинарных кадров `[streamId BE32][Ogg page]` приходят **одной пачкой** вместе с
     `StreamControl.SpotterEnd`: это пре-ролл около 2 с с «Алиса».
  3. После SpotterEnd начинается **новый логический Ogg-поток** (BOS, тот же serial) с живым звуком, примерно 1 кадр
     на 50 мс.
  4. Через ~1,2–1,5 с приходит `ContinueRequest {RequestMessageId}`. Похоже на keepalive или просьбу продолжить,
     игнорируем.
  5. Если сервер молчит, через ~5 с после SpotterEnd станция шлёт `StreamControl.Close{NO_SPEECH}`.
- Аудио: Opus, `OpusHead` input rate 16000, mono. **Очень тихо**: RMS речи около −50…−56 dBFS, пики около −40 dBFS.
  Нужно усиление на 30–40 дБ.
- «Алиса, громче» станция выполняет **локально** (`CancelRequest` с `LocalExecution.ScenarioResponse.ScenarioName=
  fast_command_scenario`).
- **Минимальный ответ работает** (`spike_responder.py`): `InputStartAck` → (через 2,5 с) `AsrResult{EndOfUtt, Recognition}`
  → `StreamControl.Close(mic)` → `AliceResponse{VoiceResponse.OutputSpeech, HasVoiceResponse, Directives:
  [tts_play_placeholder {channel:Dialog}]}` → `TtsSpeak{Format:"audio/opus"}` (Header.StreamId=2) → один бинарный
  кадр с Ogg/Opus 48 кГц (≈18 КБ) → `StreamControl.Close(2)`. Станция проиграла ответ.
  После нашего Close(mic) станция ещё шлёт `Close{CANCEL}` по mic-потоку, это нормально.
- **Окончание речи:** после проигрывания станция шлёт `RequestStat {RequestMessageId = MessageId VoiceInput,
  Timestamps.OnSoundPlayerEndTime, OnSoundPlayerBeginTime, …}`. Пришёл через 3,1 с после отправки звука длиной 2,95 с.
  Это наш сигнал `playback_finished`.
- `SynchronizeStateResponse`, `RequestStatAck` и пустой `AliceResponse` на server_action станция принимает без ошибок.
- **NO_SPEECH-таймер.** Через ~5 с после SpotterEnd станция закрывает микрофон с `NO_SPEECH`, если не получила
  **непустой** partial. Пустые `AsrResult{EndOfUtt:false}` её не удерживают: фраза длиннее 5 с обрывалась.
  Адаптер детектирует речь по уровню (порог −40 dBFS после усиления +30 дБ) и шлёт partial с текстом `…`.
  После этого фраза на 6,8 с прошла целиком (проверено 2026-10-28).
- Уровни после усиления +30 дБ: речь −15…−35 dBFS, тишина −44…−52 dBFS.
- `DeviceStateRaw` (`TDeviceState`, `alice/protos/api/alicekit/common/device_state`) приходит в каждом `VoiceInput`
  и `TextInput`, в том числе в колбэках плеера: `SoundLevel/SoundMaxLevel` (0..10), `SoundMuted`, `MicsMuted`,
  `AudioPlayer{PlayerState, OffsetMs, DurationMs, CurrentlyPlaying{StreamId, Title, SubTitle}}`. Для MVP это основной
  источник состояния, glagol не нужен.
- `VoiceInput.Request.ActivationType`: `spotter` — по wake word (есть `SpotterPhrase`, есть SpotterEnd с
  пре-роллом); `auto_listening` — станция сама открыла микрофон после `ShouldListen` (follow-up,
  start_conversation), wake word и пре-ролла нет.
- После рестарта адаптера станция переподключается через ~1–3 мин (backoff около 2 мин).
- **Dev на Mac: сон.** Если Mac уходит в Idle Sleep, станция не может достучаться до адаптера
  (`Timer Expired` каждые ~2 мин, `PongTimeoutElapsed` на живом соединении). Это подтверждено по `pmset -g log`.
  Адаптер на Mac запускаем под `caffeinate -i -s -w <pid адаптера>`. На RPi/Docker проблемы нет.
- В сети уже есть glagol-клиент станции (`192.168.0.50` → `:1961`), вероятно основной HA с `yandex_station`
  (AlexxIT). Нам не мешает.
- **Хвост wake word.** Первые ~200–300 мс после SpotterEnd — громкий конец «Алиса» («-са»). Whisper превращает его
  в «Так,»/«Да,» в начале фразы. Адаптер отрезает `spotter_tail_ms = 300`. На записях это исправило
  «Да, готовить час» → «Который час?».
- **Качество STT (тестовый стек, CPU Docker на Mac, 2026-10-28)** на 4 реальных фразах после обрезки:
  `small-int8` и `medium-int8` путают слова («Ключи цвета на копне»), ~2 с на фразу; `turbo` распознаёт все
  4 верно, но ~7 с на фразу. Звук станции (VQE, дальнее поле) для Whisper сложный. Владелец использует HA на
  Raspberry Pi 5, поэтому тяжёлые модели не рассматриваем: локально `medium`, иначе облачный STT.

### Push и плеер: что подтверждено (2026-10-28)

Все команды отправлялись как `Event.Push {PushIds, DeduplicationPushId, Directives}` в открытую uniproxy-сессию
без голосового запроса (`POST /_spike/push` сниффера).

| Директива | Результат |
|---|---|
| `audio_play` (Payload Struct: `stream{id,type:"Track",url,format:"MP3",offset_ms}`, `metadata`, `callbacks`) | ✅ Играет. Плеер mediad — GStreamer `souphttpsrc` (`libsoup/2.68.4`, UA `cucumber/<fw> (YandexIO)`), запросы с `Range` и параметром `?vsid=…` к URL |
| `audio_play` с бесконечным MP3 без Content-Length | ✅ Играет (поток в стиле MA flow) |
| `audio_play` с `offset_ms=10000` | ✅ Стартует с 10 с: seek через HTTP Range |
| URL с редиректом 302 | ✅ |
| URL с редиректом **308** | ❌ Бесконечные повторы запроса. **Адаптер должен раскрывать редиректы сам** |
| `callbacks.on_started/on_stopped` (server_action) | ✅ Возвращаются как `TextInput` server_action с нашим `name` и `payload`. Конец трека и замена трека дают **`on_stopped`**, `on_finished` не видели |
| `sound_set_level {new_level:3}` | ✅ Громкость меньше. В логе: platform volume 6, glagol `volume 0.3` |
| `player_pause {smooth:true}` | ✅ Пауза. Через ~10–15 с на паузе плеер сам переходит в STOPPED (`on_stopped`) |
| `player_continue` (`{}` и `{player:"music"}`) | ❌ `Unexpected player type in LegacyPlayerDirectiveHandler.handlePlayerContinue`, текущий `audio_play` отменяется |
| `audio_player_play_directive` (DirectiveType 155, пустой PayloadRaw) | ❌ `Failed to find handler for directive` |
| `@@mm_semantic_frame {typed_semantic_frame:{tts_semantic_frame:{text}}}` (server_action) | ✅ **Бумеранг работает.** Станция возвращает его как `TextInput` с тем же payload. Отвечаем `AliceResponse(tts_play_placeholder)` + `TtsSpeak`, станция говорит и потом шлёт `RequestStat` |

Выводы для реализации:
- resume = повторный `audio_play` того же URL с `offset_ms` = позиция паузы (для бесконечного потока — без offset).
  Нативный resume проверить через glagol `play`, когда будем выдавать glagol-токены сами.
- announce/start_conversation — через бумеранг `tts_semantic_frame`. Текст в payload не важен: кладём туда `turn_id`
  и по нему отдаём подготовленный звук.
- Имена директив прошивки удобно искать через `strings` по `maind` (копия в `/tmp/ys-fw/`, не коммитим).

### Полная отвязка от облака (2026-10-28 21:10)

Все облачные адреса в `quasar.cfg` указывают на аддон `http://192.168.0.50:8080/<сервис>`:
`common.backendUrl` → `/quasar`, `authd.passportUrl/codeUrl/loginUrl` → `/oauth`, `/passport`, `/login`,
`arpc_client.server_url` → `/rpc`, `teleme3d.appMetricaStartupHost` → `/startup`, `appmetricaFallbackHosts` →
`/appmetrica`, `clickdaemon.endpointUri` → `/clck/...`, `ntpd.timeOverHttpUrl` → `/scbh/generate_204`.
`fluent-bitd.fluent-bit.enabled = false`. `uniProxyQuicUrl`, `mediad.apiUrl` и URL звуков/моделей на CDN
не трогали.

- Бэкапы на станции: `/data/quasar.cfg.orig` (заводской), `/data/quasar.cfg.uniproxy-only` (только uniproxy
  на HA), `/data/quasar-cloud-backup.tar` (`account_storage.dat`, `devices.json`, маркеры, `user_config.dat`).
- Исходные данные бэкенда (`sync_info_base.json` из `user_config.dat`, `devices.json`) лежат в
  `/addon_configs/local_quasar_adapter/` на HA.
- **Старт после перезагрузки** (~20 с до uniproxy): `HEAD /scbh/generate_204` → `GET /quasar/get_sync_info` →
  `GET /quasar/glagol/device_list` → uniproxy → `POST /quasar/set_device_capability`. В фоне идёт
  `POST /clck/quasar_metrics/write/batch`. `register` и OAuth при обычном старте не вызываются: станция
  уже зарегистрирована, токены действуют.
- Лог станции: `backend_status: UnknownStatus → Available`, fluent-bit получил `enabled:false`.
- Постоянных соединений с Яндексом нет (наблюдали 2,5 мин). Одно короткое TCP-соединение к
  `2a02:6b8::3:207:443` сразу после загрузки, источник не установлен **[проверить]**.
- glagol `:1961` продолжает работать для интеграции `yandex_station` (HA 192.168.0.50): её токены принимает
  `glagol/check_token` (`glagol_accept_any`).

### glagol как источник состояния (2026-10-31)

- Кнопки, мьют и подсветка обрабатываются станцией локально и наружу не сообщаются: ни в бэкенд
  (`rpc/gproxy/push_events_batch` шлёт только `EndpointStatus: Online`), ни в uniproxy. В логе станции:
  `Button click: volume_up` → `onVolumeChanged … source: button` → `glagol: Changed volume to 0.6`.
- Адаптер подключается к `wss://<станция>:1961` (самоподписанный сертификат, `ssl=False`) с JWT, подписанным
  ключом нашего бэкенда. Станция проверяет его через `/quasar/glagol/v2.0/check_token`. Первое сообщение —
  `{"command":"softwareVersion"}`, раз в 30 с — `ping`. Станция пушит `{"state":{"volume":…}}` при каждом изменении,
  и громкость в HA обновляется сразу после нажатия кнопки (проверено).
- Кнопки и подсветку решили не делать, пока не появится агент на станции: владелец против лишнего софта на колонке.

### Таймаут ожидания ответа (2026-10-31)

- После `AsrResult{EndOfUtt}` станция ждёт ответ **15 с** и потом сама говорит «произошла ошибка»:
  `Vins request timeout elapsed: 15000 ms`. LLM-агент с инструментами MA отвечал за 24 с: станция сдалась,
  а HA всё равно включил музыку.
- В прошивке есть ключ `vinsRequestTimeout` (рядом с настройками speechkit `sendCancelOnDestroy`,
  `enableUpdateTtsDirectives_261`). Бэкенд адаптера отдаёт его со значением 60000 в `system_config` и
  `system_config.voiceDialogSettings`, `push_update_config` → `Config updated`. **[проверить]**, что станция
  его применяет (долгий запрос).
- Адаптер сам закрывает ход через `ha_response_timeout` (55 с) коротким звуком ошибки, а пайплайн HA
  доигрывает своё действие.

### Прочее

- `/etc/hosts` содержит только localhost. Корень ro, и после правки нужен `mount -o remount,rw /`.
- Станция активирована в облаке Яндекса, `user_config.dat` синхронизирован 28.10.
