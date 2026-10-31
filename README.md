# Yandex Quasar → Home Assistant

Превращает **рутованную Яндекс Станцию** в голосовой спутник Home Assistant. Станция сохраняет своё железо:
микрофоны с шумоподавлением, слово активации «Алиса»/«Яндекс», динамик. Распознавание речи, понимание команд
и ответы берёт на себя ваш **Assist-пайплайн** в HA, облако Яндекса не используется.

> Неофициальный проект, с Яндексом не связан. Нужен root-доступ к станции, всё делается на свой риск.
> Для получения root см. [hackalice.me](https://hackalice.me/start/).

## Что получается в Home Assistant

| Сущность | Что умеет |
|---|---|
| `assist_satellite` | голосовые команды через любой Assist-пайплайн; продолжение диалога без слова активации; `announce`, `start_conversation`, `ask_question`; таймеры Assist; смена слова активации «Алиса» ↔ «Яндекс» |
| `media_player` | воспроизведение по ссылке (media source, Music Assistant), громкость, пауза и продолжение с позиции, объявления |
| `sensor.*_status` | статус связи: станция / адаптер |
| `select` | выбор пайплайна и чувствительности определения конца речи |

Планы (кнопки, подсветка, мьют микрофона) — в [docs/roadmap.md](docs/roadmap.md).

## Как устроено

```
Яндекс Станция ──ws──> Quasar Adapter (app в HA OS) ──ws──> интеграция yandex_quasar ──> Assist / Music Assistant
     (root)      HTTP   эмулирует облако Яндекса:             assist_satellite, media_player, …
                        uniproxy, quasar backend, oauth
```

- **Quasar Adapter** (`adapter/`, упакован как app в `ha-addon/`) говорит со станцией на её родном протоколе
  (uniproxy, protobuf). Он перекодирует звук Ogg/Opus ⇄ PCM и подменяет облачные сервисы Яндекса:
  конфиг, авторизацию, обновления (всегда «обновлений нет»), метрики.
- **Интеграция** `custom_components/yandex_quasar` подключается к адаптеру по WebSocket и создаёт сущности.

Подробно — [docs/architecture.md](docs/architecture.md).

## Проверено

| Станция | Прошивка | Статус |
|---|---|---|
| Яндекс Станция Миди (`cucumber`) | 0.369.1.50 | ✅ работает |

Другие модели с прошивками на protobuf-протоколе (AAPI), скорее всего, тоже заработают, но не проверялись.
Особенности конкретных устройств собраны в [docs/device-notes.md](docs/device-notes.md).

## Установка

### 1. App (аддон) Quasar Adapter

**Настройки → Приложения → Магазин → ⋮ → Репозитории**, добавьте
`https://github.com/darkdarin/yandex-quasar` и установите **Quasar Adapter**.
При первом запуске адаптер выведет в лог API-токен. Можно задать свой в опции `api_token`.

### 2. Интеграция

HACS → ⋮ → Пользовательские репозитории → `https://github.com/darkdarin/yandex-quasar` (тип *Integration*) →
установить **Yandex Station (Quasar Adapter)** → перезапустить HA. Адаптер найдётся сам через zeroconf,
останется ввести токен.

### 3. Станция (нужен root)

Минимальный вариант — голосовой канал через HA, остальное остаётся в облаке Яндекса. В
`/system/vendor/quasar/quasar.cfg` поменяйте:

```json
"uniProxyUrl" : "ws://<IP Home Assistant>:8080/uni.ws",
```

Полная отвязка от облака (конфиг, авторизация, обновления на адаптере), бэкапы и откат описаны в
[docs/station-setup.md](docs/station-setup.md).

> ⚠️ После отвязки станция пропадёт из приложения «Дом с Алисой», Яндекс Музыка на ней работать не будет.

## Разработка

- План, решения, протоколы: [docs/](docs/README.md), правила для агентов — [AGENTS.md](AGENTS.md).
- Адаптер: `cd adapter && tools/gen_protos.sh && uv run pytest`.
- Локальная сборка app: [ha-addon/README.md](ha-addon/README.md).

Протокол станции изучен по открытым проектам [quasard](https://github.com/amirilivice/quasard),
[quasar-server](https://github.com/v3rt3p/quasar-server), [quasar-protos](https://github.com/v3rt3p/quasar-protos)
и вики [hackalice.me](https://hackalice.me). Их код в репозиторий не копировался. `.proto`-файлы (© Яндекс)
скачиваются и компилируются при сборке.

## Лицензия

[MIT](LICENSE)
