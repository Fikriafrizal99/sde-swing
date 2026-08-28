#!/usr/bin/env bash
set -euo pipefail

START_NOW=false
INSTALL_BROWSER=false

for arg in "$@"; do
  case "$arg" in
    --start) START_NOW=true ;;
    --with-browser) INSTALL_BROWSER=true ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/install_systemd_services.sh [--with-browser] [--start]

  --with-browser  Install Playwright Chromium and Linux browser dependencies.
  --start         Start/restart all timers now. The IDX watcher itself is
                  timer-controlled and only runs Mon-Fri 07:00-18:00 WIB.

Stockbit login is intentionally NOT automated. Run the one-time headed setup
from an interactive Ubuntu desktop session before relying on Final Watchlist.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDE_USER="${SDE_USER:-$(id -un)}"
PYTHON="${SDE_PYTHON:-$ROOT/.venv/bin/python}"
SYSTEMD_DIR="/etc/systemd/system"
TEMPLATE_DIR="$ROOT/deploy/systemd"

if [[ ! -x "$PYTHON" ]]; then
  echo "[ERROR] Python virtualenv not found: $PYTHON" >&2
  echo "Create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  echo "[ERROR] Missing $ROOT/.env" >&2
  echo "Copy the server environment file before enabling automated jobs." >&2
  exit 1
fi

REQUIRED_FILES=(
  "$TEMPLATE_DIR/sde-swing-job@.service.template"
  "$TEMPLATE_DIR/sde-swing-idx-watcher.service.template"
  "$TEMPLATE_DIR/sde-swing-idx-watcher-stop.service"
  "$TEMPLATE_DIR/sde-swing-position-management.service.template"
  "$TEMPLATE_DIR/sde-swing-idx-universe.service.template"
  "$TEMPLATE_DIR/sde-swing-idx-watcher-start.timer"
  "$TEMPLATE_DIR/sde-swing-idx-watcher-stop.timer"
  "$TEMPLATE_DIR/sde-swing-market-outlook.timer"
  "$TEMPLATE_DIR/sde-swing-post-market.timer"
  "$TEMPLATE_DIR/sde-swing-final-watchlist.timer"
  "$TEMPLATE_DIR/sde-swing-position-management.timer"
  "$TEMPLATE_DIR/sde-swing-idx-universe.timer"
  "$ROOT/tools/check_idx_watcher_market_hours.py"
)

for required in "${REQUIRED_FILES[@]}"; do
  if [[ ! -f "$required" ]]; then
    echo "[ERROR] Missing deployment file: $required" >&2
    exit 1
  fi
done

mkdir -p \
  "$ROOT/data/runtime/broker_exports" \
  "$ROOT/data/runtime/portfolio_broker_exports" \
  "$ROOT/data/state/playwright/stockbit" \
  "$ROOT/data/logs/broker_playwright" \
  "$ROOT/logs"

if $INSTALL_BROWSER; then
  echo "[SETUP] Installing Playwright Chromium + Linux dependencies..."
  "$PYTHON" -m playwright install --with-deps chromium
fi

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "[ERROR] xvfb-run is required for the IDX Disclosure headed Chromium service." >&2
  echo "Install it first: sudo apt-get update && sudo apt-get install -y xvfb" >&2
  exit 1
fi

render_template() {
  local source="$1"
  local target="$2"
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__SDE_USER__|$SDE_USER|g" \
    -e "s|__SDE_ROOT__|$ROOT|g" \
    -e "s|__SDE_PYTHON__|$PYTHON|g" \
    "$source" > "$tmp"
  sudo install -m 0644 "$tmp" "$target"
  rm -f "$tmp"
}

render_template \
  "$TEMPLATE_DIR/sde-swing-job@.service.template" \
  "$SYSTEMD_DIR/sde-swing-job@.service"
render_template \
  "$TEMPLATE_DIR/sde-swing-idx-watcher.service.template" \
  "$SYSTEMD_DIR/sde-swing-idx-watcher.service"
render_template \
  "$TEMPLATE_DIR/sde-swing-position-management.service.template" \
  "$SYSTEMD_DIR/sde-swing-position-management.service"
render_template \
  "$TEMPLATE_DIR/sde-swing-idx-universe.service.template" \
  "$SYSTEMD_DIR/sde-swing-idx-universe.service"

sudo install -m 0644 \
  "$TEMPLATE_DIR/sde-swing-idx-watcher-stop.service" \
  "$SYSTEMD_DIR/sde-swing-idx-watcher-stop.service"

TIMERS=(
  sde-swing-idx-watcher-start.timer
  sde-swing-idx-watcher-stop.timer
  sde-swing-market-outlook.timer
  sde-swing-post-market.timer
  sde-swing-final-watchlist.timer
  sde-swing-position-management.timer
  sde-swing-idx-universe.timer
)

for timer in "${TIMERS[@]}"; do
  sudo install -m 0644 "$TEMPLATE_DIR/$timer" "$SYSTEMD_DIR/$timer"
done

# Legacy installs enabled the watcher directly at boot. Remove that old boot
# symlink: the watcher is now owned exclusively by its 07:00/18:00 timers.
sudo rm -f "$SYSTEMD_DIR/multi-user.target.wants/sde-swing-idx-watcher.service"

sudo systemctl daemon-reload
for timer in "${TIMERS[@]}"; do
  sudo systemctl enable "$timer"
done

if $START_NOW; then
  for timer in "${TIMERS[@]}"; do
    sudo systemctl restart "$timer"
  done
fi

echo ""
echo "[OK] SDE Swing systemd units installed."
echo "Project root : $ROOT"
echo "Service user : $SDE_USER"
echo "Python       : $PYTHON"
echo "IDX watcher  : Mon-Fri 07:00-18:00 WIB via Xvfb headed Chromium"
echo ""
echo "Timers:"
systemctl list-timers --all "${TIMERS[@]}" --no-pager || true

echo ""
COLLECTOR_STATE="$($PYTHON -m modules.portfolio.stockbit_playwright_collector status --value 2>/dev/null || echo UNKNOWN)"
echo "Stockbit Playwright collector: $COLLECTOR_STATE"
if [[ "$COLLECTOR_STATE" != "ON" ]]; then
  echo "[ACTION REQUIRED] Before Final Watchlist automation:"
  echo "  $PYTHON -m modules.portfolio.stockbit_playwright_collector setup"
  echo "  $PYTHON -m modules.portfolio.stockbit_playwright_collector enable"
fi

echo ""
if $START_NOW; then
  echo "IDX watcher current state:"
  systemctl --no-pager --full status sde-swing-idx-watcher.service || true
else
  echo "Timers are enabled for future boots."
  echo "Run again with --start to activate/restart all timers now."
fi
