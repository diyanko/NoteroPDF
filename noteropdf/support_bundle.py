from __future__ import annotations

import json
import platform
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .__init__ import __version__
from .config import AppConfig


def _sanitize_text(text: str) -> str:
    sanitized = text
    sanitized = re.sub(
        r"(?im)^\s*([A-Z0-9_]*(?:TOKEN|KEY|SECRET)[A-Z0-9_]*)\s*=.*$",
        r"\1=<REDACTED>",
        sanitized,
    )
    sanitized = re.sub(r"secret_[A-Za-z0-9_\-]+", "secret_<REDACTED>", sanitized)

    home = str(Path.home())
    if home and home in sanitized:
        sanitized = sanitized.replace(home, "~")
    return sanitized


def _latest_file(directory: Path, glob_pattern: str) -> Path | None:
    if not directory.exists() or not directory.is_dir():
        return None
    files = [p for p in directory.glob(glob_pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _latest_report_json(directory: Path) -> Path | None:
    if not directory.exists() or not directory.is_dir():
        return None
    files = [
        p
        for p in directory.glob("*.json")
        if p.is_file()
        and not p.name.endswith("-summary.json")
        and not p.name.startswith("support-bundle-")
    ]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _safe_read(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_support_bundle(
    *,
    cfg: AppConfig,
    output_dir: Path,
    config_path: Path | None,
    env_path: Path | None,
    doctor_lines: list[str] | None,
    doctor_error: str | None,
    current_run_log: Path | None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = output_dir / f"support-bundle-{ts}.zip"

    latest_summary = _latest_file(cfg.sync.report_dir, "*-summary.json")
    latest_csv = _latest_file(cfg.sync.report_dir, "*.csv")
    latest_json = _latest_report_json(cfg.sync.report_dir)
    latest_log = (
        current_run_log
        if current_run_log and current_run_log.exists()
        else _latest_file(cfg.sync.log_dir, "*.log")
    )

    state_meta = {
        "state_db_path": str(cfg.sync.state_db_path),
        "exists": cfg.sync.state_db_path.exists(),
        "size_bytes": (
            cfg.sync.state_db_path.stat().st_size
            if cfg.sync.state_db_path.exists()
            else 0
        ),
    }

    manifest = {
        "created_utc": ts,
        "app_version": __version__,
        "python": platform.python_version(),
        "os": platform.platform(),
        "token_source": cfg.notion_token_source,
        "paths": {
            "report_dir": str(cfg.sync.report_dir),
            "log_dir": str(cfg.sync.log_dir),
            "state_db_path": str(cfg.sync.state_db_path),
        },
        "latest_files": {
            "summary": str(latest_summary) if latest_summary else "",
            "csv": str(latest_csv) if latest_csv else "",
            "json": str(latest_json) if latest_json else "",
            "log": str(latest_log) if latest_log else "",
        },
        "state_meta": state_meta,
    }

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _sanitize_text(json.dumps(manifest, indent=2)))

        if doctor_lines is not None:
            zf.writestr("doctor.txt", _sanitize_text("\n".join(doctor_lines) + "\n"))
        if doctor_error:
            zf.writestr("doctor-error.txt", _sanitize_text(doctor_error.strip() + "\n"))

        raw_config = _safe_read(config_path)
        if raw_config:
            zf.writestr("config.redacted.yaml", _sanitize_text(raw_config))

        raw_env = _safe_read(env_path)
        if raw_env:
            zf.writestr("env.redacted", _sanitize_text(raw_env))

        for src, target in (
            (latest_summary, "latest-summary.json"),
            (latest_csv, "latest-report.csv"),
            (latest_json, "latest-report.json"),
            (latest_log, "latest-run.log"),
        ):
            if src is None:
                continue
            content = _safe_read(src)
            if content:
                zf.writestr(target, _sanitize_text(content))

    return out
