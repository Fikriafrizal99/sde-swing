"""IDX Disclosure Watcher entrypoint — architecture scaffold only.

This runner is intentionally non-operational until Phase 2 implements the HTTP
client, SQLite repository, formatter, delivery integration and tests. The config
ships with `enabled=false` and delivery disabled.
"""

from __future__ import annotations


def main() -> int:
    print("IDX Disclosure Watcher scaffold is installed but disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
