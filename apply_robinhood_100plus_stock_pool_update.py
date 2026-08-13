#!/usr/bin/env python3
"""Refresh a final Robinhood stock bot with the expanded official asset set."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time


MIN_EXPECTED_POOLS = 300
MIN_EXPECTED_STOCK_SYMBOLS = 170
QUOTE_SYMBOLS = {"ETH", "WETH", "USDG"}


def load_pools_module(folder: Path):
    module_path = folder / "pools.py"
    if not module_path.is_file():
        raise SystemExit(
            "pools.py not found. Put this patch inside the final Robinhood "
            "multi-pool stock bot folder."
        )
    spec = importlib.util.spec_from_file_location(
        "robinhood_expanded_pool_refresh", module_path
    )
    if spec is None or spec.loader is None:
        raise SystemExit("Could not load pools.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name in (
        "_discover_pools",
        "_load_cache",
        "_merge_catalogs",
        "_save_cache",
        "validate_pool",
    ):
        if not hasattr(module, name):
            raise SystemExit(
                f"This patch needs the final Robinhood bot; pools.py has no {name}."
            )
    return module


def pool_ids(items) -> set[str]:
    return {str(item["pool_id"]).lower() for item in items}


def stock_symbols(items) -> set[str]:
    symbols: set[str] = set()
    for item in items:
        for key in ("symbol0", "symbol1"):
            symbol = str(item[key]).upper()
            if symbol not in QUOTE_SYMBOLS:
                symbols.add(symbol)
    return symbols


def main() -> None:
    folder = Path(__file__).resolve().parent
    catalog_path = folder / "robinhood_pool_catalog.json"
    if not catalog_path.is_file():
        raise SystemExit("robinhood_pool_catalog.json not found beside this patch.")

    module = load_pools_module(folder)
    old_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    old_items = old_payload.get("pools") or []

    print("Reading official Robinhood assets and eligible Uniswap pools...")
    discovered = module._discover_pools(30)
    cached = module._load_cache(catalog_path)
    merged = module._merge_catalogs(cached, discovered)
    new_items = [module.asdict(pool) for pool in merged.values()]

    for pool in merged.values():
        module.validate_pool(pool)

    ids = pool_ids(new_items)
    choices = {str(item["choice"]) for item in new_items}
    symbols = stock_symbols(new_items)
    if len(new_items) < MIN_EXPECTED_POOLS:
        raise SystemExit(
            f"Refusing incomplete update: {len(new_items)} pools found; "
            f"expected at least {MIN_EXPECTED_POOLS}."
        )
    if len(symbols) < MIN_EXPECTED_STOCK_SYMBOLS:
        raise SystemExit(
            f"Refusing incomplete update: only {len(symbols)} stock symbols found."
        )
    if len(ids) != len(new_items):
        raise SystemExit("Refusing update: duplicate pool IDs found.")
    if len(choices) != len(new_items):
        raise SystemExit("Refusing update: duplicate menu choices found.")

    old_ids = pool_ids(old_items)
    backup = catalog_path.with_name(
        f"{catalog_path.name}.before_100plus_{time.strftime('%Y%m%d_%H%M%S')}.bak"
    )
    shutil.copy2(catalog_path, backup)
    module._save_cache(catalog_path, merged)

    written = json.loads(catalog_path.read_text(encoding="utf-8"))
    if pool_ids(written.get("pools") or []) != ids:
        shutil.copy2(backup, catalog_path)
        raise SystemExit("Post-write verification failed; restored the backup.")

    added = len(ids - old_ids)
    v3 = sum(item["protocol"] == "v3" for item in new_items)
    v4 = sum(item["protocol"] == "v4" for item in new_items)
    print(f"Updated: {len(new_items)} pools ({v3} V3, {v4} V4).")
    print(f"Supported stock/ETF symbols: {len(symbols)} | newly added pools: {added}")
    print(f"Backup: {backup.name}")
    print("Next:")
    print(
        "  python3 -m py_compile pools.py pool_apr.py config.py abis.py "
        "blockchain.py notifications.py lp_bot.py diagnose.py"
    )
    print("  python3 check_pool_catalog.py")
    print("  python3 lp_bot.py")


if __name__ == "__main__":
    main()
