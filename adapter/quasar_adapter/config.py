"""Adapter configuration (TOML file + QUASAR_* environment overrides)."""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class StationOverride:
    name: str | None = None


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8080
    # Address announced via zeroconf and used to build URLs for the station; autodetected when empty.
    advertise_host: str = ""
    data_dir: Path = Path("data")
    api_token: str = ""
    adapter_name: str = "Quasar Adapter"
    mic_gain_db: float = 30.0
    # Level (after gain) above which the mic chunk counts as speech; keeps the station from closing with NO_SPEECH.
    speech_threshold_db: float = -40.0
    # Station gives up with NO_SPEECH ~5 s after SpotterEnd unless it sees ASR progress.
    partial_interval: float = 0.5
    max_utterance: float = 15.0
    preroll_timeout: float = 0.5
    # Audio right after SpotterEnd still contains the end of the wake word ("-са"); STT turns it into "Так,"/"Да,".
    spotter_tail_ms: int = 300
    playback_grace: float = 2.0
    # Station sound (EStoredSound name) played when the HA pipeline fails; empty disables it.
    error_sound: str = "SpotterCancel"
    # Our cut-off for an HA answer after recognition; keep below vins_request_timeout_ms.
    ha_response_timeout: float = 55.0
    # Station's own wait for an answer (default 15 s in firmware), raised via get_sync_info for slow LLM agents.
    vins_request_timeout_ms: int = 60000
    ffmpeg: str = "ffmpeg"
    log_traffic: bool = False
    # Backend emulation: directory with seed files captured from the station (sync_info_base.json, devices.json).
    seed_dir: str = ""
    # Accept glagol tokens we did not issue (other LAN clients such as the yandex_station integration).
    glagol_accept_any: bool = True
    # Connect to the station's local glagol socket for live state (volume changed by buttons).
    glagol_client: bool = True
    # Music Assistant player over Sendspin: one listener per station starting at this port (mDNS _sendspin._tcp).
    sendspin_enabled: bool = True
    sendspin_port: int = 8928
    # Yandex account uid reported in OAuth responses (X-UPRX-UID of the station); optional.
    account_uid: str = ""
    # Debug: save raw bodies of set_device_capability and rpc gproxy reports here.
    backend_capture_dir: str = ""
    # When set, PCM sent to HA for every voice turn is also saved as <dir>/<turn_id>.wav (debugging STT quality).
    debug_audio_dir: str = ""
    stations: dict[str, StationOverride] = field(default_factory=dict)
    # Runtime flag, not configurable: the API token was created on this start.
    token_generated: bool = field(default=False, init=False)

    @classmethod
    def load(cls, path: Path | None) -> Config:
        raw: dict = {}
        if path and path.exists():
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg = cls()
        for f in fields(cls):
            if f.name in ("stations", "token_generated"):
                continue
            value = raw.get(f.name, getattr(cfg, f.name))
            env = os.environ.get(f"QUASAR_{f.name.upper()}")
            if env is not None:
                value = env
            setattr(cfg, f.name, _coerce(f.type, value))
        cfg.stations = {
            device_id: StationOverride(**(override or {}))
            for device_id, override in raw.get("stations", {}).items()
        }
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        if not cfg.api_token:
            token_path = cfg.data_dir / "api_token"
            cfg.token_generated = not token_path.exists()
            cfg.api_token = _persistent_token(token_path)
        return cfg


def _coerce(type_name: str, value):
    if type_name in ("int", int):
        return int(value)
    if type_name in ("float", float):
        return float(value)
    if type_name in ("bool", bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if type_name in ("Path", Path):
        return Path(value)
    return value


def _persistent_token(path: Path) -> str:
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(24)
    path.write_text(token)
    path.chmod(0o600)
    return token
