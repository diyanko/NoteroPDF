from __future__ import annotations

import configparser
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path, user_log_path

from .auth import CredentialStore, resolve_access_token
from .settings import LocalSettings, load_settings
from .state_store import StateStore
from .util import unescape_js_string_literal

LATEST_NOTION_VERSION = "2026-03-11"
APP_NAME = "noteropdf"


class SetupRequired(RuntimeError):
    """Raised when the guided connection flow has not completed."""


@dataclass(frozen=True)
class ZoteroConfig:
    data_dir: Path
    storage_dir: Path


@dataclass(frozen=True)
class NotionConfig:
    notion_version: str
    data_source_id: str
    pdf_property_id: str


@dataclass(frozen=True)
class SyncConfig:
    state_db_path: Path
    log_dir: Path


@dataclass(frozen=True)
class AppConfig:
    zotero: ZoteroConfig
    notion: NotionConfig
    sync: SyncConfig
    notion_token: str
    notion_token_source: str


def get_default_state_db_path() -> Path:
    return (
        user_data_path(APP_NAME, appauthor=False, ensure_exists=False)
        / "noteropdf.sqlite3"
    )


def get_default_log_dir() -> Path:
    return user_log_path(APP_NAME, appauthor=False, ensure_exists=False)


def default_zotero_data_dir_candidates() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA", "").strip()
        local = os.getenv("LOCALAPPDATA", "").strip()
        candidates: list[Path] = []
        if appdata:
            candidates.append(Path(appdata) / "Zotero")
        if local:
            candidates.append(Path(local) / "Zotero")
        candidates.append(home / "Zotero")
        return candidates
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
    paths: list[Path] = []
    if profiles_ini.exists():
        parser = configparser.RawConfigParser()
        try:
            parser.read(profiles_ini, encoding="utf-8")
        except (OSError, UnicodeError, configparser.Error):
            parser = configparser.RawConfigParser()
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            raw_path = parser.get(section, "Path", fallback="").strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            if parser.getboolean(section, "IsRelative", fallback=True):
                path = profile_root / path
            paths.append(path)

    if not paths and profile_root.exists():
        for child in profile_root.iterdir():
            if child.is_dir() and (child / "prefs.js").exists():
                paths.append(child)
            elif child.is_dir() and (child / "Profiles").is_dir():
                paths.extend(
                    path
                    for path in (child / "Profiles").iterdir()
                    if path.is_dir() and (path / "prefs.js").exists()
                )

    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _read_custom_zotero_data_dir(profile_dir: Path) -> Path | None:
    prefs_path = profile_dir / "prefs.js"
    if not prefs_path.exists():
        return None
    try:
        text = prefs_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    enabled = re.search(
        r'user_pref\("extensions\.zotero\.useDataDir",\s*(true|false)\s*\);',
        text,
        flags=re.IGNORECASE,
    )
    if not enabled or enabled.group(1).lower() != "true":
        return None
    match = re.search(
        r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\s*\);',
        text,
    )
    if not match:
        return None
    raw_value = unescape_js_string_literal(match.group(1)).strip()
    if not raw_value:
        return None
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = profile_dir / path
    return path.resolve()


def detect_zotero_data_dir() -> Path | None:
    for profile_root in default_zotero_profile_root_candidates():
        for profile_dir in _profile_paths_from_root(profile_root):
            custom = _read_custom_zotero_data_dir(profile_dir)
            if custom and custom.is_dir():
                return custom
    for candidate in default_zotero_data_dir_candidates():
        if candidate.is_dir():
            return candidate.resolve()
    return None


def validate_zotero_data_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not (path / "zotero.sqlite").is_file():
        raise ValueError(f"Zotero database not found in {path}")
    if not (path / "storage").is_dir():
        raise ValueError(f"Zotero storage folder not found in {path}")
    return path


def load_local_settings(state_db_path: Path | None = None) -> LocalSettings:
    db_path = state_db_path or get_default_state_db_path()
    store = StateStore(db_path, acquire_lock=False)
    try:
        return load_settings(store)
    finally:
        store.close()


def build_app_config(
    settings: LocalSettings,
    *,
    token: str,
    token_source: str,
    state_db_path: Path | None = None,
) -> AppConfig:
    if not settings.zotero_data_dir:
        raise SetupRequired("Zotero has not been connected yet.")
    if not settings.notion_data_source_id or not settings.pdf_property_id:
        raise SetupRequired("Notion has not been connected yet.")
    if not token:
        raise SetupRequired("Notion authorization is missing. Run `noteropdf connect`.")

    zotero_dir = validate_zotero_data_dir(Path(settings.zotero_data_dir))
    return AppConfig(
        zotero=ZoteroConfig(
            data_dir=zotero_dir,
            storage_dir=zotero_dir / "storage",
        ),
        notion=NotionConfig(
            notion_version=LATEST_NOTION_VERSION,
            data_source_id=settings.notion_data_source_id,
            pdf_property_id=settings.pdf_property_id,
        ),
        sync=SyncConfig(
            state_db_path=state_db_path or get_default_state_db_path(),
            log_dir=get_default_log_dir(),
        ),
        notion_token=token,
        notion_token_source=token_source,
    )


def load_app_config(
    *,
    state_db_path: Path | None = None,
    credential_store: CredentialStore | None = None,
) -> AppConfig:
    settings = load_local_settings(state_db_path)
    credentials = credential_store or CredentialStore()
    token, source = resolve_access_token(credentials)
    return build_app_config(
        settings,
        token=token,
        token_source=source,
        state_db_path=state_db_path,
    )
