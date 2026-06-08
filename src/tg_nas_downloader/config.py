from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_VIDEO_EXTENSIONS = {
    ".3g2",
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
    ".wmv",
}


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    session: Path


@dataclass(frozen=True)
class DownloadConfig:
    destination: Path
    chats: list[str | int]
    tmp_dir: Path = Path("./data/tmp")
    history_limit: int = 200
    scan_on_start: bool = True
    workers: int = 2
    overwrite: bool = False
    dedupe_by_media: bool = True
    min_bytes: int = 0
    max_bytes: int = 0
    layout: str = "{chat_title}/{date:%Y-%m}/{message_id}_{filename}"
    video_extensions: set[str] = field(default_factory=lambda: set(DEFAULT_VIDEO_EXTENSIONS))


@dataclass(frozen=True)
class StateConfig:
    db: Path = Path("./data/state.sqlite3")


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(frozen=True)
class NetworkConfig:
    proxy_url: str | None = None
    proxy_rdns: bool = True


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    download: DownloadConfig
    state: StateConfig = field(default_factory=StateConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    load_env_file(config_path.with_name(".env"))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)

    telegram_raw = raw.get("telegram") or {}
    download_raw = raw.get("download") or {}
    state_raw = raw.get("state") or {}
    logging_raw = raw.get("logging") or {}
    network_raw = raw.get("network") or {}

    api_id = _required_int(telegram_raw, "api_id", "telegram.api_id")
    api_hash = _required_str(telegram_raw, "api_hash", "telegram.api_hash")
    session = _path(telegram_raw.get("session", "./data/telegram.session"))

    destination = _path(_required_str(download_raw, "destination", "download.destination"))
    chats = download_raw.get("chats")
    if not isinstance(chats, list) or not chats:
        raise ConfigError("download.chats must be a non-empty list of @usernames, invite IDs, or numeric chat IDs.")

    extensions = {
        _normalize_extension(value)
        for value in download_raw.get("video_extensions", sorted(DEFAULT_VIDEO_EXTENSIONS))
    }

    return AppConfig(
        telegram=TelegramConfig(api_id=api_id, api_hash=api_hash, session=session),
        download=DownloadConfig(
            destination=destination,
            chats=chats,
            tmp_dir=_path(download_raw.get("tmp_dir", "./data/tmp")),
            history_limit=max(0, int(download_raw.get("history_limit", 200))),
            scan_on_start=bool(download_raw.get("scan_on_start", True)),
            workers=max(1, int(download_raw.get("workers", 2))),
            overwrite=bool(download_raw.get("overwrite", False)),
            dedupe_by_media=bool(download_raw.get("dedupe_by_media", True)),
            min_bytes=max(0, int(download_raw.get("min_bytes", 0))),
            max_bytes=max(0, int(download_raw.get("max_bytes", 0))),
            layout=str(download_raw.get("layout", "{chat_title}/{date:%Y-%m}/{message_id}_{filename}")),
            video_extensions=extensions,
        ),
        state=StateConfig(db=_path(state_raw.get("db", "./data/state.sqlite3"))),
        logging=LoggingConfig(level=str(logging_raw.get("level", "INFO")).upper()),
        network=NetworkConfig(
            proxy_url=_optional_str(
                network_raw.get("proxy_url")
                or network_raw.get("proxy")
                or os.environ.get("TELEGRAM_PROXY_URL")
            ),
            proxy_rdns=bool(network_raw.get("proxy_rdns", True)),
        ),
    )


def _required_str(raw: dict[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if value is None or str(value).strip() == "":
        raise ConfigError(f"{label} is required.")
    return str(value)


def _required_int(raw: dict[str, Any], key: str, label: str) -> int:
    value = raw.get(key)
    if value is None:
        raise ConfigError(f"{label} is required.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be an integer.") from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _path(value: Any) -> Path:
    return Path(os.path.expanduser(str(value))).resolve()


def _normalize_extension(value: Any) -> str:
    extension = str(value).strip().lower()
    if not extension:
        raise ConfigError("download.video_extensions contains an empty value.")
    return extension if extension.startswith(".") else f".{extension}"


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return _expand_required_env(os.path.expandvars(value))
    return value


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_required_env(value: str) -> str:
    missing = [name for name in _ENV_PATTERN.findall(value) if name not in os.environ]
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise ConfigError(f"Missing environment variable(s): {names}")
    return value


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
