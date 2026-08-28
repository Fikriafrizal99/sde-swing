# SDE Swing — Ubuntu Server Runtime

Branch target: `server-integration` (forked from `testing`).

This deployment layer changes operating-system scheduling only. It does not
change Technical, Broker Fusion, Decision Engine, Entry, SL/TP, lifecycle, or
portfolio-management formulas.

## Runtime layout

| Component | Schedule / mode | Runtime |
| --- | --- | --- |
| IDX Disclosure Watcher | persistent | `sde-swing-idx-watcher.service` |
| Market Outlook + Morning News | Mon–Fri 08:45 WIB | `sde-swing-market-outlook.timer` |
| Post Market + Post Market News | Mon–Fri 16:30 WIB | `sde-swing-post-market.timer` |
| Final Watchlist | Mon–Fri 18:00 WIB | `sde-swing-final-watchlist.timer` |
| Active Portfolio Management | Mon–Fri 18:45 WIB | `sde-swing-position-management.timer` |
| IDX Universe refresh | Fri 20:00 WIB | `sde-swing-idx-universe.timer` |

Final Watchlist keeps its existing downstream Active Recommendations and
Watchlist AI presentation flow. Active Portfolio is separate and analyzes only
actual `OPEN` portfolio positions; it never reruns the Decision Engine.

Market-sensitive timers use `Persistent=false` intentionally. If the server was
off at 08:45, for example, Market Outlook will not be replayed late in the
afternoon merely because the machine booted. Existing explicit recovery tools
remain the correct path for a missed session. The weekly IDX Universe maintenance
timer is persistent because a delayed refresh is safe.

## First server setup

From the repository root:

```bash
# Use the isolated server branch
git switch server-integration
git pull --ff-only origin server-integration

# Create the environment if it does not already exist
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Put the real server .env in the repo root before enabling services.
# Never commit .env.

# Install Linux units and bundled Playwright Chromium
bash scripts/install_systemd_services.sh --with-browser
```

The installer creates the repo-local broker staging directory at
`data/runtime/broker_exports`, renders systemd templates with the current user,
repo root and `.venv` Python, then enables the service/timers.

## One-time Stockbit login

Final Watchlist is unattended on the server, so the branch deliberately refuses
to fall back to the legacy manual broker-export wait when Playwright is OFF.
Perform the existing collector's authenticated setup once from an interactive
Ubuntu desktop session:

```bash
cd /path/to/sde-swing

.venv/bin/python -m modules.portfolio.stockbit_playwright_collector setup
.venv/bin/python -m modules.portfolio.stockbit_playwright_collector enable
.venv/bin/python -m modules.portfolio.stockbit_playwright_collector status
```

`setup` opens Chromium headed and stores the authenticated persistent profile in:

```text
data/state/playwright/stockbit
```

Scheduled Final Watchlist then reuses that profile headlessly. If the session
expires, the canonical collector fails closed with login/authentication errors;
repeat `setup` and leave the trading engines unchanged.

## Activate now

After `.env`, dependencies and Stockbit setup are ready:

```bash
bash scripts/install_systemd_services.sh --start
```

Or install dependencies and activate in one installer invocation after the repo
and `.env` are ready:

```bash
bash scripts/install_systemd_services.sh --with-browser --start
```

## Health checks

```bash
bash scripts/check_systemd_services.sh
```

Useful direct commands:

```bash
systemctl list-timers --all 'sde-swing*'

sudo systemctl status sde-swing-idx-watcher --no-pager
journalctl -u sde-swing-idx-watcher -f

journalctl -u 'sde-swing-job@market_outlook.service' -n 100 --no-pager -l
journalctl -u 'sde-swing-job@post_market.service' -n 100 --no-pager -l
journalctl -u 'sde-swing-job@final_watchlist.service' -n 100 --no-pager -l
journalctl -u sde-swing-position-management.service -n 100 --no-pager -l
```

A `Type=oneshot` job service is normally `inactive (dead)` between runs. That is
healthy; the corresponding `.timer` is what remains active and waits for the
next schedule. The IDX Disclosure Watcher is different: it is persistent and
should show `active (running)`.

## Manual job validation

Before waiting for the next timer, individual service paths can be exercised
without changing their schedule:

```bash
sudo systemctl start 'sde-swing-job@market_outlook.service'
sudo systemctl start 'sde-swing-job@post_market.service'
sudo systemctl start 'sde-swing-job@final_watchlist.service'
sudo systemctl start sde-swing-position-management.service
sudo systemctl start sde-swing-idx-universe.service
```

Only run market-sensitive jobs manually when their data boundary is valid for
the intended session. The normal production path remains the configured timers.

## Server-specific operational boundaries

- Market Outlook server time is **08:45 WIB**.
- Morning/Post Market News execute only after a fresh `SUCCESS` primary job and
  remain non-blocking.
- Final Watchlist requires Stockbit Playwright enabled plus a persisted browser
  profile. It does not wait for a human export on the unattended server.
- Broker Playwright artifacts are staged in `data/runtime/broker_exports`, not a
  Windows `%USERPROFILE%/Downloads` path.
- Active Portfolio uses the existing integrity runtime and does not rerun the
  Decision Engine.
- IDX Universe refresh updates the already-configured
  `data/input/IDX_ALL_NON_FCA.csv`; the service does not use `--activate` and
  therefore does not rewrite pipeline configuration.
- Secrets remain in `.env`; systemd loads it through `EnvironmentFile`.
