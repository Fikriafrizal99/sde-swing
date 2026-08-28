#!/usr/bin/env python3
from __future__ import annotations

"""Non-interactive Linux port of maintenance/RUN_POSITION_MANAGEMENT.bat.

The workflow intentionally does not run the Decision Engine. It refreshes only
OPEN portfolio positions, synchronizes broker context through the existing
integrity runtime, and sends the existing Position Management Telegram report
when the report route is valid.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    print("Broker     : Current + 3D + 5D + 7D + Since Entry", flush=True)

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
