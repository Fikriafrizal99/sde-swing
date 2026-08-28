#!/usr/bin/env bash
set -u

echo "=== SDE Swing systemd status ==="
echo ""

echo "[IDX Watcher]"
systemctl --no-pager --full status sde-swing-idx-watcher.service || true

echo ""
echo "[Timers]"
systemctl list-timers --all \
  sde-swing-market-outlook.timer \
  sde-swing-post-market.timer \
  sde-swing-final-watchlist.timer \
  --no-pager || true

echo ""
echo "[Latest IDX logs]"
journalctl -u sde-swing-idx-watcher.service -n 30 --no-pager -l || true

echo ""
echo "[Latest scheduled job logs]"
journalctl \
  -u 'sde-swing-job@market_outlook.service' \
  -u 'sde-swing-job@post_market.service' \
  -u 'sde-swing-job@final_watchlist.service' \
  -n 50 --no-pager -l || true
