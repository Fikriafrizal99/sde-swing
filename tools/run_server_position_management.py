#!/usr/bin/env python3
from __future__ import annotations

"""Non-interactive Linux Active Portfolio workflow.

This is the server equivalent of the existing portfolio maintenance workflows:
1. resolve the last completed IDX session;
2. repair legacy broker provenance metadata;
3. refresh EOD price/technical inputs for OPEN positions;
4. build only missing broker-history tasks for OPEN positions;
5. collect those tasks through the existing read-only Stockbit Playwright lane;
6. import the validated portfolio broker CSV into the existing history database;
7. run the existing Position Management integrity runtime and Telegram report.

The Decision Engine, Broker Fusion, discovery, Final Watchlist, Entry, SL and TP
engines are not invoked here.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_TASKS = ROOT / "data/input/broker/BROKER_PORTFOLIO_BACKFILL_TASKS.csv"
PORTFOLIO_EXPORTS = ROOT / "data/runtime/portfolio_broker_exports"


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=capture,
    )


def _resolve_trade_date() -> str:
    result = _run(
        [sys.executable, "-u", str(ROOT / "tools" / "resolve_last_trading_day.py")],
        capture=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "resolve_last_trading_day failed")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("resolve_last_trading_day returned no date")
    return lines[-1]


def _task_count() -> int | None:
    result = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.stockbit_playwright_collector",
            "task-count",
            "--tasks",
            str(PORTFOLIO_TASKS),
        ],
        capture=True,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _collector_enabled() -> bool:
    result = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.stockbit_playwright_collector",
            "status",
            "--value",
        ],
        capture=True,
    )
    return result.returncode == 0 and result.stdout.strip().upper().splitlines()[-1:] == ["ON"]


def _parse_portfolio_csv(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        if not line.startswith("Portfolio CSV :"):
            continue
        value = line.split(":", 1)[1].strip()
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else ROOT / path
    return None


def _refresh_portfolio_broker_history() -> None:
    """Best-effort broker refresh; Position Management still owns stale-data warnings."""
    PORTFOLIO_EXPORTS.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_TASKS.parent.mkdir(parents=True, exist_ok=True)

    prepare = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.portfolio_broker_daily",
            "--output",
            str(PORTFOLIO_TASKS),
            "daily",
        ]
    )
    if prepare.returncode != 0:
        print(
            "[WARNING] Gagal membangun missing broker tasks portfolio; Position Management lanjut dengan last valid broker history.",
            flush=True,
        )
        return

    count = _task_count()
    if count is None:
        print(
            "[WARNING] Broker task CSV portfolio tidak dapat diverifikasi; broker refresh dilewati.",
            flush=True,
        )
        return
    if count == 0:
        print("[BROKER PORTFOLIO] Tidak ada sesi missing untuk posisi OPEN.", flush=True)
        return
    if not _collector_enabled():
        print(
            f"[WARNING] Ada {count} broker task portfolio tetapi Stockbit Playwright OFF; analisis lanjut dengan histori terakhir.",
            flush=True,
        )
        return

    collect = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.stockbit_playwright_collector",
            "collect",
            "--tasks",
            str(PORTFOLIO_TASKS),
            "--output-dir",
            str(PORTFOLIO_EXPORTS),
        ],
        capture=True,
    )
    if collect.stdout:
        print(collect.stdout, end="" if collect.stdout.endswith("\n") else "\n", flush=True)
    if collect.returncode != 0:
        if collect.stderr:
            print(collect.stderr, file=sys.stderr, end="" if collect.stderr.endswith("\n") else "\n")
        print(
            "[WARNING] Stockbit portfolio broker collection gagal; database tidak diubah dan Position Management lanjut dengan histori terakhir.",
            flush=True,
        )
        return

    portfolio_csv = _parse_portfolio_csv(collect.stdout)
    if portfolio_csv is None or not portfolio_csv.exists():
        print(
            "[WARNING] Collector sukses tetapi Portfolio CSV tidak ditemukan; import broker dilewati.",
            flush=True,
        )
        return

    imported = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.portfolio_broker_daily",
            "--output",
            str(PORTFOLIO_TASKS),
            "import",
            "--file",
            str(portfolio_csv),
        ]
    )
    if imported.returncode != 0:
        print(
            "[WARNING] Import broker portfolio gagal; validator menjaga database agar tidak menerima data invalid.",
            flush=True,
        )
        return
    print(f"[BROKER PORTFOLIO] {count} missing task berhasil dikoleksi dan diimport.", flush=True)


def main() -> int:
    try:
        trade_date = _resolve_trade_date()
    except RuntimeError as exc:
        print(f"[FAILED] Tidak dapat menentukan hari trading IDX terakhir: {exc}", file=sys.stderr)
        return 1

    route = _run(
        [sys.executable, "-u", str(ROOT / "tools" / "check_telegram_report_route.py")]
    )
    telegram_enabled = route.returncode == 0
    if not telegram_enabled:
        print(
            "[WARNING] Portfolio tetap dianalisis, Telegram dilewati karena route Report belum valid.",
            flush=True,
        )

    print("", flush=True)
    print("===============================================================", flush=True)
    print("        SDE SWING - ACTIVE PORTFOLIO MANAGEMENT (SERVER)", flush=True)
    print("===============================================================", flush=True)
    print(f"Trade date : {trade_date}", flush=True)
    print("Scope      : posisi portfolio aktual status OPEN", flush=True)
    print("Engine     : Decision Engine TIDAK dijalankan ulang", flush=True)
    print("Broker     : missing daily refresh -> Current + 3D + 5D + 7D + Since Entry", flush=True)

    repair = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.repair_legacy_broker_backfill_provenance",
            "--db",
            "data/database/sde_swing_history.db",
        ]
    )
    if repair.returncode != 0:
        print(
            "[WARNING] Repair metadata broker gagal; raw broker data tidak diubah dan workflow dilanjutkan.",
            flush=True,
        )

    refresh = _run(
        [
            sys.executable,
            "-u",
            "-m",
            "modules.portfolio.refresh_open_positions",
            "--config",
            "config/pipeline.json",
            "--trade-date",
            trade_date,
        ]
    )
    if refresh.returncode != 0:
        print(
            "[WARNING] Refresh posisi OPEN tidak lengkap; runtime akan memakai last valid local data sesuai contract.",
            flush=True,
        )

    _refresh_portfolio_broker_history()

    runtime_cmd = [
        sys.executable,
        "-u",
        "-m",
        "modules.portfolio.position_management_runtime_integrity",
        "--config",
        "config/pipeline.json",
        "--scheduler-config",
        "config/scheduler.json",
        "--trade-date",
        trade_date,
    ]
    if telegram_enabled:
        runtime_cmd.append("--telegram")
    else:
        runtime_cmd.append("--no-telegram")

    runtime = _run(runtime_cmd)
    rc = int(runtime.returncode)

    if telegram_enabled:
        _run(
            [
                sys.executable,
                "-u",
                "-m",
                "modules.portfolio.portfolio_delivery_status",
            ]
        )

    if rc == 0:
        print("[OK] Position Management selesai.", flush=True)
    elif rc == 50:
        print("[WARNING] Analisis selesai tetapi delivery Telegram gagal.", flush=True)
    else:
        print(f"[FAILED] Position Management gagal. Exit code {rc}.", file=sys.stderr, flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
