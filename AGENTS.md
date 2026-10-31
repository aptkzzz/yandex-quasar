# AGENTS.md

Интеграция рутованной Яндекс Станции с Home Assistant: станция работает как Assist-сателлит и медиаплеер,
облако Яндекса заменено нашим сервером.

## Компоненты

- `adapter/` — **Quasar Adapter** (Python, asyncio/aiohttp). Эмулирует `quasar.yandex.net` и uniproxy для станции,
  отдаёт HA свой WebSocket API.
- `custom_components/yandex_quasar/` — **HA custom integration** (`assist_satellite`, `media_player`, `sensor`,
  `select`; позже `event`, `light`, `switch`).
- `station-agent/` — опциональный агент на станции (Go): кнопки, LED, мьют.
- `ha-addon/` — упаковка адаптера как HA OS add-on (app), установка: [ha-addon/README.md](ha-addon/README.md).
- `ha-dev/` — тестовый HA в Docker (HA + Wyoming Whisper/Piper) для разработки интеграции.

## Документация (читать перед работой)

- [docs/README.md](docs/README.md) — оглавление
- [docs/architecture.md](docs/architecture.md) — архитектурный план и ключевые решения
- [docs/station-protocol.md](docs/station-protocol.md) — протокол станции (HTTP-бэкенд, uniproxy JSON/AAPI, директивы, glagol)
- [docs/adapter-ha-api.md](docs/adapter-ha-api.md) — протокол Adapter ↔ HA
- [docs/ha-integration.md](docs/ha-integration.md) — дизайн HA-интеграции
- [docs/station-setup.md](docs/station-setup.md) — подготовка и перенаправление станции
- [docs/roadmap.md](docs/roadmap.md) — этапы, риски, открытые вопросы
- [docs/references.md](docs/references.md) — источники

## Репозиторий и релизы

- GitHub: `git@github.com:darkdarin/yandex-quasar.git` (push по SSH-ключу этого Mac, аккаунт `darkdarin`;
  `gh` на машине авторизован под другим, рабочим аккаунтом — для настроек репозитория не годится).
- Один репозиторий — два канала: HACS (`custom_components/`, `hacs.json`) и магазин HA apps (`repository.yaml` +
  `ha-addon/`). Опубликованный app собирает адаптер из тега `v<version>`.
- Релиз: поднять версию в 4 местах (см. [ha-addon/README.md](ha-addon/README.md#release-checklist)), коммит,
  тег `v<version>`, push тега. CI: `.github/workflows/adapter.yml` (pytest), `validate.yml` (hassfest, HACS, app lint).

## Рабочее окружение

Разработка ведётся на **живом HA** владельца, тестовый `ha-dev/` выключен.

| Что | Где / как |
|---|---|
| Станция (Midi) | `ssh alice` (192.168.1.25:2222, root). `uniProxyUrl` → `ws://192.168.0.50:8080/uni.ws`, бэкап — `/data/quasar.cfg.orig` |
| Home Assistant OS (RPi 5) | `ssh ha` (192.168.0.50, Terminal & SSH add-on). API-токен — `main-ha.env` в корне (gitignored, в чат не выводить) |
| Адаптер | add-on `local_quasar_adapter` на HA; обновление — [ha-addon/README.md](ha-addon/README.md) |
| Интеграция | `tar -C custom_components -cf - yandex_quasar \| ssh ha 'tar -C /config/custom_components -xf -'`, затем `ssh ha 'ha core check && ha core restart'` |
| Отладка Assist | WS `assist_pipeline/pipeline_debug/list|get` на основном HA; пайплайн «OpenAI» (облачный STT + агент) |
| Тесты адаптера | `cd adapter && uv run pytest` |

Ограничения: HA на Raspberry Pi 5, поэтому тяжёлые локальные модели (Whisper turbo и т. п.) не предлагать.
Перезапуск HA и правки на станции касаются живого дома: делать осознанно и сообщать.

## Правила

- Документация на русском, идентификаторы, код и комментарии в коде — на английском.
- Меняешь протокол (станция или Adapter ↔ HA) — обнови соответствующий документ в том же изменении.
- Находки с реального устройства записывай в `docs/device-notes.md` (модель, прошивка, что подтверждено).
  Пометки **[проверить]** в доках снимай, только когда факт подтверждён.
- Не коммить секреты станции: `devices.json`, glagol-сертификаты, токены, записи трафика с ними.
- Интеграция не тянет внешних зависимостей (`requirements: []`). Общие схемы протокола копируются из адаптера.
- Код из quasard и quasar-server не копируем без проверки лицензии: протоколы описываем, код пишем свой.
