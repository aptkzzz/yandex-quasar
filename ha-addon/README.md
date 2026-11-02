# HA app (add-on): Quasar Adapter

This directory is the app itself (`config.yaml`, `Dockerfile`, `run.sh`); `repository.yaml` in the repo root makes
the repository installable from the HA app store.

- **Published install** (from the app store): the Dockerfile installs the adapter from the git tag
  `v<version>` — tag the repo with the version from `config.yaml` before publishing.
- **Local dev build**: `build.sh` stages this directory plus the adapter source into `.build/quasar_adapter`;
  the Dockerfile then installs the local source instead of the tag.

## Local install on HA OS (dev flow)

```sh
STAGE=$(ha-addon/build.sh)
COPYFILE_DISABLE=1 tar -C "$(dirname "$STAGE")" -cf - quasar_adapter | ssh ha 'rm -rf /local_apps/quasar_adapter && tar -C /local_apps -xf -'
ssh ha 'ha store reload && ha apps install local_quasar_adapter'   # first time
ssh ha 'ha store reload && ha apps update local_quasar_adapter'    # after changes (bump version in config.yaml)
ssh ha 'ha apps start local_quasar_adapter && ha apps logs local_quasar_adapter'
```

`COPYFILE_DISABLE=1` keeps macOS `._*` files out of the archive. Each update needs a new version in `config.yaml`;
if `ha store reload` times out, retry it — the Supervisor may still be busy with a previous reload.

On the first start the adapter prints a generated API token in the log (or set the `api_token` option).
Home Assistant discovers the adapter via zeroconf; confirm it with the token.

Seed files for the Yandex backend emulation go to `/addon_configs/local_quasar_adapter/` on the host
(see `docs/station-setup.md`).

## Local build check (arm64 like RPi 5)

```sh
cd "$(ha-addon/build.sh)" && docker build --platform linux/arm64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.14-alpine3.22 -t local/quasar_adapter:test .
```

## Release checklist

1. Bump the version in `ha-addon/config.yaml`, `adapter/pyproject.toml`, `adapter/quasar_adapter/__init__.py`,
   `custom_components/yandex_quasar/manifest.json`.
2. Commit, tag `v<version>`, push the tag (the app build installs the adapter from it), create a GitHub release.
