#!/usr/bin/env python3
from __future__ import annotations

"""Integrated runner shim for the market-first Post Market contract.

The canonical integrated runner remains unchanged. This shim replaces only
its Post Market report builder with the closing-session implementation and
adds a Post Market-only delivery guard for Telegram remote-ACK ambiguity.
Engine scoring and all other jobs keep their existing paths.
"""

import run_sde_job_integrated as integrated
from modules.job_runner.post_market_delivery_guard import deliver_post_market_hardened
from modules.job_runner.post_market_live import post_market_live_payloads


# `_enhanced_payloads()` resolves this module global at call time, so both
# standalone Post Market and Full Manual use the fresh closing pulse without
# changing Market Outlook or decision-engine code.
integrated.post_market_payloads = post_market_live_payloads

# The shared delivery implementation remains untouched. Only this market-first
# Post Market entrypoint gets the anti-duplicate remote-ACK guard.
integrated.deliver = deliver_post_market_hardened


if __name__ == "__main__":
    raise SystemExit(integrated.main())
