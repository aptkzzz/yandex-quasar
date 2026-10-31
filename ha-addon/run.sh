#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
export QUASAR_DATA_DIR=/data
export QUASAR_PORT="$(bashio::config 'port')"
export QUASAR_MIC_GAIN_DB="$(bashio::config 'mic_gain_db')"
export QUASAR_SPEECH_THRESHOLD_DB="$(bashio::config 'speech_threshold_db')"
export QUASAR_SPOTTER_TAIL_MS="$(bashio::config 'spotter_tail_ms')"
export QUASAR_LOG_LEVEL="$(bashio::config 'log_level')"
export QUASAR_SEED_DIR=/config
export QUASAR_GLAGOL_ACCEPT_ANY="$(bashio::config 'glagol_accept_any')"
if bashio::config.has_value 'account_uid'; then
    export QUASAR_ACCOUNT_UID="$(bashio::config 'account_uid')"
fi
if bashio::config.has_value 'api_token'; then
    export QUASAR_API_TOKEN="$(bashio::config 'api_token')"
fi
if [ ! -s /data/api_token ] && [ -z "${QUASAR_API_TOKEN:-}" ]; then
    bashio::log.info "A new API token will be generated in /data/api_token"
fi
cd /data || exit 1
exec python3 -m quasar_adapter serve -c /data/config.toml
