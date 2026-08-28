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
  --start         Start/restart the IDX watcher and timers immediately.
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

for required in \
  "$TEMPLATE_DIR/sde-swing-job@.service.template" \
  "$TEMPLATE_DIR/sde-swing-idx-watcher.service.template" \
  "$TEMPLATE_DIR/sde-swing-market-outlook.timer" \
  "$TEMPLATE_DIR/sde-swing-post-market.timer" \
  "$TEMPLATE_DIR/sde-swing-final-watchlist.timer"; do
  if [[ ! -f "$required" ]]; then
    echo "[ERROR] Missing deployment file: $required" >&2
    exit 1
  fi
done

if $INSTALL_BROWSER; then
  echo "[SETUP] Installing Playwright Chromium + Linux dependencies..."
  "$PYTHON" -m playwright install --with-deps chromium
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

sudo install -m 0644 \
  "$TEMPLATE_DIR/sde-swing-market-outlook.timer" \
  "$SYSTEMD_DIR/sde-swing-market-outlook.timer"
sudo install -m 0644 \
  "$TEMPLATE_DIR/sde-swing-post-market.timer" \
  "$SYSTEMD_DIR/sde-swing-post-market.timer"
sudo install -m 0644 \
  "$TEMPLATE_DIR/sde-swing-final-watchlist.timer" \
  "$SYSTEMD_DIR/sde-swing-final-watchlist.timer"

sudo systemctl daemon-reload
sudo systemctl enable sde-swing-idx-watcher.service
sudo systemctl enable sde-swing-market-outlook.timer
sudo systemctl enable sde-swing-post-market.timer
sudo systemctl enable sde-swing-final-watchlist.timer

if $START_NOW; then
  sudo systemctl restart sde-swing-idx-watcher.service
  sudo systemctl restart sde-swing-market-outlook.timer
  sudo systemctl restart sde-swing-post-market.timer
  sudo systemctl restart sde-swing-final-watchlist.timer
fi

echo ""
echo "[OK] SDE Swing systemd units installed."
echo "Project root : $ROOT"
echo "Service user : $SDE_USER"
echo "Python       : $PYTHON"
echo ""
echo "Timers:"
systemctl list-timers --all \
  sde-swing-market-outlook.timer \
  sde-swing-post-market.timer \
  sde-swing-final-watchlist.timer \
  --no-pager || true

echo ""
if $START_NOW; then
  echo "IDX watcher:"
  systemctl --no-pager --full status sde-swing-idx-watcher.service || true
else
  echo "Units are enabled and will start on the next boot."
  echo "Run again with --start to activate them now."
fi
