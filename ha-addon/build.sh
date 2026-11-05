#!/bin/sh
# Assemble the app's build context (app files + adapter source) into ha-addon/.build/quasar_adapter.
# CI builds the published image from it; copied to /local_apps on HA it is a local app that the Supervisor
# builds itself (the staged config.yaml has no "image:", so nothing is pulled from ghcr).
# Usage: ha-addon/build.sh            -> prints the staging dir
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAGE="$ROOT/ha-addon/.build/quasar_adapter"
rm -rf "$STAGE" && mkdir -p "$STAGE/adapter"
for f in config.yaml build.yaml Dockerfile run.sh DOCS.md README.md CHANGELOG.md translations icon.png logo.png; do
    cp -R "$ROOT/ha-addon/$f" "$STAGE/"
done
grep -v '^image:' "$ROOT/ha-addon/config.yaml" > "$STAGE/config.yaml"
cp "$ROOT/adapter/pyproject.toml" "$ROOT/adapter/uv.lock" "$STAGE/adapter/"
tar -C "$ROOT/adapter" --exclude __pycache__ --exclude _proto -cf - quasar_adapter | tar -C "$STAGE/adapter" -xf -
echo "$STAGE"
