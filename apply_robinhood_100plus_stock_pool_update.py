#!/usr/bin/env python3
"""Refresh a final Robinhood stock bot with the expanded official asset set."""

from __future__ import annotations

import importlib.util
import base64
import gzip
import json
from pathlib import Path
import shutil
import sys
import time


MIN_EXPECTED_POOLS = 300
MIN_EXPECTED_STOCK_SYMBOLS = 170
QUOTE_SYMBOLS = {"ETH", "WETH", "USDG"}

# Gzip-compressed startup_positions.py. Embedded so this remains a single-file
# Kali updater that can be dropped into an existing final bot folder.
STARTUP_POSITIONS_GZIP_BASE64 = "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"

CONFIG_IMPORT_OLD = "from pools import ZERO_ADDRESS, load_pools\n"
CONFIG_IMPORT_NEW = (
    CONFIG_IMPORT_OLD + "from startup_positions import scan_and_print_before_selection\n"
)
CONFIG_POOLS_OLD = "POOLS = load_pools()\n"
CONFIG_POOLS_NEW = '''POOLS = load_pools()
EXISTING_POSITIONS = scan_and_print_before_selection(POOLS.values())
EXISTING_POSITIONS_BY_POOL = {
    position.pool_choice: tuple(
        item for item in EXISTING_POSITIONS if item.pool_choice == position.pool_choice
    )
    for position in EXISTING_POSITIONS
}
'''
CONFIG_MENU_OLD = '''        print(f"{rank}) {pool.label} | {format_pool_apr(apr)}{tvl}{low_tvl}")
'''
CONFIG_MENU_NEW = '''        existing = EXISTING_POSITIONS_BY_POOL.get(pool.choice, ())
        existing_note = (
            " | EXISTING " + ", ".join(f"NFT #{item.token_id}" for item in existing)
            if existing
            else ""
        )
        print(
            f"{rank}) {pool.label} | {format_pool_apr(apr)}{tvl}{low_tvl}"
            f"{existing_note}"
        )
'''

SCAN_DISABLED_OLD = '''    if not config.SCAN_EXISTING_POSITIONS:
        return False
'''
SCAN_DISABLED_NEW = '''    if not config.SCAN_EXISTING_POSITIONS:
        print("\\nExisting LP position scan is disabled by configuration.")
        return False
    print("\\n=== Existing LP position scan ===")
    print(f"Pool          : {config.POOL_LABEL}")
    print(f"Wallet        : {config.WALLET_ADDRESS}")
    print("Searching the selected pool before requesting a new deposit...")
'''
SCAN_RESULT_OLD = '''    if not positions:
        return False
    position = positions[0]
'''
SCAN_RESULT_NEW = '''    if not positions:
        print("Result        : no active wallet-owned position found for this pool.")
        return False
    print(f"Result        : {len(positions)} active position(s) found.")
    for index, discovered in enumerate(positions, start=1):
        discovered_low, discovered_high = bc.display_range_for_ticks(
            discovered.tick_lower,
            discovered.tick_upper,
        )
        selected = " [will resume]" if index == 1 else " [left untouched]"
        print(f"{index}) Token ID {discovered.token_id}{selected}")
        print(
            f"   Amounts    : {discovered.amount0:.8f} {config.TOKEN0_SYMBOL} + "
            f"{discovered.amount1:.8f} {config.TOKEN1_SYMBOL}"
        )
        print(
            f"   Range      : {discovered_low:.8f} - {discovered_high:.8f} "
            f"{config.QUOTE_SYMBOL} per {config.BASE_SYMBOL}"
        )
        print(f"   Est. value : {discovered.value_quote:.4f} {config.QUOTE_SYMBOL}")
    position = positions[0]
'''
MAIN_SCAN_OLD = '''    if state.token_id:
        try:
            bc.read_position(state.token_id)
            logger.info("Resuming position token_id=%s", state.token_id)
        except Exception as exc:
            logger.warning("Saved position is unavailable: %s", exc)
            state.token_id = 0
            save_state()
    if not state.token_id:
        resume_discovered_position()
    if not state.token_id:
        initial_deposit()
'''
MAIN_SCAN_NEW = '''    discovered_position = False
    try:
        discovered_position = resume_discovered_position()
    except Exception as exc:
        logger.warning(
            "On-chain existing-position scan failed: %s. Falling back to saved state.",
            exc,
        )
        print(f"Position scan : temporarily unavailable ({exc})")

    if not discovered_position and state.token_id:
        try:
            bc.read_position(state.token_id)
            logger.info("Resuming position token_id=%s", state.token_id)
        except Exception as exc:
            logger.warning("Saved position is unavailable: %s", exc)
            state.token_id = 0
            save_state()
    if not state.token_id:
        initial_deposit()
'''
LOG_READ_OLD = '''        logs = w3.eth.get_logs(
            {
                "address": manager,
                "fromBlock": chunk_start,
                "toBlock": chunk_end,
                "topics": [TRANSFER_TOPIC, None, wallet_topic],
            }
        )
'''
LOG_READ_NEW = '''        logs = _get_logs_with_range_splitting(
            {
                "address": manager,
                "fromBlock": chunk_start,
                "toBlock": chunk_end,
                "topics": [TRANSFER_TOPIC, None, wallet_topic],
            }
        )
'''
LOG_HELPER = '''def _get_logs_with_range_splitting(filter_params: dict[str, Any]) -> list[Any]:
    """Read logs while adapting to RPC providers with small block-range limits."""
    try:
        return list(w3.eth.get_logs(filter_params))
    except Exception as exc:
        start = int(filter_params["fromBlock"])
        end = int(filter_params["toBlock"])
        message = str(exc).lower()
        range_error = any(
            marker in message
            for marker in (
                "block range",
                "range is too large",
                "response size",
                "query returned more than",
                "limit exceeded",
                "too many results",
            )
        )
        if not range_error or start >= end:
            raise
        midpoint = (start + end) // 2
        logger.info(
            "RPC rejected log range %s..%s; retrying as %s..%s and %s..%s.",
            start,
            end,
            start,
            midpoint,
            midpoint + 1,
            end,
        )
        left = dict(filter_params, fromBlock=start, toBlock=midpoint)
        right = dict(filter_params, fromBlock=midpoint + 1, toBlock=end)
        return _get_logs_with_range_splitting(left) + _get_logs_with_range_splitting(right)


'''


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


def patch_existing_position_scan(folder: Path) -> list[str]:
    changed: list[str] = []
    lp_path = folder / "lp_bot.py"
    blockchain_path = folder / "blockchain.py"
    if not lp_path.is_file() or not blockchain_path.is_file():
        raise SystemExit("lp_bot.py or blockchain.py is missing from this bot folder.")

    lp_source = lp_path.read_text(encoding="utf-8")
    blockchain_source = blockchain_path.read_text(encoding="utf-8")
    patch_lp = "=== Existing LP position scan ===" not in lp_source
    patch_blockchain = "def _get_logs_with_range_splitting(" not in blockchain_source

    if patch_lp:
        replacements = (
            (SCAN_DISABLED_OLD, SCAN_DISABLED_NEW),
            (SCAN_RESULT_OLD, SCAN_RESULT_NEW),
            (MAIN_SCAN_OLD, MAIN_SCAN_NEW),
        )
        for old, new in replacements:
            if old not in lp_source:
                raise SystemExit(
                    "Existing-position patch does not match lp_bot.py; no source files changed."
                )
            lp_source = lp_source.replace(old, new, 1)

    if patch_blockchain:
        marker = "def find_existing_positions() -> list[PositionSnapshot]:\n"
        if LOG_READ_OLD not in blockchain_source or marker not in blockchain_source:
            raise SystemExit(
                "Existing-position patch does not match blockchain.py; no source files changed."
            )
        blockchain_source = blockchain_source.replace(LOG_READ_OLD, LOG_READ_NEW, 1)
        blockchain_source = blockchain_source.replace(marker, LOG_HELPER + marker, 1)

    if patch_lp:
        backup = lp_path.with_name(
            f"lp_bot.py.before_position_scan_{time.strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(lp_path, backup)
        lp_path.write_text(lp_source, encoding="utf-8")
        changed.append(lp_path.name)

    if patch_blockchain:
        backup = blockchain_path.with_name(
            f"blockchain.py.before_position_scan_{time.strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(blockchain_path, backup)
        blockchain_path.write_text(blockchain_source, encoding="utf-8")
        changed.append(blockchain_path.name)
    return changed


def install_preselection_position_scan(folder: Path) -> list[str]:
    changed: list[str] = []
    scanner_path = folder / "startup_positions.py"
    scanner_source = gzip.decompress(
        base64.b64decode(STARTUP_POSITIONS_GZIP_BASE64)
    ).decode("utf-8")
    # web3.py 7 returns a prefixless value from HexBytes.hex() on some
    # distributions. JSON-RPC topics must always carry the 0x prefix.
    scanner_source = scanner_source.replace(
        'TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()',
        '''TRANSFER_TOPIC = (
    "0x"
    + Web3.keccak(text="Transfer(address,address,uint256)")
    .hex()
    .removeprefix("0x")
)''',
        1,
    )
    if not scanner_path.is_file() or scanner_path.read_text(encoding="utf-8") != scanner_source:
        if scanner_path.is_file():
            backup = scanner_path.with_name(
                f"startup_positions.py.before_preselection_{time.strftime('%Y%m%d_%H%M%S')}.bak"
            )
            shutil.copy2(scanner_path, backup)
        scanner_path.write_text(scanner_source, encoding="utf-8")
        changed.append(scanner_path.name)

    config_path = folder / "config.py"
    if not config_path.is_file():
        raise SystemExit("config.py is missing from this bot folder.")
    source = config_path.read_text(encoding="utf-8")
    updated = source
    if "from startup_positions import scan_and_print_before_selection" not in updated:
        if CONFIG_IMPORT_OLD not in updated:
            raise SystemExit("Could not install preselection scan import in config.py.")
        updated = updated.replace(CONFIG_IMPORT_OLD, CONFIG_IMPORT_NEW, 1)
    if "EXISTING_POSITIONS = scan_and_print_before_selection" not in updated:
        if CONFIG_POOLS_OLD not in updated:
            raise SystemExit("Could not install preselection scan call in config.py.")
        updated = updated.replace(CONFIG_POOLS_OLD, CONFIG_POOLS_NEW, 1)
    if "existing_note = (" not in updated:
        if CONFIG_MENU_OLD not in updated:
            raise SystemExit("Could not add existing-position labels to the pool menu.")
        updated = updated.replace(CONFIG_MENU_OLD, CONFIG_MENU_NEW, 1)
    if updated != source:
        backup = config_path.with_name(
            f"config.py.before_preselection_{time.strftime('%Y%m%d_%H%M%S')}.bak"
        )
        shutil.copy2(config_path, backup)
        config_path.write_text(updated, encoding="utf-8")
        changed.append(config_path.name)
    return changed


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

    changed_sources = patch_existing_position_scan(folder)
    changed_sources.extend(install_preselection_position_scan(folder))

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
    if changed_sources:
        print("Added startup position discovery/output to: " + ", ".join(changed_sources))
    else:
        print("Startup position discovery/output was already installed.")
    print("Next:")
    print(
        "  python3 -m py_compile pools.py pool_apr.py startup_positions.py config.py abis.py "
        "blockchain.py notifications.py lp_bot.py diagnose.py"
    )
    print("  python3 check_pool_catalog.py")
    print("  python3 lp_bot.py")


if __name__ == "__main__":
    main()
