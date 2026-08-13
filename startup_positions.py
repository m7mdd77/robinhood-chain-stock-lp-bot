from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Iterable

from web3 import Web3

import abis
from pools import PoolConfig, computed_v4_pool_id


TRANSFER_TOPIC = (
    "0x"
    + Web3.keccak(text="Transfer(address,address,uint256)")
    .hex()
    .removeprefix("0x")
)
V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
V3_POSITION_MANAGER = "0x73991a25c818bf1f1128deaab1492d45638de0d3"


@dataclass(frozen=True)
class ExistingPosition:
    token_id: int
    protocol: str
    pool_choice: str
    pool_id: str
    pool_label: str
    tick_lower: int
    tick_upper: int
    liquidity: int


def _signed_int24(value: int) -> int:
    value &= 0xFFFFFF
    return value - (1 << 24) if value & (1 << 23) else value


def _get_logs_split(w3: Web3, params: dict[str, Any]) -> list[Any]:
    start = int(params["fromBlock"])
    end = int(params["toBlock"])
    last_error: Exception | None = None
    split_required = False
    for attempt in range(1, 4):
        try:
            return list(w3.eth.get_logs(params))
        except Exception as exc:
            last_error = exc
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
            transient = any(
                marker in message
                for marker in (
                    "i/o timeout",
                    "timed out",
                    "timeout",
                    "connection reset",
                    "connection aborted",
                    "bad gateway",
                    "502",
                    "503",
                    "504",
                )
            )
            if range_error:
                split_required = True
                break
            if not transient:
                raise
            if attempt < 3:
                time.sleep(attempt * 2)
            else:
                split_required = True
    if not split_required or start >= end:
        assert last_error is not None
        raise last_error
    midpoint = (start + end) // 2
    print(
        f"RPC log query {start}..{end} timed out or exceeded limits; "
        f"splitting into {start}..{midpoint} and {midpoint + 1}..{end}."
    )
    left = dict(params, fromBlock=start, toBlock=midpoint)
    right = dict(params, fromBlock=midpoint + 1, toBlock=end)
    return _get_logs_split(w3, left) + _get_logs_split(w3, right)


def _received_token_ids(
    w3: Web3,
    manager: str,
    wallet: str,
    start: int,
    latest: int,
    chunk_size: int,
) -> set[int]:
    wallet_topic = "0x" + ("0" * 24) + wallet[2:].lower()
    token_ids: set[int] = set()
    for chunk_start in range(start, latest + 1, chunk_size):
        chunk_end = min(latest, chunk_start + chunk_size - 1)
        logs = _get_logs_split(
            w3,
            {
                "address": Web3.to_checksum_address(manager),
                "fromBlock": chunk_start,
                "toBlock": chunk_end,
                "topics": [TRANSFER_TOPIC, None, wallet_topic],
            },
        )
        for event in logs:
            if len(event["topics"]) >= 4:
                token_ids.add(int(event["topics"][3].hex(), 16))
    return token_ids


def _catalog_maps(pools: Iterable[PoolConfig]):
    v3: dict[tuple[str, str, int], PoolConfig] = {}
    v4: dict[str, PoolConfig] = {}
    for pool in pools:
        if pool.protocol.lower() == "v3":
            v3[(pool.currency0.lower(), pool.currency1.lower(), int(pool.fee))] = pool
        else:
            v4[computed_v4_pool_id(pool).lower()] = pool
    return v3, v4


def scan_existing_positions(
    pools: Iterable[PoolConfig],
    *,
    rpc_url: str,
    wallet_address: str,
    scan_blocks: int,
    chunk_blocks: int,
) -> list[ExistingPosition]:
    """Read wallet-owned Uniswap NFTs across every pool in the local catalog."""
    wallet = Web3.to_checksum_address(wallet_address)
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if int(w3.eth.chain_id) != 4663:
        raise RuntimeError(f"RPC is on chain {w3.eth.chain_id}, expected Robinhood 4663")

    latest = int(w3.eth.block_number)
    start = max(0, latest - max(1, int(scan_blocks)))
    chunk_size = max(1, int(chunk_blocks))
    v3_catalog, v4_catalog = _catalog_maps(pools)
    found: list[ExistingPosition] = []

    v3_manager = w3.eth.contract(
        Web3.to_checksum_address(V3_POSITION_MANAGER),
        abi=abis.V3_POSITION_MANAGER_ABI,
    )
    for token_id in sorted(
        _received_token_ids(
            w3, V3_POSITION_MANAGER, wallet, start, latest, chunk_size
        ),
        reverse=True,
    ):
        try:
            if v3_manager.functions.ownerOf(token_id).call().lower() != wallet.lower():
                continue
            position = v3_manager.functions.positions(token_id).call()
            liquidity = int(position[7])
            pool = v3_catalog.get(
                (position[2].lower(), position[3].lower(), int(position[4]))
            )
            if pool is None or liquidity <= 0:
                continue
            found.append(
                ExistingPosition(
                    token_id,
                    "v3",
                    pool.choice,
                    pool.pool_id,
                    pool.label,
                    int(position[5]),
                    int(position[6]),
                    liquidity,
                )
            )
        except Exception:
            continue

    v4_manager = w3.eth.contract(
        Web3.to_checksum_address(V4_POSITION_MANAGER),
        abi=abis.POSITION_MANAGER_ABI,
    )
    for token_id in sorted(
        _received_token_ids(
            w3, V4_POSITION_MANAGER, wallet, start, latest, chunk_size
        ),
        reverse=True,
    ):
        try:
            if v4_manager.functions.ownerOf(token_id).call().lower() != wallet.lower():
                continue
            pool_key, packed = v4_manager.functions.getPoolAndPositionInfo(token_id).call()
            key_pool = PoolConfig(
                choice="scan",
                label="scan",
                pool_id="0x" + ("00" * 32),
                currency0=pool_key[0],
                currency1=pool_key[1],
                symbol0="",
                symbol1="",
                decimals0=18,
                decimals1=18,
                fee=int(pool_key[2]),
                tick_spacing=int(pool_key[3]),
                hooks=pool_key[4],
            )
            # computed_v4_pool_id only hashes the key; the placeholder pool_id is ignored.
            pool = v4_catalog.get(computed_v4_pool_id(key_pool).lower())
            liquidity = int(v4_manager.functions.getPositionLiquidity(token_id).call())
            if pool is None or liquidity <= 0:
                continue
            packed = int(packed)
            found.append(
                ExistingPosition(
                    token_id,
                    "v4",
                    pool.choice,
                    pool.pool_id,
                    pool.label,
                    _signed_int24(packed >> 8),
                    _signed_int24(packed >> 32),
                    liquidity,
                )
            )
        except Exception:
            continue

    return sorted(found, key=lambda item: (item.pool_label, -item.token_id))


def scan_and_print_before_selection(
    pools: Iterable[PoolConfig],
) -> tuple[ExistingPosition, ...]:
    if os.getenv("SCAN_EXISTING_POSITIONS", "true").lower() not in {
        "1",
        "true",
        "yes",
        "y",
    }:
        return ()
    wallet = os.getenv("WALLET_ADDRESS", "").strip()
    if not wallet:
        print("\nExisting-position scan skipped: WALLET_ADDRESS is not set.")
        return ()
    print("\n=== Existing wallet LP position scan (before pool selection) ===")
    try:
        positions = scan_existing_positions(
            pools,
            rpc_url=os.getenv(
                "ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com"
            ).strip(),
            wallet_address=wallet,
            scan_blocks=int(os.getenv("POSITION_SCAN_BLOCKS", "5000000")),
            chunk_blocks=int(os.getenv("POSITION_SCAN_CHUNK_BLOCKS", "50000")),
        )
    except Exception as exc:
        if os.getenv("REQUIRE_EXISTING_POSITION_SCAN", "true").lower() in {
            "1",
            "true",
            "yes",
            "y",
        }:
            raise RuntimeError(
                "Required existing-position scan failed after adaptive RPC retries. "
                "The bot stopped before pool selection to avoid opening a duplicate LP. "
                f"RPC detail: {exc}"
            ) from exc
        print(f"Existing-position scan unavailable ({exc}). Continuing because REQUIRE_EXISTING_POSITION_SCAN=false.")
        return ()
    if not positions:
        print("No active wallet-owned Uniswap LP NFT matched the supported pool catalog.")
        return ()
    print(f"Found {len(positions)} active position(s):")
    for position in positions:
        print(
            f"- {position.pool_label} | {position.protocol.upper()} NFT "
            f"#{position.token_id} | ticks {position.tick_lower}..{position.tick_upper} "
            f"| liquidity {position.liquidity}"
        )
        print(f"  Pool ID: {position.pool_id}")
    return tuple(positions)
