#!/usr/bin/env python3
"""Validate every cached Robinhood pool before starting the trading bot."""

from __future__ import annotations

import json
from pathlib import Path

from pools import PoolConfig, validate_pool


def main() -> None:
    path = Path(__file__).with_name("robinhood_pool_catalog.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    pools = [PoolConfig(**item) for item in payload.get("pools") or []]
    if not pools:
        raise SystemExit("Catalog contains no pools.")
    seen_ids: set[str] = set()
    seen_choices: set[str] = set()
    protocols = {"v3": 0, "v4": 0}
    for pool in pools:
        validate_pool(pool)
        pool_id = pool.pool_id.lower()
        if pool_id in seen_ids:
            raise SystemExit(f"Duplicate pool ID: {pool.pool_id}")
        if pool.choice in seen_choices:
            raise SystemExit(f"Duplicate pool choice: {pool.choice}")
        seen_ids.add(pool_id)
        seen_choices.add(pool.choice)
        protocols[pool.protocol.lower()] = protocols.get(pool.protocol.lower(), 0) + 1
    print(
        f"Catalog OK: {len(pools)} unique pools "
        f"({protocols.get('v3', 0)} V3, {protocols.get('v4', 0)} V4)."
    )


if __name__ == "__main__":
    main()
