from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable
import urllib.request

from eth_abi import encode
from web3 import Web3


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
USDG_ADDRESS = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
ROBINHOOD_ASSETS_URL = "https://api.robinhood.com/rhj/assets"
UNISWAP_GRAPHQL_URL = "https://interface.gateway.uniswap.org/v1/graphql"


@dataclass(frozen=True)
class PoolConfig:
    choice: str
    label: str
    pool_id: str
    currency0: str
    currency1: str
    symbol0: str
    symbol1: str
    decimals0: int
    decimals1: int
    fee: int
    tick_spacing: int
    protocol: str = "v4"
    hooks: str = ZERO_ADDRESS
    # The UI price is quote units per one base unit.
    base_index: int = 0
    # Token whose claimed LP fees should be converted to native ETH.
    stock_index: int = 0


def computed_v4_pool_id(pool: PoolConfig) -> str:
    encoded_key = encode(
        ["address", "address", "uint24", "int24", "address"],
        [
            Web3.to_checksum_address(pool.currency0),
            Web3.to_checksum_address(pool.currency1),
            int(pool.fee),
            int(pool.tick_spacing),
            Web3.to_checksum_address(pool.hooks),
        ],
    )
    return "0x" + Web3.keccak(encoded_key).hex().removeprefix("0x").lower()


def validate_pool(pool: PoolConfig) -> None:
    if pool.protocol.lower() == "v3":
        Web3.to_checksum_address(pool.pool_id)
        return
    if int(pool.currency0, 16) >= int(pool.currency1, 16):
        raise ValueError("V4 currencies are not in canonical address order")
    computed = computed_v4_pool_id(pool)
    if computed != pool.pool_id.lower():
        raise ValueError(
            f"V4 pool key mismatch: computed {computed}, expected {pool.pool_id.lower()}"
        )


# These explicitly requested pools remain pinned even if a different fee tier
# temporarily has more TVL. Dynamic discovery fills in every other asset.
PINNED_POOLS: dict[str, PoolConfig] = {
    "1": PoolConfig(
        choice="1",
        label="SPCX / USDG 1.00%",
        pool_id="0xcb6ffbcc84359535c2cc0a5688c0a76520ea6e0a4820fddd3ac8d7880e576370",
        currency0="0x4a0E65A3EcceC6dBe60AE065F2e7bb85Fae35eEa",
        currency1=USDG_ADDRESS,
        symbol0="SPCX",
        symbol1="USDG",
        decimals0=18,
        decimals1=6,
        fee=10_000,
        tick_spacing=200,
        base_index=0,
        stock_index=0,
    ),
    "2": PoolConfig(
        choice="2",
        label="AAPL / USDG 0.30%",
        pool_id="0xc748f4671a867db48b552f6b7650bf3255e05f80f00e3f7aad1b17ccb7898fdb",
        currency0=USDG_ADDRESS,
        currency1="0xaF3D76f1834A1d425780943C99Ea8A608f8a93f9",
        symbol0="USDG",
        symbol1="AAPL",
        decimals0=6,
        decimals1=18,
        fee=3_000,
        tick_spacing=60,
        base_index=1,
        stock_index=1,
    ),
    "3": PoolConfig(
        choice="3",
        label="ETH / NVDA 5.00%",
        pool_id="0xaa8039d8e39d2bbcae23762e71f5a12162875a61848f0d96914c827f61877ef6",
        currency0=ZERO_ADDRESS,
        currency1="0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC",
        symbol0="ETH",
        symbol1="NVDA",
        decimals0=18,
        decimals1=18,
        fee=50_000,
        tick_spacing=1_000,
        base_index=0,
        stock_index=1,
    ),
    "4": PoolConfig(
        choice="4",
        label="MSFT / USDG 2.00%",
        pool_id="0xace02af66d24427b162f80329e039b78c226fb9a79669f5e18d5feec2aa0c056",
        currency0=USDG_ADDRESS,
        currency1="0xe93237C50D904957Cf27E7B1133b510C669c2e74",
        symbol0="USDG",
        symbol1="MSFT",
        decimals0=6,
        decimals1=18,
        fee=20_000,
        tick_spacing=400,
        base_index=1,
        stock_index=1,
    ),
    "5": PoolConfig(
        choice="5",
        label="GOOGL / USDG 0.30%",
        pool_id="0xd4ecb79fdc521d7725d22b33ed43cb4e47aa96bfad76aa29577e3151f723ac5e",
        currency0="0x2e0847E8910a9732eB3fb1bb4b70a580ADAD4FE3",
        currency1=USDG_ADDRESS,
        symbol0="GOOGL",
        symbol1="USDG",
        decimals0=18,
        decimals1=6,
        fee=3_000,
        tick_spacing=60,
        base_index=0,
        stock_index=0,
    ),
}


def _request_json(
    url: str,
    *,
    timeout_seconds: float,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://app.uniswap.org",
            "User-Agent": "robinhood-multi-pool-v4-bot/2.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


def _official_assets(timeout_seconds: float) -> list[dict[str, str]]:
    payload = _request_json(ROBINHOOD_ASSETS_URL, timeout_seconds=timeout_seconds)
    assets: list[dict[str, str]] = []
    for item in payload.get("assets") or []:
        if item.get("status") not in {None, "ASSET_STATUS_ACTIVE"}:
            continue
        deployment = next(
            (
                value
                for value in item.get("deployments") or []
                if int(value.get("chainId") or value.get("chain_id") or 0) == 4663
            ),
            None,
        )
        address = (deployment or {}).get("contractAddress") or (deployment or {}).get(
            "contract_address"
        )
        symbol = item.get("tokenSymbol") or item.get("token_symbol")
        if address and symbol:
            assets.append({"address": address, "symbol": symbol})
    return assets


def _pool_query(assets: Iterable[dict[str, str]]) -> str:
    fields = []
    for index, asset in enumerate(assets):
        fields.append(
            f'''v4p{index}: topV4Pools(
              first: 100
              chain: ROBINHOOD
              tokenFilter: "{asset["address"]}"
            ) {{
              poolId
              feeTier
              tickSpacing
              token0 {{ address symbol decimals }}
              token1 {{ address symbol decimals }}
              hook {{ address }}
              totalLiquidity {{ value }}
            }}
            v3p{index}: topV3Pools(
              first: 100
              chain: ROBINHOOD
              tokenFilter: "{asset["address"]}"
            ) {{
              address
              feeTier
              token0 {{ address symbol decimals }}
              token1 {{ address symbol decimals }}
              totalLiquidity {{ value }}
            }}'''
        )
    return "query RobinhoodOfficialStockPools {\n" + "\n".join(fields) + "\n}"


def _discover_pools(timeout_seconds: float) -> dict[str, PoolConfig]:
    assets = _official_assets(timeout_seconds)
    candidates: dict[str, list[dict[str, Any]]] = {asset["symbol"]: [] for asset in assets}
    official = {asset["address"].lower(): asset for asset in assets}
    usdg = USDG_ADDRESS.lower()
    weth = WETH_ADDRESS.lower()
    max_fee = int(os.getenv("ROBINHOOD_POOL_MAX_FEE", "50000"))
    min_tvl = float(os.getenv("ROBINHOOD_POOL_MIN_TVL_USD", "100"))

    def query_batch(batch: list[dict[str, str]]) -> None:
        payload = _request_json(
            UNISWAP_GRAPHQL_URL,
            timeout_seconds=timeout_seconds,
            body={"query": _pool_query(batch)},
        )
        if payload.get("errors"):
            if len(batch) == 1:
                print(
                    f"Skipping live pool discovery for {batch[0]['symbol']} "
                    f"({payload['errors'][0]})."
                )
                return
            midpoint = len(batch) // 2
            query_batch(batch[:midpoint])
            query_batch(batch[midpoint:])
            return
        data = payload.get("data") or {}
        for index, _asset in enumerate(batch):
            protocol_pools = [
                ("v4", pool) for pool in data.get(f"v4p{index}") or []
            ] + [
                ("v3", pool) for pool in data.get(f"v3p{index}") or []
            ]
            for protocol, pool in protocol_pools:
                token0 = pool.get("token0") or {}
                token1 = pool.get("token1") or {}
                fee = int(float(pool.get("feeTier") or 0))
                tvl = float((pool.get("totalLiquidity") or {}).get("value") or 0)
                if fee <= 0 or fee > max_fee or tvl < min_tvl:
                    continue
                address0 = (token0.get("address") or ZERO_ADDRESS).lower()
                address1 = (token1.get("address") or ZERO_ADDRESS).lower()
                stock0 = official.get(address0)
                stock1 = official.get(address1)
                quote0 = address0 in {ZERO_ADDRESS, usdg, weth}
                quote1 = address1 in {ZERO_ADDRESS, usdg, weth}
                hook = ((pool.get("hook") or {}).get("address") or ZERO_ADDRESS).lower()
                if protocol == "v4" and hook != ZERO_ADDRESS:
                    continue
                pool["_protocol"] = protocol
                if stock0 and quote1:
                    candidates[stock0["symbol"]].append(pool)
                elif stock1 and quote0:
                    candidates[stock1["symbol"]].append(pool)

    for start in range(0, len(assets), 12):
        query_batch(assets[start : start + 12])

    selected: list[PoolConfig] = list(PINNED_POOLS.values())
    used_ids: set[str] = {pool.pool_id.lower() for pool in selected}
    invalid_discovered = 0
    for asset in sorted(assets, key=lambda value: value["symbol"]):
        symbol = asset["symbol"]
        available = candidates.get(symbol) or []
        for pool in available:
            token0 = pool.get("token0") or {}
            token1 = pool.get("token1") or {}
            address0 = (token0.get("address") or ZERO_ADDRESS).lower()
            address1 = (token1.get("address") or ZERO_ADDRESS).lower()
            stock_index = 0 if address0 == asset["address"].lower() else 1
            quote_address = address1 if stock_index == 0 else address0
            quote_kind = "usdg" if quote_address == usdg else "eth"
            protocol = str(pool.get("_protocol") or "v4")
            pool_id = str(pool.get("poolId") or pool.get("address")).lower()
            if pool_id in used_ids:
                continue
            fee = int(pool["feeTier"])
            choice = f"{protocol}-{symbol.lower()}-{quote_kind}-{fee}-{pool_id[-6:]}"
            label = (
                f"{token0['symbol']} / {token1['symbol']} "
                f"{fee / 10_000:.2f}% ({protocol.upper()})"
            )
            candidate = PoolConfig(
                choice=choice,
                label=label,
                pool_id=pool_id,
                currency0=address0,
                currency1=address1,
                symbol0=token0["symbol"],
                symbol1=token1["symbol"],
                decimals0=int(token0["decimals"]),
                decimals1=int(token1["decimals"]),
                fee=fee,
                tick_spacing=int(
                    pool.get("tickSpacing")
                    or {100: 1, 500: 10, 3000: 60, 10000: 200}.get(fee, 1)
                ),
                protocol=protocol,
                base_index=(
                    0
                    if token0["symbol"] in {"ETH", "WETH"}
                    else 1
                    if token1["symbol"] in {"ETH", "WETH"}
                    else stock_index
                ),
                stock_index=stock_index,
            )
            try:
                validate_pool(candidate)
            except ValueError:
                invalid_discovered += 1
                continue
            selected.append(candidate)
            used_ids.add(pool_id)

    if invalid_discovered:
        print(
            f"Skipped {invalid_discovered} incompatible Uniswap V4 pool "
            "candidate(s) with noncanonical pool keys."
        )
    return {pool.choice: pool for pool in selected}


def _load_cache(cache_path: Path) -> dict[str, PoolConfig]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    valid: dict[str, PoolConfig] = {}
    rejected = 0
    for item in payload.get("pools") or []:
        pool = PoolConfig(**item)
        try:
            validate_pool(pool)
        except (TypeError, ValueError) as exc:
            rejected += 1
            print(f"Skipping invalid cached pool {pool.pool_id}: {exc}")
            continue
        valid[pool.choice] = pool
    if rejected:
        noun = "entry" if rejected == 1 else "entries"
        print(
            f"Rejected {rejected} pool catalog {noun} with invalid on-chain identifiers."
        )
    return valid


def _save_cache(cache_path: Path, pools: dict[str, PoolConfig]) -> None:
    payload = {
        "saved_at": time.time(),
        "pools": [asdict(pool) for pool in pools.values()],
    }
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(cache_path)


def _merge_catalogs(
    cached: dict[str, PoolConfig],
    discovered: dict[str, PoolConfig],
) -> dict[str, PoolConfig]:
    """Use successful live discovery as authoritative; cache is fallback only."""
    by_id = {pool.pool_id.lower(): pool for pool in discovered.values()}
    merged: dict[str, PoolConfig] = {}
    for pool in by_id.values():
        choice = pool.choice
        if choice in merged and merged[choice].pool_id.lower() != pool.pool_id.lower():
            choice = f"{choice}-{pool.pool_id[-6:]}"
            pool = PoolConfig(**{**asdict(pool), "choice": choice})
        merged[choice] = pool
    return merged


def load_pools() -> dict[str, PoolConfig]:
    cache_path = Path(
        os.getenv(
            "ROBINHOOD_POOL_CATALOG_FILE",
            str(Path(__file__).with_name("robinhood_pool_catalog.json")),
        )
    )
    refresh_hours = float(os.getenv("ROBINHOOD_POOL_CATALOG_REFRESH_HOURS", "0"))
    timeout_seconds = float(os.getenv("ROBINHOOD_POOL_CATALOG_TIMEOUT_SECONDS", "20"))
    cached: dict[str, PoolConfig] = {}
    if cache_path.exists():
        try:
            cached = _load_cache(cache_path)
            age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
            if cached and age_hours < refresh_hours:
                return cached
        except Exception as exc:
            print(f"Ignoring invalid Robinhood pool cache ({exc}).")
    try:
        discovered = _discover_pools(timeout_seconds)
        if not discovered:
            raise RuntimeError("no compatible official stock pools were discovered")
        merged = _merge_catalogs(cached, discovered)
        _save_cache(cache_path, merged)
        print(
            f"Loaded {len(merged)} compatible Robinhood stock pools "
            f"({len(discovered)} found live, {len(merged) - len(discovered)} "
            "retained from the verified catalog)."
        )
        return merged
    except Exception as exc:
        if cached:
            print(f"Pool catalog refresh failed ({exc}); using cached catalog.")
            return cached
        print(f"Pool catalog discovery failed ({exc}); using pinned pools only.")
        return dict(PINNED_POOLS)


# Compatibility for imports that expect a module-level catalog. config.py calls
# load_pools after loading .env, so runtime settings still take effect.
POOLS = PINNED_POOLS
