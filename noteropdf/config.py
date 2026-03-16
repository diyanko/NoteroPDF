from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from platformdirs import (user_cache_path, user_config_path, user_data_path,
                          user_log_path)

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
    doi_property_name: str = "DOI"  # Default to "DOI" for backwards compatibility


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
    notion_token_source: str


APP_NAME = "noteropdf"
TOKEN_KEYRING_SERVICE = "noteropdf"


def _normalize_windows_uuidish(value: str) -> str:
    compact = value.replace("-", "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", compact):
        return (
            f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
            f"{compact[16:20]}-{compact[20:32]}"
        )
    return value.strip()


def get_default_config_path() -> Path:
    return (
        user_config_path(APP_NAME, appauthor=False, ensure_exists=True) / "config.yaml"
    )


def get_default_env_path() -> Path:
    return user_config_path(APP_NAME, appauthor=False, ensure_exists=True) / ".env"


def get_default_sync_paths() -> tuple[Path, Path, Path]:
    state_db = (
        user_data_path(APP_NAME, appauthor=False, ensure_exists=True)
        / "sync-state.sqlite3"
    )
    report_dir = (
        user_cache_path(APP_NAME, appauthor=False, ensure_exists=True) / "reports"
    )
    log_dir = user_log_path(APP_NAME, appauthor=False, ensure_exists=True) / "runs"
    return state_db, report_dir, log_dir


def default_zotero_data_dir_candidates() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA", "").strip()
        local = os.getenv("LOCALAPPDATA", "").strip()
        out: list[Path] = []
        if appdata:
            out.append(Path(appdata) / "Zotero")
        if local:
            out.append(Path(local) / "Zotero")
        out.append(home / "Zotero")
        return out
    if sys.platform == "darwin":
        return [home / "Zotero", home / "Library" / "Application Support" / "Zotero"]
    return [home / "Zotero", home / ".zotero" / "zotero"]


def detect_zotero_data_dir() -> Path | None:
    for candidate in default_zotero_data_dir_candidates():
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def render_setup_config(
    *,
    zotero_data_dir: Path,
    sqlite_path: Path,
    storage_dir: Path,
    token_env: str,
    database_id: str,
    data_source_id: str,
    pdf_property_name: str,
    zotero_uri_property_name: str,
    doi_property_name: str = "DOI",
    dry_run: bool,
    state_db_path: Path,
    report_dir: Path,
    log_dir: Path,
) -> str:
    payload = {
        "zotero": {
            "data_dir": str(zotero_data_dir),
            "sqlite_path": str(sqlite_path),
            "storage_dir": str(storage_dir),
        },
        "notion": {
            "token_env": token_env,
            "notion_version": LATEST_NOTION_VERSION,
            "database_id": _normalize_windows_uuidish(database_id),
            "data_source_id": _normalize_windows_uuidish(data_source_id),
            "pdf_property_name": pdf_property_name,
            "zotero_uri_property_name": zotero_uri_property_name,
            "doi_property_name": doi_property_name,
        },
        "sync": {
            "state_db_path": str(state_db_path),
            "report_dir": str(report_dir),
            "log_dir": str(log_dir),
            "log_level": "INFO",
            "dry_run": bool(dry_run),
            "continue_on_error": False,
            "max_simple_upload_mb": 20,
            "max_supported_mb": 20,
        },
    }
    return yaml.safe_dump(payload, sort_keys=False)


def keyring_available() -> bool:
    try:
        import keyring  # type: ignore
    except Exception:
        return False
    try:
        keyring.get_keyring()
    except Exception:
        return False
    return True


def load_token_from_keyring(token_env: str) -> str:
    try:
        import keyring  # type: ignore
    except Exception:
        return ""
    try:
        value = keyring.get_password(TOKEN_KEYRING_SERVICE, token_env)
    except Exception:
        return ""
    return (value or "").strip()


def store_token_in_keyring(token_env: str, token: str) -> bool:
    try:
        import keyring  # type: ignore
    except Exception:
        return False
    try:
        keyring.set_password(TOKEN_KEYRING_SERVICE, token_env, token)
        return True
    except Exception:
        return False


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
    raise ValueError(
        f"Config value '{name}' must be a boolean (true/false), not {type(value).__name__}"
    )


def _validate_notion_id(name: str, value: str) -> None:
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ):
        raise ValueError(
            f"Config value '{name}' must be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
        )


def _resolve_notion_token(token_env: str) -> tuple[str, str]:
    token = os.getenv(token_env, "").strip()
    if token:
        return token, "env"

    token = load_token_from_keyring(token_env)
    if token:
        return token, "keyring"
    return "", "missing"


def load_config(config_path: Path, env_path: Path | None = None) -> AppConfig:
    requested_config_path = config_path.expanduser().resolve()
    # Check if the user provided the default "config.yaml" filename
    if (
        requested_config_path.name == "config.yaml"
        and not requested_config_path.exists()
    ):
        default_config = get_default_config_path()
        config_path = (
            default_config if default_config.exists() else requested_config_path
        )
    else:
        config_path = requested_config_path

    if env_path is None:
        # First check if there's a .env in the config's directory
        env_path = config_path.parent / ".env"
        # If no .env there, try the default env path
        if not env_path.exists():
            default_env = get_default_env_path()
            if default_env.exists():
                env_path = default_env
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

    notion_version = str(
        notion_raw.get("notion_version") or LATEST_NOTION_VERSION
    ).strip()
    if notion_version != LATEST_NOTION_VERSION:
        raise ValueError(
            f"Unsupported Notion API version '{notion_version}'. "
            f"Use notion.notion_version: '{LATEST_NOTION_VERSION}'."
        )

    notion = NotionConfig(
        token_env=_require_str(notion_raw, "token_env"),
        notion_version=notion_version,
        database_id=_normalize_windows_uuidish(_require_str(notion_raw, "database_id")),
        data_source_id=_normalize_windows_uuidish(
            str(notion_raw.get("data_source_id") or "").strip()
        ),
        pdf_property_name=_require_str(notion_raw, "pdf_property_name"),
        zotero_uri_property_name=_require_str(notion_raw, "zotero_uri_property_name"),
        doi_property_name=str(notion_raw.get("doi_property_name") or "DOI").strip(),
    )
    _validate_notion_id("notion.database_id", notion.database_id)
    if notion.data_source_id:
        _validate_notion_id("notion.data_source_id", notion.data_source_id)

    token, token_source = _resolve_notion_token(notion.token_env)
    if not token:
        raise ValueError(
            f"Missing Notion token for key '{notion.token_env}'. "
            "Set it in your environment/.env or run 'noteropdf setup'."
        )
    if any(ch.isspace() for ch in token):
        raise ValueError(f"Notion token in {notion.token_env} contains whitespace.")
    if len(token) < 20:
        raise ValueError(
            f"Notion token in {notion.token_env} looks too short. Double-check that you copied the full token."
        )
    if notion.pdf_property_name == notion.zotero_uri_property_name:
        raise ValueError(
            "notion.pdf_property_name and notion.zotero_uri_property_name must be different fields."
        )

    default_state_db, default_report_dir, default_log_dir = get_default_sync_paths()
    sync = SyncConfig(
        state_db_path=_resolve_path(
            base_dir, str(sync_raw.get("state_db_path", str(default_state_db)))
        ),
        report_dir=_resolve_path(
            base_dir, str(sync_raw.get("report_dir", str(default_report_dir)))
        ),
        log_dir=_resolve_path(
            base_dir, str(sync_raw.get("log_dir", str(default_log_dir)))
        ),
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
        max_supported_mb=_require_positive_int(
            "sync.max_supported_mb", sync_raw.get("max_supported_mb", 20)
        ),
    )
    if sync.max_simple_upload_mb > sync.max_supported_mb:
        raise ValueError(
            "sync.max_simple_upload_mb cannot be greater than sync.max_supported_mb"
        )
    if sync.log_level not in ALLOWED_LOG_LEVELS:
        allowed = ", ".join(sorted(ALLOWED_LOG_LEVELS))
        raise ValueError(f"sync.log_level must be one of: {allowed}")

    return AppConfig(
        zotero=zotero,
        notion=notion,
        sync=sync,
        notion_token=token,
        notion_token_source=token_source,
    )
