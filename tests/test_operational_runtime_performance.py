from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tools import print_job_status


ROOT = Path(__file__).resolve().parents[1]


def _status(root: Path, job: str, run_id: str) -> None:
    destination = root / "data/output/job_status" / f"{job}_latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "trade_date": "2026-08-13",
                "job_mode": "PREVIEW",
                "status": "SUCCESS",
                "current_stage": "COMPLETE",
                "exit_code": 0,
                "details": {
                    "engine_status": "SUCCESS",
                    "report_status": "SUCCESS",
                    "delivery_status": "SKIPPED",
                },
            }
        ),
        encoding="utf-8",
    )


def test_multi_job_status_matches_sequential_single_job_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(print_job_status, "ROOT", tmp_path)
    _status(tmp_path, "market_outlook", "RUN-MARKET")
    _status(tmp_path, "post_market", "RUN-POST")

    assert print_job_status.main(["--job", "market_outlook"]) == 0
    market_output = capsys.readouterr().out
    assert print_job_status.main(["--job", "post_market"]) == 0
    post_output = capsys.readouterr().out

    assert print_job_status.main(
        ["--jobs", "market_outlook", "post_market"]
    ) == 0
    combined_output = capsys.readouterr().out
    assert combined_output == market_output + "\n" + post_output


def test_delivery_status_flag_reads_delivery_channel(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(print_job_status, "ROOT", tmp_path)
    _status(tmp_path, "final_watchlist", "ENGINE-RUN")
    delivery_path = (
        tmp_path
        / "data/output/job_status/final_watchlist_delivery_latest.json"
    )
    delivery_path.write_text(
        json.dumps({
            "run_id": "DELIVERY-RUN",
            "trade_date": "2026-08-13",
            "job_mode": "RESEND",
            "status": "FAILED",
            "current_stage": "FINAL_WATCHLIST_RESEND_EXACT",
            "exit_code": 50,
            "details": {
                "engine_status": "NOT_RUN",
                "report_status": "REUSED_EXACT",
                "delivery_status": "FAILED",
                "replay_mode": "TELEGRAM_COPY_WITH_HASH_LOCKED_ARCHIVE_FALLBACK",
                "telegram_part_count": 0,
            },
        }),
        encoding="utf-8",
    )

    assert print_job_status.main(["--job", "final_watchlist", "--delivery"]) == 0
    output = capsys.readouterr().out

    assert "SDE Delivery Status: final_watchlist" in output
    assert "Run ID       : DELIVERY-RUN" in output
    assert "Delivery     : FAILED" in output
    assert "Exit code    : 50" in output
    assert "Replay       : TELEGRAM_COPY_WITH_HASH_LOCKED_ARCHIVE_FALLBACK" in output
    assert "Telegram msgs: 0" in output
    assert "ENGINE-RUN" not in output


def test_status_batch_files_start_one_python_process() -> None:
    root_status = (ROOT / "CHECK_SDE_STATUS.bat").read_text(encoding="utf-8-sig")
    maintenance_status = (ROOT / "maintenance/CHECK_SDE_STATUS.bat").read_text(
        encoding="utf-8-sig"
    )

    assert root_status.count("%SDE_PYTHON_CMD% tools\\print_job_status.py") == 1
    assert "tools\\print_job_status.py --all" in root_status
    assert "for %%J" not in root_status
    assert maintenance_status.count(
        "%SDE_PYTHON_CMD% tools\\print_job_status.py"
    ) == 1
    assert "--jobs market_outlook post_market final_watchlist" in maintenance_status


def test_python_command_helper_reuses_validated_command_before_discovery() -> None:
    source = (ROOT / "tools/set_python_cmd.bat").read_text(encoding="utf-8-sig")
    lower = source.lower()

    cache_check = lower.index("if defined sde_python_cmd_validated")
    clear_command = lower.index('set "sde_python_cmd="')
    first_discovery = lower.index("where python")
    assert cache_check < clear_command < first_discovery
    assert ":validate_python_cmd" in lower
    assert 'if exist %sde_python_cmd% exit /b 0' in lower
    assert '.venv\\scripts\\python.exe' in lower


def test_for_f_python_captures_wrap_commands_for_quoted_executables() -> None:
    for path in ROOT.rglob("*.bat"):
        source = path.read_text(encoding="utf-8-sig")
        for line in source.splitlines():
            if "for /f" not in line.lower() or "SDE_PYTHON_CMD" not in line:
                continue
            assert "usebackq" in line.lower(), f"missing usebackq: {path}: {line}"
            assert 'in (`"%SDE_PYTHON_CMD%' in line, (
                f"quoted Python command is not protected: {path}: {line}"
            )


def test_for_f_task_count_runs_with_quoted_python_path(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    tasks = tmp_path / "task file with spaces.csv"
    tasks.write_text(
        "Symbol,FROM_DATE,TO_DATE,TASK_KEY,POSITION_ID,SOURCE\n"
        "BBCA,2026-08-13,2026-08-13,BBCA|2026-08-13,POS-1,OPEN_PORTFOLIO\n",
        encoding="utf-8",
    )
    script = tmp_path / "capture task count.bat"
    script.write_text(
        "\n".join(
            (
                "@echo off",
                "setlocal EnableExtensions EnableDelayedExpansion",
                f'cd /d "{ROOT}"',
                "call tools\\set_python_cmd.bat",
                'set "PYTHON_VERSION="',
                'for /f "usebackq delims=" %%V in (`"%SDE_PYTHON_CMD% --version" 2^>^&1`) do set "PYTHON_VERSION=%%V"',
                'if not defined PYTHON_VERSION exit /b 21',
                'set "TRADE_DATE="',
                'for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\\resolve_last_trading_day.py" 2^>nul`) do set "TRADE_DATE=%%D"',
                'if not defined TRADE_DATE exit /b 22',
                'set "PLAYWRIGHT_PY=modules\\portfolio\\stockbit_playwright_collector.py"',
                f'set "TASK_FILE={tasks}"',
                'set "TASK_COUNT="',
                (
                    'for /f "usebackq delims=" %%C in (`"%SDE_PYTHON_CMD% '
                    '-u "%PLAYWRIGHT_PY%" task-count --tasks "%TASK_FILE%"" '
                    '2^>nul`) do set "TASK_COUNT=%%C"'
                ),
                'if not "!TASK_COUNT!"=="1" exit /b 23',
                "echo QUOTED_FOR_F_VERSION=!PYTHON_VERSION!",
                "echo QUOTED_FOR_F_DATE=!TRADE_DATE!",
                "echo QUOTED_FOR_F_COUNT=!TASK_COUNT!",
                "exit /b 0",
            )
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "QUOTED_FOR_F_VERSION=Python " in completed.stdout
    assert "QUOTED_FOR_F_DATE=20" in completed.stdout
    assert "QUOTED_FOR_F_COUNT=1" in completed.stdout


def test_collector_status_and_task_count_do_not_import_heavy_stack(
    tmp_path: Path,
) -> None:
    tasks = tmp_path / "tasks.csv"
    tasks.write_text(
        "Symbol,FROM_DATE,TO_DATE,TASK_KEY,POSITION_ID,SOURCE\n"
        "BBCA,2026-08-13,2026-08-13,BBCA|2026-08-13,POS-1,OPEN_PORTFOLIO\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    code = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from modules.portfolio import stockbit_playwright_collector as collector",
            "blocked = ('pandas', 'playwright', 'modules.portfolio.broker_portfolio_backfill')",
            "assert all(name not in sys.modules for name in blocked)",
            f"assert collector.main(['status', '--state', {str(state)!r}, '--value']) == 0",
            f"assert collector.main(['task-count', '--tasks', {str(tasks)!r}]) == 0",
            "assert all(name not in sys.modules for name in blocked)",
            "print('LIGHTWEIGHT_IMPORTS_OK')",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "LIGHTWEIGHT_IMPORTS_OK" in completed.stdout
