# Quasar Adapter

Local replacement of the Yandex cloud voice backend (uniproxy) for rooted Yandex Stations.
The station keeps its wake word, microphones and speaker; recognition, intents and answers come from
Home Assistant Assist through the **Yandex Station (Quasar Adapter)** custom integration.

## Setup

1. Start the app. The first start generates an API token and prints it in the log
   (or set your own in the `api_token` option).
2. Home Assistant discovers the adapter via zeroconf; enter the token in the integration dialog.
3. Point the station to the adapter (root required): in `/system/vendor/quasar/quasar.cfg` set
   `"uniProxyUrl" : "ws://<home-assistant-ip>:<port>/uni.ws"` and restart quasar.
   See `docs/station-setup.md` in the repository.

## Options

- `spotter_tail_ms` — the longest wake-word tail ("-sa" of "Alisa") to cut from the start of the command. The leading
  sound is cut only when a pause follows it, so a command spoken right after the wake word stays intact.
- `speech_threshold_db` — level (after `mic_gain_db`) that counts as speech; also separates the wake-word tail from
  the pause after it.
