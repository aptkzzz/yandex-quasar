ARG BUILD_FROM
FROM ${BUILD_FROM}

# Supervisor passes the app version; the published app installs the adapter from the matching git tag.
ARG BUILD_VERSION
ARG ADAPTER_REPO=https://github.com/darkdarin/yandex-quasar.git
# quasar-protos are Yandex-copyrighted, so they are fetched and compiled at build time instead of being vendored.
ARG QUASAR_PROTOS_COMMIT=1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6

RUN apk add --no-cache ffmpeg openssl \
    && apk add --no-cache --virtual .build-deps git protobuf-dev

# Local builds (ha-addon/build.sh) put the adapter source next to this file; the wildcard keeps COPY valid without it.
COPY run.sh adapter* /tmp/src/
RUN if [ -f /tmp/src/pyproject.toml ]; then \
        pip install --no-cache-dir /tmp/src; \
    else \
        pip install --no-cache-dir "quasar-adapter @ git+${ADAPTER_REPO}@v${BUILD_VERSION}#subdirectory=adapter"; \
    fi \
    && git clone --quiet https://github.com/v3rt3p/quasar-protos.git /tmp/protos \
    && git -C /tmp/protos checkout --quiet ${QUASAR_PROTOS_COMMIT} \
    && OUT=$(python3 -c "import quasar_adapter, pathlib; print(pathlib.Path(quasar_adapter.__file__).parent / '_proto')") \
    && mkdir -p "$OUT" \
    && cd /tmp/protos \
    && protoc -I. --python_out="$OUT" $(find . -name '*.proto' -not -path './google/*' | sed 's#^\./##') 2>/dev/null \
    && python3 -c "from quasar_adapter import aapi; print('protos ok')" \
    && rm -rf /tmp/protos /tmp/src \
    && apk del .build-deps

COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
