from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import re

from dotenv import load_dotenv
import yaml


LATEST_NOTION_VERSION = "2026-03-11"
ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


@dataclass(frozen=True)
class ZoteroConfig:
    data_dir: Path
    sqlite_path: Path
    storage_dir: Path


@dataclass(frozen=True)
class NotionConfig:
    token_env: str
    notion_version: str
    database_id: str
    data_source_id: str
    pdf_property_name: str
    zotero_uri_property_name: str


@dataclass(frozen=True)
class SyncConfig:
    state_db_path: Path
    report_dir: Path
    log_dir: Path
    log_level: str
    dry_run: bool
    continue_on_error: bool
    max_simple_upload_mb: int
    max_supported_mb: int


@dataclass(frozen=True)
class AppConfig:
    zotero: ZoteroConfig
    notion: NotionConfig
    sync: SyncConfig
    notion_token: str


def _require_str(dct: dict[str, Any], key: str) -> str:
    val = dct.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Missing required config value: {key}")
    return val.strip()


def _resolve_path(base_dir: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base_dir / p).resolve()


def _require_positive_int(name: str, value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Config value '{name}' must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"Config value '{name}' must be > 0")
    return parsed


def _require_bool(name: str, value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"Config value '{name}' must be a boolean (true/false), not {type(value).__name__}")


def _validate_notion_id(name: str, value: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
        raise ValueError(f"Config value '{name}' must be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)")


def load_config(config_path: Path, env_path: Path | None = None) -> AppConfig:
    config_path = config_path.expanduser().resolve()
    if env_path is None:
        env_path = config_path.parent / ".env"
    else:
        env_path = env_path.expanduser().resolve()
    if env_path.exists():
        load_dotenv(env_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    base_dir = config_path.parent

    zotero_raw = raw.get("zotero") or {}
    notion_raw = raw.get("notion") or {}
    sync_raw = raw.get("sync") or {}

    zotero = ZoteroConfig(
        data_dir=_resolve_path(base_dir, _require_str(zotero_raw, "data_dir")),
        sqlite_path=_resolve_path(base_dir, _require_str(zotero_raw, "sqlite_path")),
        storage_dir=_resolve_path(base_dir, _require_str(zotero_raw, "storage_dir")),
    )

    notion_version = str(notion_raw.get("notion_version") or LATEST_NOTION_VERSION).strip()
    if notion_version != LATEST_NOTION_VERSION:
        raise ValueError(
            f"Unsupported Notion API version '{notion_version}'. "
            f"Use notion.notion_version: '{LATEST_NOTION_VERSION}'."
        )

    notion = NotionConfig(
        token_env=_require_str(notion_raw, "token_env"),
        notion_version=notion_version,
        database_id=_require_str(notion_raw, "database_id"),
        data_source_id=str(notion_raw.get("data_source_id") or "").strip(),
        pdf_property_name=_require_str(notion_raw, "pdf_property_name"),
        zotero_uri_property_name=_require_str(notion_raw, "zotero_uri_property_name"),
    )
    _validate_notion_id("notion.database_id", notion.database_id)
    if notion.data_source_id:
        _validate_notion_id("notion.data_source_id", notion.data_source_id)

    token = os.getenv(notion.token_env, "").strip()
    if not token:
        raise ValueError(f"Missing Notion token in env var: {notion.token_env}. Add it to .env or shell env.")
    if any(ch.isspace() for ch in token):
        raise ValueError(f"Notion token in {notion.token_env} contains whitespace.")
    if len(token) < 20:
        raise ValueError(
            f"Notion token in {notion.token_env} looks too short. Double-check that you copied the full token."
        )
    if notion.pdf_property_name == notion.zotero_uri_property_name:
        raise ValueError("notion.pdf_property_name and notion.zotero_uri_property_name must be different fields.")

    sync = SyncConfig(
        state_db_path=_resolve_path(base_dir, str(sync_raw.get("state_db_path", ".sync-state.sqlite3"))),
        report_dir=_resolve_path(base_dir, str(sync_raw.get("report_dir", "./reports"))),
        log_dir=_resolve_path(base_dir, str(sync_raw.get("log_dir", "./logs"))),
        log_level=str(sync_raw.get("log_level", "INFO")).strip().upper(),
        dry_run=_require_bool("sync.dry_run", sync_raw.get("dry_run"), False),
        continue_on_error=_require_bool(
            "sync.continue_on_error",
            sync_raw.get("continue_on_error"),
            False,
        ),
        max_simple_upload_mb=_require_positive_int(
            "sync.max_simple_upload_mb", sync_raw.get("max_simple_upload_mb", 20)
        ),
        max_supported_mb=_require_positive_int("sync.max_supported_mb", sync_raw.get("max_supported_mb", 20)),
    )
    if sync.max_simple_upload_mb > sync.max_supported_mb:
        raise ValueError("sync.max_simple_upload_mb cannot be greater than sync.max_supported_mb")
    if sync.log_level not in ALLOWED_LOG_LEVELS:
        allowed = ", ".join(sorted(ALLOWED_LOG_LEVELS))
        raise ValueError(f"sync.log_level must be one of: {allowed}")

    return AppConfig(zotero=zotero, notion=notion, sync=sync, notion_token=token)
