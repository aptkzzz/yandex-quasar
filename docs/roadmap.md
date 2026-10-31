# Roadmap

Каждый этап заканчивается рабочим вертикальным срезом и записью находок в документацию.

## Этап 0. Разведка на устройстве (spike)

Цель: снять неизвестные до того, как писать основной код.

- [x] Модель, прошивка, архитектура. Записано в `docs/device-notes.md` (Midi, 0.369.1.50).
- [x] Полный список URL-ключей в `quasar.cfg`. `uniProxyUrl=ws://…` работает без TLS.
      `backendUrl=http://…` ещё не проверен.
- [x] Логирующий адаптер (`quasar-adapter sniff`): станция открывает `/uni.ws` на Mac.
- [x] Протокол uniproxy — AAPI, без zstd.
- [x] Записано 5 голосовых запросов (`captures/traffic/20261028-173139`). SpotterEnd, двойной Ogg BOS, сигнал
      около −45 dBFS.
- [x] Минимальный ответ: AsrResult + AliceResponse + TtsSpeak + `hello.opus`. Станция говорит.
      Окончание речи приходит в `RequestStat.OnSoundPlayerEndTime`.
- [ ] `backendUrl` на адаптер: свой `get_sync_info`, glagol-токены.
- [x] Push `audio_play` с URL: MP3 ✅, бесконечный MP3 ✅, offset ✅, 302 ✅, 308 ❌ (раскрывать в адаптере).
      FLAC ещё не проверен.
- [x] Бумеранг `@@mm_semantic_frame` → TextInput → TTS работает.
- [x] Громкость (`sound_set_level`) ✅, пауза ✅, `player_continue` ❌ (resume через `audio_play` + `offset_ms`).
- [ ] glagol: подключиться к `:1961` с нашим JWT, получить `state`, попробовать `setVolume`/`play`/`stop`.
- [ ] Кнопки и LED: `/proc/bus/input/devices`, `/sys/class/leds`, `led_util`.

Готово, когда: станция работает только через адаптер, произносит фиксированный ответ на любой запрос, известны ответы
на вопросы выше.

## Этап 1. Quasar Adapter MVP

- [x] Каркас пакета, конфиг (TOML + `QUASAR_*` ENV), CLI `serve` / `sniff`. Dockerfile — позже.
- [ ] `backend/`: HTTP-эндпоинты `quasar.yandex.net`, `get_sync_info`, glagol JWT. Перенесено на этап 3:
      для голоса не нужно, станция пока ходит в облако.
- [x] `session.py`: AAPI-сессия, push, голосовые ходы, announce через бумеранг, медиа-команды.
- [x] `audio.py`: инкрементальный Ogg-демуксер, Opus → PCM 16k с усилением, ffmpeg-транскод → Ogg/Opus 48k стримом.
- [x] Детектор речи для непустых partial (обход NO_SPEECH-таймера станции).
- [x] `ha_api.py` + `hub.py`: WS `/ha`, hello/токен, события и команды, zeroconf `_quasar-adapter._tcp`.
- [x] Тесты: `tests/test_flows.py` — фейковая станция ↔ адаптер ↔ фейковый HA (8 сценариев).
- [x] Проверено на реальной Midi с `tools/echo_ha.py`: короткие и длинные (6,8 с) фразы, ответ голосом.

Готово, когда: `pytest` зелёный; симулятор + фейковый HA-клиент проходят полный ход; реальная станция ходит в адаптер.

## Этап 2. Интеграция HA: голос

- [x] Каркас `custom_components/yandex_quasar`, `manifest.json`, config flow (user + zeroconf + reauth),
      `AdapterClient` с автопереподключением. `hacs.json` — позже.
- [x] Устройства и `sensor.*_status` (ENUM online / station_offline / adapter_offline).
- [x] `assist_satellite` + `select` (pipeline, VAD). На реальной станции через тестовый HA: «сколько времени»,
      «какое число» отвечают голосом Piper.
- [x] Окончание речи — `RequestStat` (через `voice.playback_finished`), fallback по длительности в адаптере.
- [x] `assist_satellite.announce` на устройстве: сервис ждёт конца воспроизведения (~5,8 с).
- [x] `media_player` (часть этапа 3) через тестовый HA: play_media, громкость, пауза, resume с позиции,
      состояние из колбэков и `DeviceState`.
- [x] Основной HA (RPi 5, 192.168.0.50): интеграция найдена по zeroconf, пайплайн OpenAI (облачный STT + агент).
- [x] start_conversation и follow-up (`continue_conversation` → `should_listen`): станция слушает без wake word
      (`ActivationType: auto_listening`).
- [x] Таймеры HA: `async_register_timer_handler`, по FINISHED — announce «Таймер закончился».
- [ ] Звук ошибки на станции при ошибке пайплайна (сейчас станция молчит).
- [ ] Music Assistant на основном HA.
- Найдено в HA (не наше): русский ответ `HassStartTimer` падает, если STT пишет число словом («тридцать»);
  OpenAI STT иногда определяет язык как английский, хотя `stt_language: ru-RU`.
- [ ] Тесты интеграции с фейковым адаптером (`pytest-homeassistant-custom-component`).
- Тестовый стек: `ha-dev/docker-compose.yml` (HA 2026.10.4 + Wyoming Whisper/Piper),
  первичная настройка — `ha-dev/setup_ha.py`.

Готово, когда: «Алиса, включи свет на кухне» выполняется через HA Assist и озвучивается; работает
`assist_satellite.announce`; работает мастер Voice Setup.

## Этап 3a. Отвязка от облака Яндекса ✅

- [x] `backend.py`: quasar.yandex.net (register, sync_info, OTA off, glagol token/check/device_list,
      device_state/capability), oauth/passport (x-token отдаёт исходный токен станции), appmetrica/clck/rpc/scbh.
- [x] Исходные данные из облачного конфига и списка устройств станции (`addon_config`), uniProxyUrl и OTA
      переопределяются.
- [x] Станция переключена и перезагружена: стартует без облака, соединений с Яндексом нет.
- [ ] Выяснить разовое соединение к `2a02:6b8::3:207` при загрузке; по желанию — iptables-блок сетей Яндекса.
- [ ] Смена wake word (`account_config.spotter`) через `get_sync_info` + `push_update_config` теперь возможна.

## Этап 3. media_player

- [ ] glagol-клиент в адаптере (state + команды), fallback на `device_state`/колбэки.
- [ ] `media_player`: play/pause/stop/next/prev/seek, громкость, `play_media` с media_source, announce.
- [ ] Music Assistant: плеер виден, играет flow-стрим, корректный `media_content_id`/state.
- [ ] Поведение при голосовом запросе во время музыки (дакинг/пауза, восстановление).

Готово, когда: MA играет плейлист на станции, громкость и пауза работают из HA, во время музыки работают TTS-объявления.

## Этап 4. station-agent: кнопки, LED, мьют (отложено)

Отложено по решению владельца: без софта на станции. Громкость от кнопок уже приходит через glagol (адаптер
— glagol-клиент). Варианты агента (shell на BusyBox или Go) и найденные коды событий — в device-notes.md.


- [ ] Go-агент, сборка под armhf/arm64, init-скрипт, протокол к адаптеру (`/agent` WS).
- [ ] Кнопки → `event`. LED → `light`. Мьют → `switch`.
- [ ] IPC `aliced :9875`: состояние Алисы и TTS_STARTED/COMPLETED (если glagol не даёт нужного).
- [ ] Смена wake word (`account_config.spotter`).

## Этап 5. Доводка

- [x] HA add-on (app) `ha-addon/`: Alpine `base-python:3.14`, ffmpeg, protobuf генерируется при сборке,
      `host_network`. Работает на основном HA (RPi 5), станция переключена на `ws://192.168.0.50:8080/uni.ws`.
- [x] Публикация: GitHub `darkdarin/yandex-quasar` (app-репозиторий + HACS), app ставит адаптер из тега `v<version>`.
- Второй кодек uniproxy (JSON ↔ AAPI), несколько станций одновременно, мультирум (позже).
- HACS-релиз, диагностика, repair issues, переводы.
- Discovery через Supervisor (`discovery: [yandex_quasar]`): токен уходит в интеграцию автоматически.
- Стриминг TTS по `INTENT_PROGRESS.tts_start_streaming` для меньшей задержки.
- Гибридный режим: «не понял HA → проксировать в настоящий uniproxy Яндекса» (по образцу Quasar Bridge). Исследовать
  отдельно, требует настоящей авторизации станции.

## Риски

| Риск | Влияние | Митигация |
|---|---|---|
| Прошивка говорит только AAPI, а JSON-кодек бесполезен | объём работы | protobuf-схемы есть (quasar-protos, `aliceapi.desc` у quasard). Начинаем с того кодека, который видим на этапе 0 |
| Бумеранг не работает, ход по инициативе сервера невозможен | нет announce через Dialog-канал | `audio_play` с URL (без дакинга) или `START_VOCALIZE` через station-agent |
| Плеер станции не тянет бесконечный стрим MA | MA не работает | настройки HTTP Profile в MA, FLAC/MP3, проксирование стрима через адаптер с перекодированием |
| glagol недоступен на модели | хуже state для media_player | `device_state` + SyncPush polling + station-agent |
| Тихий микрофон после VQE | плохой STT | AGC/gain в адаптере, настраиваемый |
| OTA/синхронизация с облаком ломает root | кирпич / потеря доступа | блокировки OTA на двух уровнях, станция не ходит к Яндексу |
| Модель-специфичные различия (LED, кнопки, порты) | этап 4 | capabilities в `StationInfo`, `device-notes.md` по моделям |

## Открытые вопросы к владельцу

1. Какая модель (или модели) станции и версия прошивки?
2. Где будет жить адаптер: HA OS add-on, Docker на том же хосте или отдельная машина?
3. Нужна ли поддержка нескольких станций с первого этапа?
4. Допустимо ли полностью отключить Алису и облако Яндекса (или нужен гибрид)?
