#!/bin/sh
# Assemble a local build context (app files + adapter source) into ha-addon/.build/quasar_adapter.
# With the adapter source present the Dockerfile installs it instead of the published git tag.
# Usage: ha-addon/build.sh            -> prints the staging dir
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAGE="$ROOT/ha-addon/.build/quasar_adapter"
rm -rf "$STAGE" && mkdir -p "$STAGE/adapter"
for f in config.yaml build.yaml Dockerfile run.sh DOCS.md translations; do
    cp -R "$ROOT/ha-addon/$f" "$STAGE/"
done
cp "$ROOT/adapter/pyproject.toml" "$STAGE/adapter/"
tar -C "$ROOT/adapter" --exclude __pycache__ --exclude _proto -cf - quasar_adapter | tar -C "$STAGE/adapter" -xf -
echo "$STAGE"
