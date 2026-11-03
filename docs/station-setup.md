# Подготовка станции

Нужны root и SSH/ADB на станции, см. https://hackalice.me/start/. Все шаги ниже меняют системные файлы станции:
делайте их осознанно и сначала снимите бэкап.

## 1. Бэкап

```sh
adb pull /system/vendor/quasar/quasar.cfg
adb pull /system/vendor/quasar/launch.json
adb pull /data/quasar/devices.json   # секрет станции: никому не отправляйте и не публикуйте
```

## 2. Минимальный вариант: голос через Home Assistant

Голосовой канал идёт через адаптер, остальное (конфиг, авторизация, обновления) остаётся в облаке Яндекса.

```sh
mount -o remount,rw /
cp /system/vendor/quasar/quasar.cfg /data/quasar.cfg.orig
# в /system/vendor/quasar/quasar.cfg:
#   "uniProxyUrl" : "ws://<IP Home Assistant>:8080/uni.ws",
sync; mount -o remount,ro /
/system/vendor/quasar/q restart
```

Откат: `cp /data/quasar.cfg.orig /system/vendor/quasar/quasar.cfg` (с тем же `remount rw` → `sync` →
`remount ro` → `q restart`).

## 3. Полная отвязка от облака Яндекса

Адаптер заменяет все облачные сервисы, которые нужны станции: конфиг, авторизацию, проверку обновлений, метрики.
Проверено на Яндекс Станции Миди.

> ⚠️ После отвязки станция пропадёт из приложения «Дом с Алисой», Яндекс Музыка на ней работать не будет.

1. App **Quasar Adapter** установлен и запущен. Скопируйте со станции в `/addon_configs/local_quasar_adapter/`
   на HA (или в папку app, если он установлен из магазина):
   - `sync_info_base.json` — JSON из `/data/quasar/data/user_config.dat`;
   - `devices.json` — копия `/data/quasar/devices.json`.
2. Бэкапы на станции:
   ```sh
   cp /system/vendor/quasar/quasar.cfg /data/quasar.cfg.uniproxy-only
   cd /data/quasar && tar -cf /data/quasar-cloud-backup.tar account_storage.dat devices.json registered initialized data/user_config.dat
   ```
   (в BusyBox нет `tar -z`).
3. В `quasar.cfg` замените адреса облачных сервисов на `http://<IP Home Assistant>:8080/<prefix>`, а
   `uniProxyUrl` — на `ws://<IP Home Assistant>:8080/uni.ws`. Отключите fluent-bit. Удобнее править JSON на
   компьютере и загрузить обратно. Затем `mount -o remount,rw /` → `cp` → `sync` → `mount -o remount,ro /` →
   `/system/vendor/quasar/q restart`.

Откат к режиму «только голос через HA, остальное облако»:
`cp /data/quasar.cfg.uniproxy-only /system/vendor/quasar/quasar.cfg` (с remount и `q restart`).

## 4. Защита от обновлений прошивки

- Адаптер на запросы обновлений всегда отвечает «обновлений нет» и выключает OTA в конфиге станции.
- Дополнительно на устройстве: `yandex-noupdate.sh` из вики hackalice.me (подмена `ota_script.pub`, заглушка
  `device_ota_update.sh`, удаление `--updatesd` из `launch.json`). Заблокируйте `updater.mobile.yandex.net`.

## 5. Подводные камни

- Без `sync` изменения на UBIFS теряются.
- Сброс к заводским настройкам стирает `/data`: маркеры ADB, dropbear и бэкапы.
- Если станция хоть раз синхронизируется с настоящим облаком, Яндекс может выключить ADB
  (`Deactivate adb by quasmodrom config`) и перезаписать unbound.
- После полной отвязки проверку сети (`generate_204`) станции отвечает адаптер. Если он долго недоступен, станция
  решает, что сети нет, и может начать переподключать Wi-Fi.
