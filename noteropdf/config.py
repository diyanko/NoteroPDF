from __future__ import annotations

import configparser
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

from .util import (normalize_notion_id_input, normalize_notion_target_inputs,
                   unescape_js_string_literal)

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
    return normalize_notion_id_input(value)


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


def default_zotero_profile_root_candidates() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            return [Path(appdata) / "Zotero" / "Zotero"]
        return [home / "AppData" / "Roaming" / "Zotero" / "Zotero"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Zotero"]
    return [home / ".zotero" / "zotero"]


def _profile_paths_from_root(profile_root: Path) -> list[Path]:
    profiles_ini = profile_root / "profiles.ini"
    out: list[Path] = []
    if profiles_ini.exists():
        parser = configparser.RawConfigParser()
        try:
            parser.read(profiles_ini, encoding="utf-8")
        except Exception:
            parser = configparser.RawConfigParser()
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            raw_path = parser.get(section, "Path", fallback="").strip()
            if not raw_path:
                continue
            is_relative = parser.getboolean(section, "IsRelative", fallback=True)
            profile_path = Path(raw_path)
            if is_relative:
                profile_path = profile_root / profile_path
            out.append(profile_path)

    if not out and profile_root.exists():
        for child in profile_root.iterdir():
            if child.is_dir() and (child / "prefs.js").exists():
                out.append(child)
            elif child.is_dir():
                profiles_child = child / "Profiles"
                if profiles_child.exists() and profiles_child.is_dir():
                    out.extend(
                        p for p in profiles_child.iterdir() if p.is_dir() and (p / "prefs.js").exists()
                    )

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in out:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _read_custom_zotero_data_dir(profile_dir: Path) -> Path | None:
    prefs_path = profile_dir / "prefs.js"
    if not prefs_path.exists():
        return None
    try:
        text = prefs_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    use_custom_match = re.search(
        r'user_pref\("extensions\.zotero\.useDataDir",\s*(true|false)\s*\);',
        text,
        flags=re.IGNORECASE,
    )
    if not use_custom_match or use_custom_match.group(1).lower() != "true":
        return None

    data_dir_match = re.search(
        r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\s*\);',
        text,
    )
    if not data_dir_match:
        return None

    raw_value = unescape_js_string_literal(data_dir_match.group(1)).strip()
    if not raw_value:
        return None
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = (profile_dir / path).resolve()
    return path.resolve()


def detect_zotero_data_dir() -> Path | None:
    for profile_root in default_zotero_profile_root_candidates():
        for profile_dir in _profile_paths_from_root(profile_root):
            custom_dir = _read_custom_zotero_data_dir(profile_dir)
            if custom_dir and custom_dir.exists() and custom_dir.is_dir():
                return custom_dir
    for candidate in default_zotero_data_dir_candidates():
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def render_setup_config(
    *,
    zotero_data_dir: Path,
    token_env: str,
    database_id: str,
    data_source_id: str,
    pdf_property_name: str,
    zotero_uri_property_name: str,
    dry_run: bool,
) -> str:
    normalized_database_id, normalized_data_source_id = normalize_notion_target_inputs(
        database_id, data_source_id
    )
    payload = {
        "zotero": {
            "data_dir": str(zotero_data_dir),
        },
        "notion": {
            "token_env": token_env,
            "database_id": normalized_database_id,
            "data_source_id": normalized_data_source_id,
            "pdf_property_name": pdf_property_name,
            "zotero_uri_property_name": zotero_uri_property_name,
        },
        "sync": {
            "dry_run": bool(dry_run),
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


def load_config(
    config_path: Path,
    env_path: Path | None = None,
    *,
    allow_default_config_fallback: bool = False,
) -> AppConfig:
    requested_config_path = config_path.expanduser().resolve()
    if allow_default_config_fallback and not requested_config_path.exists():
        default_config = get_default_config_path()
        config_path = (
            default_config if default_config.exists() else requested_config_path
        )
    else:
        config_path = requested_config_path

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

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

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    base_dir = config_path.parent

    zotero_raw = raw.get("zotero") or {}
    notion_raw = raw.get("notion") or {}
    sync_raw = raw.get("sync") or {}

    zotero_data_dir = _resolve_path(base_dir, _require_str(zotero_raw, "data_dir"))
    zotero = ZoteroConfig(
        data_dir=zotero_data_dir,
        sqlite_path=_resolve_path(
            base_dir, str(zotero_raw.get("sqlite_path") or zotero_data_dir / "zotero.sqlite")
        ),
        storage_dir=_resolve_path(
            base_dir, str(zotero_raw.get("storage_dir") or zotero_data_dir / "storage")
        ),
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
        database_id=_normalize_windows_uuidish(
            str(notion_raw.get("database_id") or "").strip()
        ),
        data_source_id=_normalize_windows_uuidish(
            str(notion_raw.get("data_source_id") or "").strip()
        ),
        pdf_property_name=_require_str(notion_raw, "pdf_property_name"),
        zotero_uri_property_name=_require_str(notion_raw, "zotero_uri_property_name"),
        doi_property_name=str(notion_raw.get("doi_property_name") or "DOI").strip(),
    )
    if not notion.database_id and not notion.data_source_id:
        raise ValueError(
            "Set notion.database_id or notion.data_source_id in config.yaml."
        )
    if notion.database_id:
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
