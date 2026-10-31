# Документация ha-yandex-station

Проект делает рутованную Яндекс Станцию полноценным голосовым ассистентом Home Assistant: Assist-пайплайны вместо Алисы,
медиаплеер для Music Assistant, а позже кнопки, подсветка и мьют микрофона.

| Документ | О чём |
|---|---|
| [architecture.md](architecture.md) | **Главный архитектурный план**: компоненты, ключевые решения, потоки данных |
| [station-protocol.md](station-protocol.md) | Протокол станции: HTTP-бэкенд `quasar.yandex.net`, uniproxy (JSON и AAPI), директивы, glagol |
| [adapter-ha-api.md](adapter-ha-api.md) | Протокол между Quasar Adapter и HA-интеграцией |
| [ha-integration.md](ha-integration.md) | Дизайн custom integration: сущности, config flow, жизненный цикл |
| [station-setup.md](station-setup.md) | Подготовка станции: перенаправление на адаптер, защита от OTA, автозапуск |
| [roadmap.md](roadmap.md) | Этапы, критерии готовности, риски, открытые вопросы |
| [references.md](references.md) | Источники и что из них взято |
