#!/usr/bin/env python3
"""Refresh the Robinhood stock-pool catalog from official live sources."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time


MIN_EXPECTED_POOLS = 180
REQUIRED_POOL_IDS = {
    "0x9194a557b6a6bb2236b49ea7e2bbccec5d3eeb705aef00903be4b3de1d949579",
    "0xe2b46c905e12ab8e2f864e4821a4325884c1b126",
}


def load_pools_module(folder: Path):
    module_path = folder / "pools.py"
    if not module_path.exists():
        raise SystemExit(
            "Run this updater inside the final Robinhood bot folder. "
            "Could not find pools.py."
        )
    spec = importlib.util.spec_from_file_location("robinhood_live_pools", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit("Could not load pools.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name in ("_discover_pools", "_save_cache"):
        if not hasattr(module, name):
            raise SystemExit(
                f"This is not the final Robinhood bot: pools.py has no {name}."
            )
    return module


def pool_ids(items) -> set[str]:
    return {str(item["pool_id"]).lower() for item in items}


def main() -> None:
    folder = Path.cwd()
    catalog_path = folder / "robinhood_pool_catalog.json"
    module = load_pools_module(folder)

    old_items = []
    if catalog_path.exists():
        try:
            old_items = json.loads(catalog_path.read_text(encoding="utf-8")).get(
                "pools", []
            )
        except Exception as exc:
            print(f"Existing catalog is invalid and will only be backed up: {exc}")

    print("Refreshing official Robinhood assets and Uniswap V3/V4 pools...")
    discovered = module._discover_pools(30)
    cached = module._load_cache(catalog_path) if catalog_path.exists() else {}
    merged = module._merge_catalogs(cached, discovered)
    new_items = [
        module.asdict(pool) if hasattr(module, "asdict") else pool.__dict__
        for pool in merged.values()
    ]
    for pool in merged.values():
        try:
            module.validate_pool(pool)
        except Exception as exc:
            raise SystemExit(
                f"Refusing refresh: invalid pool {pool.pool_id}: {exc}"
            ) from exc
    new_ids = pool_ids(new_items)
    choices = {str(item["choice"]) for item in new_items}

    if len(new_items) < MIN_EXPECTED_POOLS:
        raise SystemExit(
            f"Refusing incomplete refresh: only {len(new_items)} pools found; "
            f"expected at least {MIN_EXPECTED_POOLS}."
        )
    if len(new_ids) != len(new_items):
        raise SystemExit("Refusing refresh: duplicate pool IDs were discovered.")
    if len(choices) != len(new_items):
        raise SystemExit("Refusing refresh: duplicate catalog choices were discovered.")
    missing_required = REQUIRED_POOL_IDS - new_ids
    if missing_required:
        raise SystemExit(
            "Refusing refresh: required previously verified pools are missing: "
            + ", ".join(sorted(missing_required))
        )

    old_ids = pool_ids(old_items)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)

    if catalog_path.exists():
        backup = catalog_path.with_name(
            f"{catalog_path.name}.before_refresh_{time.strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(catalog_path, backup)
        print(f"Backup: {backup.name}")

    module._save_cache(catalog_path, merged)
    written = json.loads(catalog_path.read_text(encoding="utf-8"))
    if pool_ids(written.get("pools", [])) != new_ids:
        raise SystemExit("Catalog verification failed after writing.")

    v3 = sum(item.get("protocol") == "v3" for item in new_items)
    v4 = sum(item.get("protocol") == "v4" for item in new_items)
    print(f"Done: {len(new_items)} pools ({v3} V3, {v4} V4).")
    print(
        f"Live discovery: {len(discovered)} | Added: {len(added)} | "
        f"Retained from verified catalog: {len(merged) - len(discovered)}"
    )
    print(f"Removed: {len(removed)}")
    print("No trading or strategy logic was changed.")
    print("Next:")
    print(
        "  python3 -m py_compile pools.py pool_apr.py config.py abis.py "
        "blockchain.py notifications.py lp_bot.py diagnose.py"
    )
    print("  python3 lp_bot.py")


if __name__ == "__main__":
    main()
