from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from pool_apr import fetch_pool_aprs, format_pool_apr
from pools import ZERO_ADDRESS, load_pools
from startup_positions import scan_and_print_before_selection


load_dotenv(Path(__file__).with_name(".env"))
POOLS = load_pools()
EXISTING_POSITIONS = scan_and_print_before_selection(POOLS.values())
EXISTING_POSITIONS_BY_POOL = {
    position.pool_choice: tuple(
        item for item in EXISTING_POSITIONS if item.pool_choice == position.pool_choice
    )
    for position in EXISTING_POSITIONS
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required setting {name} in .env")
    return value


def _address(value: str) -> str:
    return Web3.to_checksum_address(value)


def _pool_reference(value: str) -> str:
    match = re.search(r"0x[a-fA-F0-9]{40,64}", value)
    return match.group(0).lower() if match else value.strip().lower()


def _matching_pools(value: str):
    reference = _pool_reference(value)
    exact = [
        pool
        for key, pool in POOLS.items()
        if reference in {
            key.lower(),
            pool.choice.lower(),
            pool.pool_id.lower(),
            pool.label.lower(),
        }
    ]
    if exact:
        return exact
    words = [word for word in re.split(r"[\s/_-]+", reference) if word]
    return [
        pool
        for pool in POOLS.values()
        if all(
            word
            in " ".join(
                (
                    pool.label,
                    pool.symbol0,
                    pool.symbol1,
                    pool.protocol,
                    pool.pool_id,
                )
            ).lower()
            for word in words
        )
    ]


def _select_pool():
    configured = os.getenv("POOL_CHOICE", "").strip()
    if configured:
        matches = _matching_pools(configured)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            labels = ", ".join(pool.label for pool in matches[:8])
            raise ValueError(
                f"POOL_CHOICE is ambiguous ({labels}). Use the full pool ID."
            )
        raise ValueError(
            "POOL_CHOICE was not found in the catalog. Run "
            "python3 refresh_pool_catalog.py or use a current pool ID."
        )
    if not sys.stdin.isatty():
        return POOLS.get("1") or next(iter(POOLS.values()))
    print("\n=== Robinhood stock pool setup ===")
    pool_aprs = {}
    if os.getenv("SHOW_POOL_APR", "true").lower() in {"1", "true", "yes", "y"}:
        try:
            pool_aprs = fetch_pool_aprs(
                POOLS.values(),
                timeout_seconds=float(os.getenv("POOL_APR_TIMEOUT_SECONDS", "12")),
            )
            print("Live APR: latest 24h LP fees annualized; not a guaranteed return.")
        except Exception as exc:
            print(f"Live pool APR unavailable ({exc}). Pool selection is still available.")
    ranked_pools = sorted(
        POOLS.values(),
        key=lambda pool: (
            pool_aprs.get(pool.choice) is not None,
            (
                pool_aprs[pool.choice].total_apr_percent
                if pool.choice in pool_aprs
                else -1.0
            ),
            pool.label,
        ),
        reverse=True,
    )
    print("Pools are ranked by live APR from highest to lowest.")
    for rank, pool in enumerate(ranked_pools, start=1):
        apr = pool_aprs.get(pool.choice)
        tvl = f" | TVL ${apr.tvl_usd:,.2f}" if apr is not None else ""
        low_tvl = " | LOW TVL" if apr is not None and apr.tvl_usd < 1_000 else ""
        existing = EXISTING_POSITIONS_BY_POOL.get(pool.choice, ())
        existing_note = (
            " | EXISTING " + ", ".join(f"NFT #{item.token_id}" for item in existing)
            if existing
            else ""
        )
        print(
            f"{rank}) {pool.label} | {format_pool_apr(apr)}{tvl}{low_tvl}"
            f"{existing_note}"
        )
        print(f"   Pool ID: {pool.pool_id}")
    while True:
        choice = input(
            f"Choose number (1-{len(ranked_pools)}), ticker, pool ID, "
            "or Uniswap pool URL: "
        ).strip()
        try:
            selected_rank = int(choice)
        except ValueError:
            selected_rank = 0
        if 1 <= selected_rank <= len(ranked_pools):
            return ranked_pools[selected_rank - 1]
        matches = _matching_pools(choice)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f"{len(matches)} pools match {choice!r}:")
            for pool in matches[:20]:
                print(f"  {pool.label} | {pool.pool_id}")
            print("Paste the exact pool ID or full Uniswap URL to select one.")
            continue
        print("No matching pool. Try its ticker, exact pool ID, or Uniswap URL.")


def _select_range() -> float:
    configured = os.getenv("RANGE_PERCENT", "").strip()
    if configured:
        value = float(configured)
    elif not sys.stdin.isatty():
        value = 3.0
    else:
        while True:
            raw = input("Range percent up/down (example 3): ").strip()
            try:
                value = float(raw)
                break
            except ValueError:
                print("Enter a numeric percent, for example 3.")
    if not 0.01 <= value <= 50:
        raise ValueError("RANGE_PERCENT must be between 0.01 and 50")
    return value


ACTIVE_POOL = _select_pool()
RANGE_PERCENT = _select_range()

PRIVATE_KEY = _required("PRIVATE_KEY")
WALLET_ADDRESS = _address(_required("WALLET_ADDRESS"))
RPC_URL = os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com").strip()
CHAIN_ID = 4663

POOL_LABEL = ACTIVE_POOL.label
POOL_PROTOCOL = ACTIVE_POOL.protocol.lower()
POOL_ID = (
    _address(ACTIVE_POOL.pool_id)
    if POOL_PROTOCOL == "v3"
    else ACTIVE_POOL.pool_id.lower()
)
TOKEN0_ADDRESS = _address(ACTIVE_POOL.currency0)
TOKEN1_ADDRESS = _address(ACTIVE_POOL.currency1)
TOKEN0_SYMBOL = ACTIVE_POOL.symbol0
TOKEN1_SYMBOL = ACTIVE_POOL.symbol1
TOKEN0_DECIMALS = ACTIVE_POOL.decimals0
TOKEN1_DECIMALS = ACTIVE_POOL.decimals1
POOL_FEE = ACTIVE_POOL.fee
TICK_SPACING = ACTIVE_POOL.tick_spacing
HOOK_ADDRESS = _address(ACTIVE_POOL.hooks)
BASE_INDEX = ACTIVE_POOL.base_index
STOCK_INDEX = ACTIVE_POOL.stock_index
BASE_SYMBOL = TOKEN0_SYMBOL if BASE_INDEX == 0 else TOKEN1_SYMBOL
QUOTE_SYMBOL = TOKEN1_SYMBOL if BASE_INDEX == 0 else TOKEN0_SYMBOL
STOCK_TOKEN_ADDRESS = TOKEN0_ADDRESS if STOCK_INDEX == 0 else TOKEN1_ADDRESS
STOCK_TOKEN_SYMBOL = TOKEN0_SYMBOL if STOCK_INDEX == 0 else TOKEN1_SYMBOL
STOCK_TOKEN_DECIMALS = TOKEN0_DECIMALS if STOCK_INDEX == 0 else TOKEN1_DECIMALS
HAS_NATIVE0 = TOKEN0_ADDRESS.lower() == ZERO_ADDRESS
HAS_NATIVE1 = TOKEN1_ADDRESS.lower() == ZERO_ADDRESS

# Official Uniswap V4 Robinhood Chain deployments.
POOL_MANAGER = _address("0x8366a39cc670b4001a1121b8f6a443a643e40951")
POSITION_MANAGER = _address("0x58daec3116aae6d93017baaea7749052e8a04fa7")
STATE_VIEW = _address("0xf3334192d15450cdd385c8b70e03f9a6bd9e673b")
V4_QUOTER = _address("0x8dc178efb8111bb0973dd9d722ebeff267c98f94")
UNIVERSAL_ROUTER = _address("0x8876789976decbfcbbbe364623c63652db8c0904")
PERMIT2 = _address("0x000000000022D473030F116dDEE9F6B43aC78BA3")

# Official Uniswap V3 Robinhood Chain deployments.
V3_FACTORY = _address("0x1f7d7550b1b028f7571e69a784071f0205fd2efa")
V3_QUOTER = _address("0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7")
V3_POSITION_MANAGER = _address("0x73991a25c818bf1f1128deaab1492d45638de0d3")
V3_SWAP_ROUTER = _address("0xcaf681a66d020601342297493863e78c959e5cb2")

RANGE_DOWN_PERCENT = RANGE_PERCENT
RANGE_UP_PERCENT = RANGE_PERCENT
CHECK_INTERVAL_MINUTES = 10.0
CONFIRM_OUT_OF_RANGE_CHECKS = 2
REBALANCE_BUFFER_PERCENT = 0.0

SLIPPAGE_PERCENT = float(os.getenv("SLIPPAGE_PERCENT", "0.01"))
MAX_PRICE_IMPACT_PERCENT = float(os.getenv("MAX_PRICE_IMPACT_PERCENT", "0.05"))
LP_MINT_TOLERANCE_PERCENT = float(os.getenv("LP_MINT_TOLERANCE_PERCENT", "0.20"))
LP_WITHDRAW_TOLERANCE_PERCENT = float(
    os.getenv("LP_WITHDRAW_TOLERANCE_PERCENT", "0.50")
)
MIN_SWAP_VALUE_QUOTE = float(os.getenv("MIN_SWAP_VALUE_QUOTE", "1"))
MIN_SWAP_VALUE_ETH = float(os.getenv("MIN_SWAP_VALUE_ETH", "0.0005"))
# Never let an environment override reduce the wallet below the requested gas
# reserve. A larger value is still allowed for users who want more headroom.
NATIVE_GAS_RESERVE_ETH = max(0.01, float(os.getenv("NATIVE_GAS_RESERVE_ETH", "0.01")))
USDG_ADDRESS = _address(
    os.getenv("USDG_ADDRESS", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168")
)
USDG_DECIMALS = 6
SWAP_RETRY_SECONDS = int(os.getenv("SWAP_RETRY_SECONDS", "15"))
MAX_SWAP_ATTEMPTS = int(os.getenv("MAX_SWAP_ATTEMPTS", "8"))
MAX_OPEN_ATTEMPTS = int(os.getenv("MAX_OPEN_ATTEMPTS", "8"))
RECEIPT_READ_ATTEMPTS = int(os.getenv("RECEIPT_READ_ATTEMPTS", "6"))
RECEIPT_RETRY_SECONDS = int(os.getenv("RECEIPT_RETRY_SECONDS", "5"))
TX_DEADLINE_SECONDS = int(os.getenv("TX_DEADLINE_SECONDS", "180"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "25"))

SWAP_ROUTE_PROVIDERS = tuple(
    item.strip().lower()
    for item in os.getenv("SWAP_ROUTE_PROVIDERS", "uniswap,kyberswap").split(",")
    if item.strip()
)
KYBER_ROUTES_URL = os.getenv(
    "KYBER_ROUTES_URL", "https://aggregator-api.kyberswap.com/robinhood/api/v1/routes"
).strip()
KYBER_BUILD_URL = os.getenv(
    "KYBER_BUILD_URL", "https://aggregator-api.kyberswap.com/robinhood/api/v1/route/build"
).strip()
KYBER_CLIENT_ID = os.getenv("KYBER_CLIENT_ID", "robinhood-multi-pool-v4-bot").strip()
KYBER_NATIVE_ADDRESS = _address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")
KYBER_ALLOWED_HOST = "aggregator-api.kyberswap.com"
KYBER_ALLOWED_ROUTERS = tuple(
    _address(item.strip())
    for item in os.getenv(
        "KYBER_ALLOWED_ROUTERS",
        "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5",
    ).split(",")
    if item.strip()
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STATUS_REPORT_HOURS = float(os.getenv("STATUS_REPORT_HOURS", "4"))
FEE_CLAIM_HOURS = float(os.getenv("FEE_CLAIM_HOURS", "4"))
SELL_STOCK_FEES_TO_ETH = os.getenv("SELL_STOCK_FEES_TO_ETH", "true").lower() in {"1", "true", "yes", "y"}
MIN_STOCK_FEE_SELL_USD = float(os.getenv("MIN_STOCK_FEE_SELL_USD", "1"))
WITHDRAW_ON_CTRL_C = os.getenv("WITHDRAW_ON_CTRL_C", "true").lower() in {"1", "true", "yes", "y"}
SCAN_EXISTING_POSITIONS = os.getenv("SCAN_EXISTING_POSITIONS", "true").lower() in {
    "1",
    "true",
    "yes",
    "y",
}
POSITION_SCAN_BLOCKS = int(os.getenv("POSITION_SCAN_BLOCKS", "5000000"))
POSITION_SCAN_CHUNK_BLOCKS = int(
    os.getenv("POSITION_SCAN_CHUNK_BLOCKS", "50000")
)

STATE_FILE = Path(__file__).with_name(
    os.getenv("STATE_FILE", f"bot_state_pool_{ACTIVE_POOL.choice}.json")
)
LOG_FILE = Path(__file__).with_name(f"robinhood_pool_{ACTIVE_POOL.choice}.log")

GAS_LIMIT_MULTIPLIER = float(os.getenv("GAS_LIMIT_MULTIPLIER", "1.20"))
PRIORITY_FEE_GWEI = float(os.getenv("PRIORITY_FEE_GWEI", "0.02"))
MAX_FEE_BASE_MULTIPLIER = float(os.getenv("MAX_FEE_BASE_MULTIPLIER", "2"))

if not 0 < SLIPPAGE_PERCENT <= 5:
    raise ValueError("SLIPPAGE_PERCENT must be greater than 0 and at most 5")
if not 0 <= MAX_PRICE_IMPACT_PERCENT <= 10:
    raise ValueError("MAX_PRICE_IMPACT_PERCENT must be between 0 and 10")
if not 0 < LP_MINT_TOLERANCE_PERCENT <= 5:
    raise ValueError("LP_MINT_TOLERANCE_PERCENT must be greater than 0 and at most 5")
if not 0 < LP_WITHDRAW_TOLERANCE_PERCENT <= 5:
    raise ValueError("LP_WITHDRAW_TOLERANCE_PERCENT must be greater than 0 and at most 5")
if MAX_SWAP_ATTEMPTS <= 0 or MAX_OPEN_ATTEMPTS <= 0:
    raise ValueError("MAX_SWAP_ATTEMPTS and MAX_OPEN_ATTEMPTS must be greater than 0")
if RECEIPT_READ_ATTEMPTS <= 0 or RECEIPT_RETRY_SECONDS < 0:
    raise ValueError("Receipt retry settings are invalid")
if not KYBER_ALLOWED_ROUTERS:
    raise ValueError("KYBER_ALLOWED_ROUTERS must contain at least one audited router")
if FEE_CLAIM_HOURS <= 0:
    raise ValueError("FEE_CLAIM_HOURS must be greater than 0")
if MIN_STOCK_FEE_SELL_USD < 0:
    raise ValueError("MIN_STOCK_FEE_SELL_USD cannot be negative")
if MIN_SWAP_VALUE_ETH < 0:
    raise ValueError("MIN_SWAP_VALUE_ETH cannot be negative")
