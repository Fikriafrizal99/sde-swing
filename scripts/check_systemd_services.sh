#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${SDE_PYTHON:-$ROOT/.venv/bin/python}"

echo "=== SDE Swing systemd status ==="
echo ""

echo "[IDX Disclosure Watcher]"
systemctl --no-pager --full status sde-swing-idx-watcher.service || true

echo ""
echo "[IDX Watcher Hours Guard]"
if [[ -x "$PYTHON" ]]; then
  "$PYTHON" -u "$ROOT/tools/check_idx_watcher_market_hours.py" || true
else
  echo "Python virtualenv         : MISSING ($PYTHON)"
fi

echo ""
echo "[Timers]"
systemctl list-timers --all \
  sde-swing-idx-watcher-start.timer \
  sde-swing-idx-watcher-stop.timer \
  sde-swing-market-outlook.timer \
  sde-swing-post-market.timer \
  sde-swing-final-watchlist.timer \
  sde-swing-position-management.timer \
  sde-swing-performance.timer \
  sde-swing-idx-universe.timer \
  --no-pager || true

echo ""
echo "[Stockbit Playwright]"
if [[ -x "$PYTHON" ]]; then
  "$PYTHON" -m modules.portfolio.stockbit_playwright_collector status || true
  PROFILE="$ROOT/data/state/playwright/stockbit"
  if [[ -d "$PROFILE" ]] && find "$PROFILE" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    echo "Stockbit profile          : PRESENT"
  else
    echo "Stockbit profile          : MISSING/EMPTY"
  fi
else
  echo "Python virtualenv         : MISSING ($PYTHON)"
fi

echo ""
echo "[Latest IDX Watcher logs]"
journalctl -u sde-swing-idx-watcher.service -n 30 --no-pager -l || true

echo ""
echo "[Latest core scheduled-job logs]"
journalctl \
  -u 'sde-swing-job@market_outlook.service' \
  -u 'sde-swing-job@post_market.service' \
  -u 'sde-swing-job@final_watchlist.service' \
  -n 80 --no-pager -l || true

echo ""
echo "[Latest Active Portfolio logs]"
journalctl -u sde-swing-position-management.service -n 50 --no-pager -l || true

echo ""
echo "[Latest Performance logs]"
journalctl -u sde-swing-performance.service -n 50 --no-pager -l || true

echo ""
echo "[Latest IDX Universe logs]"
journalctl -u sde-swing-idx-universe.service -n 30 --no-pager -l || true
