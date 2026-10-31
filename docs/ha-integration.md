# HA custom integration `yandex_quasar`

Образцы — `homeassistant/components/wyoming` (сетевой сателлит, клиент к сервису) и `esphome` (push-API, TTS по URL,
continue_conversation). Документация: https://contributors.home-assistant.io/core/entities/assist-satellite/

## 1. Каркас

- `manifest.json`: `"config_flow": true`, `"iot_class": "local_push"`,
  `"dependencies": ["assist_pipeline", "assist_satellite", "conversation", "intent", "media_source", "tts"]`,
  `"zeroconf": ["_quasar-adapter._tcp.local."]`, `"requirements": []`.
- `runtime_data`: `YandexQuasarRuntimeData(client: AdapterClient)`; `type YandexQuasarConfigEntry = ConfigEntry[...]`.
- `AdapterClient` (`client.py`): WS-клиент на `async_get_clientsession(hass)`. Цикл переподключения с backoff, hello,
  разбор кадров, колбэки по станциям: `subscribe_state`, `subscribe_voice`, `subscribe_button`. Запросы отправляются
  через `await client.request(type, station, data)`.
- `async_setup_entry`: первое подключение (`ConfigEntryNotReady` при ошибке), регистрация устройств по `stations`,
  `async_forward_entry_setups`. Станции, появившиеся позже (`station.added`), добавляются через dispatcher-сигнал
  платформам.
- Без `DataUpdateCoordinator`: все сущности `_attr_should_poll = False` и обновляются из колбэков клиента,
  `PARALLEL_UPDATES = 0`.
- Общий базовый класс `YandexQuasarEntity`: `has_entity_name=True`, `unique_id=f"{device_id}-{key}"`,
  `DeviceInfo(identifiers={(DOMAIN, device_id)}, connections={(CONNECTION_NETWORK_MAC, mac)}, manufacturer="Yandex",
  model=platform, sw_version=firmware, via_device=(DOMAIN, adapter_id))`. Если станция или адаптер offline,
  `available=False` у всех сущностей, кроме сенсора статуса. Логирование: один раз при потере и один раз при
  восстановлении связи.

### Config flow

- `zeroconf` → `async_set_unique_id(adapter_id)` → `_abort_if_unique_id_configured(updates={host, port})` → confirm +
  ввод токена.
- `user`: host, port, token, с проверкой подключения (hello).
- `reauth` (неверный токен), `reconfigure` (сменить host/port).

## 2. `assist_satellite`

```python
class YandexQuasarSatellite(YandexQuasarEntity, AssistSatelliteEntity):
    _attr_name = None
    _attr_supported_features = ANNOUNCE | START_CONVERSATION
    _attr_tts_options = {ATTR_PREFERRED_FORMAT: "wav", ATTR_PREFERRED_SAMPLE_RATE: 24000,
                         ATTR_PREFERRED_SAMPLE_CHANNELS: 1, ATTR_PREFERRED_SAMPLE_BYTES: 2}
```

| Колбэк / метод | Реализация |
|---|---|
| `voice.start` | новая `asyncio.Queue`; `config_entry.async_create_background_task(async_accept_pipeline_from_satellite(audio_stream=…, start_stage=STT, end_stage=TTS, wake_word_phrase=wake_word))`. Если `wake_word is None`, это follow-up или start_conversation |
| бинарный PCM | `queue.put_nowait(chunk)` |
| `voice.stop` | `queue.put_nowait(None)`; при `cancel` дополнительно `_cancel_running_pipeline()` |
| `on_pipeline_event(STT_END)` | `queue.put_nowait(None)`; `voice.stt_end {text}` |
| `on_pipeline_event(INTENT_END)` | запомнить `continue_conversation` и `response.speech.plain.speech` |
| `on_pipeline_event(TTS_END)` | `voice.respond {text, tts_url: async_process_play_media_url(url), listen}` |
| `on_pipeline_event(RUN_END)` без TTS | `voice.respond {text, tts_url:null}` (например, распознался только intent без речи) |
| `on_pipeline_event(ERROR)` | `voice.error` |
| `voice.playback_finished` | для announce закрыть future; иначе `tts_response_finished()` |
| `async_announce(a)` | `voice.announce {media_url:a.media_id, preannounce_url, text:a.message, listen:false}` → ждать `playback_finished` (timeout 5 мин) |
| `async_start_conversation(a)` | то же, но `listen=true`. Станция сама откроет микрофон и пришлёт `voice.start` (reason `conversation`) |
| `async_get_configuration` | `available_wake_words` из `StationInfo`, `max_active_wake_words=1` |
| `async_set_configuration` | `config.wake_word` (этап 4; до этого просто хранить) |
| `pipeline_entity_id` / `vad_sensitivity_entity_id` | через entity registry по unique_id `"{device_id}-pipeline"` / `"-vad_sensitivity"` |

Таймер-страховка на `tts_response_finished()`: длительность из ответа адаптера + 2 с.
`intent.async_register_timer_handler` (таймеры Assist) подключить позже: проиграть звук или LED по `FINISHED`.

`select.py`: `AssistPipelineSelect(hass, DOMAIN, device_id)` и `VadSensitivitySelect(hass, device_id)` с нашим
`DeviceInfo`, `entity_category=CONFIG`.

## 3. `media_player`

- `device_class=SPEAKER`. Имя устройства (`_attr_name=None`) уже занято сателлитом, поэтому здесь
  `translation_key="player"` (сущность называется «<Станция> Плеер»).
- Features: `PLAY_MEDIA | PLAY | PAUSE | STOP | NEXT_TRACK | PREVIOUS_TRACK | SEEK | VOLUME_SET | VOLUME_STEP |
  VOLUME_MUTE | MEDIA_ANNOUNCE | BROWSE_MEDIA`. `SEEK`, `NEXT`, `PREV` включать, только если станция их поддерживает
  (glagol или директивы подтверждены).
- `state` из `StationState.player.state`; `media_position`/`media_position_updated_at` меняем только при скачке позиции.
- `media_content_id` = реальный URL (это нужно Music Assistant), `media_title/artist/image_url` из metadata (MA передаёт
  их в `extra["metadata"]`).
- `async_play_media(media_type, media_id, **kw)`: `media_source.async_resolve_media` → `async_process_play_media_url`.
  При `announce=True` вызывается `voice.announce`, иначе `media.play`. `enqueue` игнорируем: флага `MEDIA_ENQUEUE`
  нет.
- `async_browse_media` → `media_source.async_browse_media(..., content_filter=audio/*)`.
- Music Assistant: провайдер «Home Assistant players». Обязателен `PLAY_MEDIA`, MA шлёт `media_stop`, затем
  `play_media(url, enqueue=replace)` с бесконечным flow-стримом (MP3 по умолчанию, FLAC опционально).

## 4. `sensor.<station>_status`

`SensorDeviceClass.ENUM`, `options=["online","connecting","station_offline","adapter_offline"]`,
`entity_category=DIAGNOSTIC`, всегда `available`. Атрибуты: `ip`, `protocol`, `firmware`, `glagol`, `agent`,
`last_seen`.

## 5. Этап 4

- `event.<station>_button_*`: `EventDeviceClass.BUTTON`, `event_types` из `capabilities` агента.
- `light.<station>_led`: `ColorMode.RGB` (или `BRIGHTNESS` для моделей без цвета), `LightEntityFeature.EFFECT`
  с эффектами из `ledpatterns`. В режиме «авто» индикацию состояния Алисы показывает прошивка. Пока свет включён
  из HA, анимации прошивки подавляются, после выключения агент возвращает штатный trigger.
- `switch.<station>_microphone_mute`.
- `select.<station>_wake_word`? Нет: wake word выбирается штатным UI Assist через `async_set_configuration`.

## 6. Качество

- `strings.json` + `translations/{en,ru}.json`: имена сущностей, ошибки config flow, exception translations.
- `diagnostics.py`: снимок `StationInfo`/`StationState` и `diagnostics` адаптера, токены вырезаются через
  `async_redact_data`.
- `quality_scale.yaml` как ориентир: Bronze + Silver обязательно, Gold по мере сил.
- Тесты: `pytest-homeassistant-custom-component`, фейковый адаптер (aiohttp WS-сервер в фикстуре).
  Сценарии: config flow, голосовой ход (STT → respond), announce, play_media, потеря связи.

## 7. Быстрые медиакоманды голосом (рекомендуется)

Встроенные русские фразы HA не покрывают короткие «продолжи», «дальше», «назад», и такие команды уходят в LLM
с многословными ответами. Пользовательские фразы срабатывают раньше LLM (`prefer_local_intents`), молча и
применяются к плеерам в комнате станции, поэтому устройство станции надо привязать к зоне.
`/config/custom_sentences/ru/media_quick.yaml`:

```yaml
language: "ru"
intents:
  HassMediaPause:
    data:
      - sentences: ["пауза", "на паузу", "поставь на паузу", "останови музыку"]
  HassMediaUnpause:
    data:
      - sentences: ["продолжи", "продолжим", "продолжай", "играй дальше", "продолжи музыку", "возобнови", "сними с паузы"]
  HassMediaNext:
    data:
      - sentences: ["дальше", "следующий", "следующий трек", "следующая песня", "переключи трек"]
  HassMediaPrevious:
    data:
      - sentences: ["назад", "предыдущий", "предыдущий трек", "предыдущая песня"]
responses:
  intents:
    HassMediaPause: {default: ""}
    HassMediaUnpause: {default: ""}
    HassMediaNext: {default: ""}
    HassMediaPrevious: {default: ""}
```

У `media_player` интеграции нет «следующий/предыдущий»: при игре из Music Assistant (Sendspin) очередью управляет
плеер MA. Если бы эти команды были и у нашей сущности, интент на всю комнату пропускал бы два трека.

## 8. Задержка голосового ответа (замеры 2026-10-31)

Адаптер передаёт звук в HA потоком (≈50 мс кусками), HA тоже принимает его потоком. Сколько уходит на этапы
пайплайна «OpenAI» на основном HA:

| Этап | Время |
|---|---|
| Ожидание тишины (select «Определение конца речи», default) | ~0,7 с |
| OpenAI STT после конца речи (`audio.transcriptions`, звук отправляется одним файлом) | 0,5–0,8 с |
| Локальный интент (custom sentences) | 0,01–0,3 с |
| LLM-агент OpenAI, музыкальные запросы с инструментами MA | 4–24 с |

Варианты ускорения:
- потоковый STT: HA Cloud, Google Cloud, Azure, Deepgram, Yandex SpeechKit (HACS); у OpenAI в HA потокового режима нет.
  Можно добавить в интеграцию свою STT-сущность на realtime-API OpenAI (`gpt-4o-transcribe` через WebSocket), выигрыш ~0,3–0,6 с;
- «Определение конца речи» → «Быстрое»: −0,4 с на фразу, но длинные паузы посреди фразы обрывают её;
- больше локальных фраз для частых команд; более быстрая модель или короче промпт для LLM.
