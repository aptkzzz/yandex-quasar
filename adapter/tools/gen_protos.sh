#!/bin/sh
# Generate Python protobuf modules from v3rt3p/quasar-protos (Yandex copyright, so not vendored in this repo).
# Usage: tools/gen_protos.sh   (run from adapter/, needs git and the dev dependency grpcio-tools)
set -eu
REPO=https://github.com/v3rt3p/quasar-protos.git
COMMIT=${QUASAR_PROTOS_COMMIT:-1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6}
OUT="$(pwd)/quasar_adapter/_proto"
PY="$(pwd)/.venv/bin/python"
SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
git clone --quiet "$REPO" "$SRC"
git -C "$SRC" checkout --quiet "$COMMIT"
rm -rf "$OUT" && mkdir -p "$OUT"
cd "$SRC"
"$PY" -m grpc_tools.protoc -I. --python_out="$OUT" $(find . -name '*.proto' -not -path './google/*' | sed 's#^\./##') 2>/dev/null
echo "Generated $(find "$OUT" -name '*_pb2.py' | wc -l | tr -d ' ') modules into $OUT"
