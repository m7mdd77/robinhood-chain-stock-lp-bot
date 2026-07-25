from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable
import urllib.request

from pools import PoolConfig


UNISWAP_GRAPHQL_URL = "https://interface.gateway.uniswap.org/v1/graphql"


@dataclass(frozen=True)
class PoolApr:
    fee_apr_percent: float
    rewards_apr_percent: float
    total_apr_percent: float
    volume_24h_usd: float
    tvl_usd: float


def fetch_pool_aprs(
    pools: Iterable[PoolConfig],
    *,
    timeout_seconds: float = 12,
) -> dict[str, PoolApr]:
    """Return Uniswap's live 24h-annualized APR for every registered pool."""
    pool_list = list(pools)
    result: dict[str, PoolApr] = {}

    def fetch_batch(batch: list[PoolConfig]) -> None:
        selections = []
        for index, pool in enumerate(batch):
            field = "v3Pool" if pool.protocol == "v3" else "v4Pool"
            identifier = "address" if pool.protocol == "v3" else "poolId"
            rewards_field = (
                "" if pool.protocol == "v3" else "rewardsCampaign { boostedApr }"
            )
            selections.append(
                f'''p{index}: {field}(chain: ROBINHOOD, {identifier}: "{pool.pool_id}") {{
                  feeTier
                  volume24h: cumulativeVolume(duration: DAY) {{ value }}
                  totalLiquidity {{ value }}
                  {rewards_field}
                }}'''
            )
        body = json.dumps(
            {"query": "query RobinhoodPoolApr {\n" + "\n".join(selections) + "\n}"}
        ).encode("utf-8")
        request = urllib.request.Request(
            UNISWAP_GRAPHQL_URL,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://app.uniswap.org",
                "User-Agent": "robinhood-multi-pool-v4-bot/2.0",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
        if payload.get("errors"):
            if len(batch) == 1:
                return
            midpoint = len(batch) // 2
            fetch_batch(batch[:midpoint])
            fetch_batch(batch[midpoint:])
            return
        data = payload.get("data") or {}
        for index, pool in enumerate(batch):
            item = data.get(f"p{index}") or {}
            volume = float((item.get("volume24h") or {}).get("value") or 0)
            tvl = float((item.get("totalLiquidity") or {}).get("value") or 0)
            fee_tier = float(item.get("feeTier") or pool.fee)
            rewards_apr = float(
                (item.get("rewardsCampaign") or {}).get("boostedApr") or 0
            )
            fee_apr = (
                volume * (fee_tier / 1_000_000) * 365 / tvl * 100
                if tvl > 0
                else 0
            )
            result[pool.choice] = PoolApr(
                fee_apr_percent=fee_apr,
                rewards_apr_percent=rewards_apr,
                total_apr_percent=fee_apr + rewards_apr,
                volume_24h_usd=volume,
                tvl_usd=tvl,
            )

    # Keep each GraphQL operation modest. If one alias fails inside a batch,
    # split it so the remaining pools still receive live APR values.
    for start in range(0, len(pool_list), 20):
        fetch_batch(pool_list[start : start + 20])
    return result


def format_pool_apr(apr: PoolApr | None) -> str:
    if apr is None:
        return "APR unavailable"
    if apr.rewards_apr_percent > 0:
        return (
            f"Total APR {apr.total_apr_percent:,.2f}% "
            f"(fees {apr.fee_apr_percent:,.2f}% + rewards {apr.rewards_apr_percent:,.2f}%)"
        )
    return f"Fee APR {apr.fee_apr_percent:,.2f}%"
