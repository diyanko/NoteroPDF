import zipfile
from pathlib import Path

from noteropdf.support_bundle import build_support_bundle


class _Cfg:
    class _Sync:
        def __init__(self, base: Path):
            self.report_dir = base / "reports"
            self.log_dir = base / "logs"
            self.state_db_path = base / "state.sqlite3"

    def __init__(self, base: Path):
        self.sync = self._Sync(base)
        self.notion_token_source = "env"


def test_support_bundle_redacts_token_and_secret(tmp_path: Path):
    cfg = _Cfg(tmp_path)
    cfg.sync.report_dir.mkdir(parents=True)
    cfg.sync.log_dir.mkdir(parents=True)

    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    log_path = cfg.sync.log_dir / "run.log"
    report_json_path = cfg.sync.report_dir / "sync.json"
    summary_path = cfg.sync.report_dir / "sync-summary.json"

    config_path.write_text("notion:\n  token_env: NOTION_TOKEN\n", encoding="utf-8")
    env_path.write_text(
        "NOTION_TOKEN=secret_abc123456\nNOTION_API_KEY=abc123\n", encoding="utf-8"
    )
    log_path.write_text("token secret_abc123456\n", encoding="utf-8")
    report_json_path.write_text('{"report": "FULL"}\n', encoding="utf-8")
    summary_path.write_text('{"status": "OK"}\n', encoding="utf-8")

    bundle = build_support_bundle(
        cfg=cfg,  # type: ignore[arg-type]
        output_dir=cfg.sync.report_dir,
        config_path=config_path,
        env_path=env_path,
        doctor_lines=["doctor ok"],
        doctor_error=None,
        current_run_log=log_path,
    )

    assert bundle.exists()

    with zipfile.ZipFile(bundle, "r") as zf:
        env_redacted = zf.read("env.redacted").decode("utf-8")
        run_log = zf.read("latest-run.log").decode("utf-8")
        latest_report_json = zf.read("latest-report.json").decode("utf-8")
        manifest = zf.read("manifest.json").decode("utf-8")

    assert "NOTION_TOKEN=<REDACTED>" in env_redacted
    assert "NOTION_API_KEY=<REDACTED>" in env_redacted
    assert "secret_abc123456" not in env_redacted
    assert "abc123" not in env_redacted
    assert "secret_abc123456" not in run_log
    assert '{"report": "FULL"}' in latest_report_json
    assert '{"status": "OK"}' not in latest_report_json
    assert "token_source" in manifest
