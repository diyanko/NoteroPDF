from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CleanupRow, SyncRow


def _build_summary(rows: list[Any], *, ok_statuses: set[str]) -> dict:
    status_counts = Counter(r.final_status for r in rows)
    action_counts = Counter(r.action_taken for r in rows)

    failure_rows = [r for r in rows if r.final_status not in ok_statuses]
    failure_reason_counts = Counter()
    for r in failure_rows:
        key = f"{r.final_status} | {(r.error_message or '').strip() or 'n/a'}"
        failure_reason_counts[key] += 1

    top_failure_reasons = [
        {"reason": reason, "count": count}
        for reason, count in failure_reason_counts.most_common(20)
    ]

    return {
        "total_items": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "failure_items": len(failure_rows),
        "top_failure_reasons": top_failure_reasons,
    }


def _write_rows(
    report_dir: Path,
    command_name: str,
    rows: list[Any],
    *,
    fieldnames: list[str],
    ok_statuses: set[str],
) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path, csv_path, summary_path = _report_paths(report_dir, command_name)

    payload = [asdict(r) for r in rows]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in payload:
            writer.writerow(row)

    summary = _build_summary(rows, ok_statuses=ok_statuses)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return json_path, csv_path, summary_path


def _report_paths(report_dir: Path, command_name: str) -> tuple[Path, Path, Path]:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{command_name}-{ts}"
    suffix = 1
    while True:
        candidate_base = base if suffix == 1 else f"{base}-{suffix}"
        json_path = report_dir / f"{candidate_base}.json"
        csv_path = report_dir / f"{candidate_base}.csv"
        summary_path = report_dir / f"{candidate_base}-summary.json"
        if not json_path.exists() and not csv_path.exists() and not summary_path.exists():
            return json_path, csv_path, summary_path
        suffix += 1


def write_reports(
    report_dir: Path, command_name: str, rows: list[SyncRow]
) -> tuple[Path, Path, Path]:
    return _write_rows(
        report_dir,
        command_name,
        rows,
        fieldnames=[
            "zotero_item_key",
            "title",
            "zotero_uri",
            "notion_page_id",
            "notion_page_url",
            "local_pdf_path",
            "action_taken",
            "final_status",
            "error_message",
        ],
        ok_statuses={"OK", "UNCHANGED"},
    )


def write_cleanup_reports(
    report_dir: Path, command_name: str, rows: list[CleanupRow]
) -> tuple[Path, Path, Path]:
    return _write_rows(
        report_dir,
        command_name,
        rows,
        fieldnames=[
            "notion_page_id",
            "notion_page_url",
            "title",
            "zotero_uri",
            "action_taken",
            "final_status",
            "error_message",
            "created_time",
            "last_edited_time",
        ],
        ok_statuses={
            "UNCHANGED",
            "STALE_NOTION_ROW",
            "AMBIGUOUS_CLEANUP_MATCH",
            "UNMANAGED_NOTION_ROW",
        },
    )
