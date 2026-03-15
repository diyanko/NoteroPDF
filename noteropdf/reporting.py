from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import csv
from collections import Counter
import json

from .models import SyncRow


def _build_summary(rows: list[SyncRow]) -> dict:
    status_counts = Counter(r.final_status for r in rows)
    action_counts = Counter(r.action_taken for r in rows)

    failure_rows = [r for r in rows if r.final_status not in ("OK", "UNCHANGED")]
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


def write_reports(report_dir: Path, command_name: str, rows: list[SyncRow]) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{command_name}-{ts}"
    json_path = report_dir / f"{base}.json"
    csv_path = report_dir / f"{base}.csv"
    summary_path = report_dir / f"{base}-summary.json"

    payload = [asdict(r) for r in rows]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    fieldnames = [
        "zotero_item_key",
        "title",
        "zotero_uri",
        "notion_page_id",
        "notion_page_url",
        "local_pdf_path",
        "action_taken",
        "final_status",
        "error_message",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in payload:
            writer.writerow(row)

    summary = _build_summary(rows)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return json_path, csv_path, summary_path
