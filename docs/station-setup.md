# Подготовка станции

Предполагается, что root и SSH/ADB уже есть (https://hackalice.me/start/). Перед любыми правками снять бэкап:
`adb pull /system/vendor/quasar/quasar.cfg`, `launch.json`, `/data/quasar/devices.json` (**секрет**, не коммитить).

## 1. Инвентаризация (этап 0)

Результат записать в `docs/device-notes.md`, по разделу на модель.

```sh
cat /system/vendor/quasar/version_full          # прошивка
/system/vendor/quasar/q device-id; /system/vendor/quasar/q full-info
uname -m; file /system/vendor/quasar/maind      # armhf userspace / aarch64 kernel
grep -nE 'Url|url|Host|host' /system/vendor/quasar/quasar.cfg   # все адреса бэкендов
cat /system/vendor/quasar/launch.json
ls /sys/class/leds/ /dev/fb* ; cat /proc/bus/input/devices      # LED и кнопки
netstat -tlnp 2>/dev/null || ss -tlnp                           # 1961 glagol и т. п.
```

## 2. Перенаправление на адаптер

### Вариант A (основной): правка `quasar.cfg`, без TLS

```sh
mount -o remount,rw /
cp /system/vendor/quasar/quasar.cfg /data/quasar.cfg.orig
# backendUrl  -> http://<adapter_ip>:8080
# uniProxyUrl -> ws://<adapter_ip>:8080/uni.ws  (и uniProxyQuicUrl, если есть)
# остальные хосты (oauth/passport/scbh/startup/appmetrica/clck/rpc), если они есть в cfg, -> http://<adapter_ip>:8080/<host>
sync; mount -o remount,ro /
/system/vendor/quasar/q restart
```

- Схема `http://`/`ws://` → прошивка не включает TLS, проверки CA нет.
- Хосты, которых нет в cfg, перекрываем через `/etc/hosts`, а если не выйдет — DNS + вариант B. `DnsFallbackMap`
  (зашитые IP) тоже указывает на Яндекс, его ключи правим там же.
- `get_sync_info` от адаптера повторно задаёт `uniProxyUrl`, так что даже облачный конфиг (если однажды станция
  до облака дойдёт) не уведёт uniproxy обратно, пока отвечаем мы.
- На **Мини 3 Про** корень не сохраняется после перезагрузки. Для неё правки делает init-скрипт из `/data`
  (bind-mount своего `quasar.cfg` поверх штатного) **[проверить]**.

Скрипт `tools/station/redirect.sh {apply|restore|status} <adapter_url>` (будет в репо) делает это идемпотентно и умеет
откатывать.

### Вариант B: DNS + свой CA

Как у quasard: dnsmasq/AdGuard отдаёт IP адаптера для всех хостов из таблицы в
[station-protocol.md](station-protocol.md), DNAT udp/tcp 53 на свой резолвер, свой CA дописан в
`/system/vendor/quasar/certs/ca-certificates.crt`. Адаптер в этом режиме слушает 443 с сертификатом, у которого
SAN = все хосты (`quasar-adapter gen-certs`).

### Полная отвязка (сделано на Midi)

Порядок, который сработал (подробности — [device-notes.md](device-notes.md)):

1. Аддон Quasar Adapter запущен. В `/addon_configs/local_quasar_adapter/` лежат `sync_info_base.json`
   (JSON из `/data/quasar/data/user_config.dat`) и `devices.json` (копия `/data/quasar/devices.json`).
2. Бэкапы на станции: `cp quasar.cfg /data/quasar.cfg.uniproxy-only`, а также
   `tar -cf /data/quasar-cloud-backup.tar account_storage.dat devices.json registered initialized data/user_config.dat`
   (в BusyBox нет `tar -z`).
3. JSON-правка `quasar.cfg` на Mac (сервисы → `http://<HA>:8080/<prefix>`, fluent-bit off), загрузка,
   `remount rw` → `cp` → `sync` → `remount ro` → `q restart`.

Откат к режиму «только голос через HA, остальное облако»:
`cp /data/quasar.cfg.uniproxy-only /system/vendor/quasar/quasar.cfg` (с remount и `q restart`).

## 3. Защита от OTA

- Адаптер отвечает `check_updates/allow_update/tv/api/*/updates` отказом, а `get_sync_info` выключает `ota`.
- Дополнительно на устройстве: `yandex-noupdate.sh` из вики (подмена `ota_script.pub`, заглушка
  `device_ota_update.sh`, удаление `--updatesd` из `launch.json`). Блокировать `updater.mobile.yandex.net`.

## 4. Автозапуск station-agent (этап 4)

```sh
mount -o remount,rw /
cat > /etc/init.d/S99zquasaragent <<'EOF'
#!/bin/sh
case "$1" in
  start) (while true; do /data/opt/bin/quasar-agent --adapter ws://<ip>:8080/agent >>/data/opt/log/agent.log 2>&1; sleep 5; done) & ;;
  stop)  killall quasar-agent ;;
esac
EOF
chmod +x /etc/init.d/S99zquasaragent; sync; mount -o remount,ro /
```

Бинарник — статический `GOARCH=arm64` (или `GOARCH=arm GOARM=7` для armhf-only ядра/Стрит). Скрипт содержит
supervisor-цикл. Откат — удалить скрипт.

## 5. Подводные камни

- Без `sync` изменения на UBIFS теряются.
- Сброс к заводским стирает `/data`: маркеры ADB, dropbear, агента.
- Если станция хоть раз синхронизируется с настоящим облаком, Яндекс может выключить ADB
  (`Deactivate adb by quasmodrom config`) и перезаписать unbound.
- Без ответа `generate_204` станция считает, что сети нет, и может уйти в переподключение Wi-Fi (`wifiReseter`).
- Свой avahi конфликтует с mDNS maind: адаптер на станции не запускаем, он работает в LAN.
