from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import requests
from eth_abi import encode
from web3 import Web3

import abis
import config
from strategy_math import token0_value_in_quote


logger = logging.getLogger(__name__)
UINT128_MAX = 2**128 - 1
UINT160_MAX = 2**160 - 1
UINT256_MAX = 2**256 - 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
TRANSFER_TOPIC = (
    "0x"
    + Web3.keccak(text="Transfer(address,address,uint256)")
    .hex()
    .removeprefix("0x")
)

rpc_session = requests.Session()
rpc_session.headers.update({"Connection": "close"})
w3 = Web3(Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 30}, session=rpc_session))
account = w3.eth.account.from_key(config.PRIVATE_KEY)
if account.address.lower() != config.WALLET_ADDRESS.lower():
    raise ValueError("WALLET_ADDRESS does not match PRIVATE_KEY")

token0 = None if config.HAS_NATIVE0 else w3.eth.contract(config.TOKEN0_ADDRESS, abi=abis.ERC20_ABI)
token1 = None if config.HAS_NATIVE1 else w3.eth.contract(config.TOKEN1_ADDRESS, abi=abis.ERC20_ABI)
usdg = w3.eth.contract(config.USDG_ADDRESS, abi=abis.ERC20_ABI)
state_view = w3.eth.contract(config.STATE_VIEW, abi=abis.STATE_VIEW_ABI)
quoter = w3.eth.contract(config.V4_QUOTER, abi=abis.V4_QUOTER_ABI)
permit2 = w3.eth.contract(config.PERMIT2, abi=abis.PERMIT2_ABI)
position_manager = w3.eth.contract(config.POSITION_MANAGER, abi=abis.POSITION_MANAGER_ABI)
universal_router = w3.eth.contract(config.UNIVERSAL_ROUTER, abi=abis.UNIVERSAL_ROUTER_ABI)
v3_pool = w3.eth.contract(config.POOL_ID, abi=abis.V3_POOL_ABI) if config.POOL_PROTOCOL == "v3" else None
v3_quoter = w3.eth.contract(config.V3_QUOTER, abi=abis.V3_QUOTER_ABI)
v3_router = w3.eth.contract(config.V3_SWAP_ROUTER, abi=abis.V3_SWAP_ROUTER_ABI)
v3_position_manager = w3.eth.contract(
    config.V3_POSITION_MANAGER, abi=abis.V3_POSITION_MANAGER_ABI
)

POOL_KEY = (
    config.TOKEN0_ADDRESS,
    config.TOKEN1_ADDRESS,
    config.POOL_FEE,
    config.TICK_SPACING,
    config.HOOK_ADDRESS,
)


@dataclass
class PositionSnapshot:
    token_id: int
    tick_lower: int
    tick_upper: int
    liquidity: int
    amount0: float
    amount1: float
    value_quote: float


def _raw_signed_transaction(signed: Any) -> bytes:
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("Signed transaction object exposes no raw transaction bytes")
    return raw


def _base_tx() -> dict[str, Any]:
    block = w3.eth.get_block("pending")
    base_fee = int(block.get("baseFeePerGas", w3.eth.gas_price))
    priority = w3.to_wei(config.PRIORITY_FEE_GWEI, "gwei")
    return {
        "from": config.WALLET_ADDRESS,
        "nonce": w3.eth.get_transaction_count(config.WALLET_ADDRESS, "pending"),
        "chainId": config.CHAIN_ID,
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": int(base_fee * config.MAX_FEE_BASE_MULTIPLIER + priority),
    }


def send_function(fn: Any, *, value: int = 0) -> str:
    base = _base_tx()
    args = {"from": config.WALLET_ADDRESS, "value": value}
    try:
        estimate = fn.estimate_gas(args)
    except Exception as exc:
        raise RuntimeError(f"Gas estimate failed; refusing likely-reverting transaction: {exc}") from exc
    tx = fn.build_transaction({**base, "value": value, "gas": int(estimate * config.GAS_LIMIT_MULTIPLIER)})
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(_raw_signed_transaction(signed))
    tx_hex = "0x" + tx_hash.hex().removeprefix("0x")
    logger.info("TX sent: %s", tx_hex)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"TX reverted: {tx_hex}")
    logger.info("TX confirmed in block %s", receipt.blockNumber)
    return tx_hex


def verify_pool() -> None:
    if config.POOL_PROTOCOL == "v3":
        factory = v3_pool.functions.factory().call()
        address0 = v3_pool.functions.token0().call()
        address1 = v3_pool.functions.token1().call()
        fee = int(v3_pool.functions.fee().call())
        spacing = int(v3_pool.functions.tickSpacing().call())
        if factory.lower() != config.V3_FACTORY.lower():
            raise RuntimeError(f"V3 pool factory mismatch: {factory}")
        if address0.lower() != config.TOKEN0_ADDRESS.lower() or address1.lower() != config.TOKEN1_ADDRESS.lower():
            raise RuntimeError("V3 pool token addresses do not match the selected catalog entry")
        if fee != config.POOL_FEE or spacing != config.TICK_SPACING:
            raise RuntimeError("V3 pool fee/tick spacing does not match the selected catalog entry")
        if int(v3_pool.functions.slot0().call()[0]) == 0:
            raise RuntimeError("V3 pool is not initialized")
        return
    if int(config.TOKEN0_ADDRESS, 16) >= int(config.TOKEN1_ADDRESS, 16):
        raise RuntimeError("Pool currencies are not in canonical address order")
    encoded_key = encode(
        ["address", "address", "uint24", "int24", "address"],
        list(POOL_KEY),
    )
    computed = "0x" + Web3.keccak(encoded_key).hex().removeprefix("0x").lower()
    if computed != config.POOL_ID:
        raise RuntimeError(f"Pool key mismatch: computed {computed}, expected {config.POOL_ID}")
    sqrt_price, _, _, _ = state_view.functions.getSlot0(bytes.fromhex(config.POOL_ID[2:])).call()
    if int(sqrt_price) == 0:
        raise RuntimeError("Pool is not initialized")


def get_slot0() -> tuple[int, int, int]:
    if config.POOL_PROTOCOL == "v3":
        slot0 = v3_pool.functions.slot0().call()
        return int(slot0[0]), int(slot0[1]), int(config.POOL_FEE)
    sqrt_price, tick, _, lp_fee = state_view.functions.getSlot0(bytes.fromhex(config.POOL_ID[2:])).call()
    return int(sqrt_price), int(tick), int(lp_fee)


def get_token1_per_token0() -> float:
    sqrt_price, _, _ = get_slot0()
    return (sqrt_price / 2**96) ** 2 * 10 ** (config.TOKEN0_DECIMALS - config.TOKEN1_DECIMALS)


def get_spot_price() -> float:
    token1_per_token0 = get_token1_per_token0()
    return token1_per_token0 if config.BASE_INDEX == 0 else 1.0 / token1_per_token0


def display_range_for_ticks(tick_lower: int, tick_upper: int) -> tuple[float, float]:
    human_scale = 10 ** (config.TOKEN0_DECIMALS - config.TOKEN1_DECIMALS)
    token1_per_token0_low = (1.0001**tick_lower) * human_scale
    token1_per_token0_high = (1.0001**tick_upper) * human_scale
    if config.BASE_INDEX == 0:
        return token1_per_token0_low, token1_per_token0_high
    return 1 / token1_per_token0_high, 1 / token1_per_token0_low


def portfolio_value_quote(amount0: float, amount1: float) -> float:
    token1_per_token0 = get_token1_per_token0()
    if config.BASE_INDEX == 0:
        return amount0 * token1_per_token0 + amount1
    return amount0 + amount1 / token1_per_token0


def _raw_balance(token_address: str) -> int:
    if token_address.lower() == ZERO_ADDRESS:
        return int(w3.eth.get_balance(config.WALLET_ADDRESS))
    contract = _contract(token_address)
    if contract is None:
        raise RuntimeError("Missing ERC-20 contract for selected currency")
    return int(contract.functions.balanceOf(config.WALLET_ADDRESS).call())


def _contract(token_address: str) -> Any | None:
    if token_address.lower() == ZERO_ADDRESS:
        return None
    if token_address.lower() == config.TOKEN0_ADDRESS.lower():
        return token0
    if token_address.lower() == config.TOKEN1_ADDRESS.lower():
        return token1
    if token_address.lower() == config.USDG_ADDRESS.lower():
        return usdg
    return w3.eth.contract(Web3.to_checksum_address(token_address), abi=abis.ERC20_ABI)


def usable_native_eth() -> float:
    native_raw = int(w3.eth.get_balance(config.WALLET_ADDRESS))
    reserve_raw = int(config.NATIVE_GAS_RESERVE_ETH * 1e18)
    return max(0, native_raw - reserve_raw) / 1e18


def usdg_balance() -> float:
    return _raw_balance(config.USDG_ADDRESS) / 10**config.USDG_DECIMALS


def balances() -> tuple[float, float, float]:
    native_raw = int(w3.eth.get_balance(config.WALLET_ADDRESS))
    reserve_raw = int(config.NATIVE_GAS_RESERVE_ETH * 1e18)
    raw0 = max(0, native_raw - reserve_raw) if config.HAS_NATIVE0 else _raw_balance(config.TOKEN0_ADDRESS)
    raw1 = max(0, native_raw - reserve_raw) if config.HAS_NATIVE1 else _raw_balance(config.TOKEN1_ADDRESS)
    native = native_raw / 1e18
    return raw0 / 10**config.TOKEN0_DECIMALS, raw1 / 10**config.TOKEN1_DECIMALS, native


def ticks_for_range(spot: float) -> tuple[int, int, float, float]:
    low_price = spot * (1 - config.RANGE_DOWN_PERCENT / 100)
    high_price = spot * (1 + config.RANGE_UP_PERCENT / 100)
    if config.BASE_INDEX == 0:
        human_low_1_per_0, human_high_1_per_0 = low_price, high_price
    else:
        human_low_1_per_0, human_high_1_per_0 = 1 / high_price, 1 / low_price
    decimal_scale = 10 ** (config.TOKEN1_DECIMALS - config.TOKEN0_DECIMALS)
    raw_low_tick = math.log(human_low_1_per_0 * decimal_scale, 1.0001)
    raw_high_tick = math.log(human_high_1_per_0 * decimal_scale, 1.0001)
    spacing = config.TICK_SPACING
    tick_lower = math.floor(raw_low_tick / spacing) * spacing
    tick_upper = math.ceil(raw_high_tick / spacing) * spacing
    human_scale = 10 ** (config.TOKEN0_DECIMALS - config.TOKEN1_DECIMALS)
    actual_p10_low = (1.0001**tick_lower) * human_scale
    actual_p10_high = (1.0001**tick_upper) * human_scale
    if config.BASE_INDEX == 0:
        actual_low, actual_high = actual_p10_low, actual_p10_high
    else:
        actual_low, actual_high = 1 / actual_p10_high, 1 / actual_p10_low
    return tick_lower, tick_upper, actual_low, actual_high


def liquidity_for_amounts(amount0: float, amount1: float, tick_lower: int, tick_upper: int) -> int:
    sqrt_price_x96, _, _ = get_slot0()
    sqrt_price = sqrt_price_x96 / 2**96
    sqrt_lower = 1.0001 ** (tick_lower / 2)
    sqrt_upper = 1.0001 ** (tick_upper / 2)
    raw0 = amount0 * 10**config.TOKEN0_DECIMALS
    raw1 = amount1 * 10**config.TOKEN1_DECIMALS
    if not (sqrt_lower < sqrt_price < sqrt_upper):
        raise RuntimeError("Current price is outside the proposed range")
    liquidity0 = raw0 * sqrt_price * sqrt_upper / (sqrt_upper - sqrt_price)
    liquidity1 = raw1 / (sqrt_price - sqrt_lower)
    liquidity = int(min(liquidity0, liquidity1))
    if liquidity <= 0:
        raise RuntimeError("Selected amounts are too small to mint liquidity")
    return liquidity


def amounts_for_liquidity(liquidity: int, tick_lower: int, tick_upper: int) -> tuple[float, float]:
    sqrt_price_x96, _, _ = get_slot0()
    sqrt_price = sqrt_price_x96 / 2**96
    sqrt_lower = 1.0001 ** (tick_lower / 2)
    sqrt_upper = 1.0001 ** (tick_upper / 2)
    if sqrt_price <= sqrt_lower:
        raw0, raw1 = liquidity * (sqrt_upper - sqrt_lower) / (sqrt_lower * sqrt_upper), 0
    elif sqrt_price >= sqrt_upper:
        raw0, raw1 = 0, liquidity * (sqrt_upper - sqrt_lower)
    else:
        raw0 = liquidity * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
        raw1 = liquidity * (sqrt_price - sqrt_lower)
    return raw0 / 10**config.TOKEN0_DECIMALS, raw1 / 10**config.TOKEN1_DECIMALS


def _approve_erc20(contract: Any, spender: str, amount: int) -> None:
    if contract is None:
        return
    if contract.functions.allowance(config.WALLET_ADDRESS, spender).call() >= amount:
        return
    logger.info("Approving token %s for %s", contract.address, spender)
    send_function(contract.functions.approve(spender, UINT256_MAX))


def _approve_permit2(contract: Any, spender: str, amount: int) -> None:
    if contract is None:
        return
    _approve_erc20(contract, config.PERMIT2, amount)
    allowed, expiration, _ = permit2.functions.allowance(config.WALLET_ADDRESS, contract.address, spender).call()
    if int(allowed) >= amount and int(expiration) > int(time.time()) + 3600:
        return
    logger.info("Setting Permit2 allowance for %s to %s", contract.address, spender)
    send_function(permit2.functions.approve(contract.address, spender, UINT160_MAX, 2**48 - 1))


def _impact_percent(
    token_in: str,
    amount_in_raw: int,
    amount_out_raw: int,
    token1_per_token0: float,
    known_route_fee_percent: float = 0.0,
) -> float:
    input_decimals = config.TOKEN0_DECIMALS if token_in.lower() == config.TOKEN0_ADDRESS.lower() else config.TOKEN1_DECIMALS
    output_decimals = config.TOKEN1_DECIMALS if token_in.lower() == config.TOKEN0_ADDRESS.lower() else config.TOKEN0_DECIMALS
    amount_in = amount_in_raw / 10**input_decimals
    amount_out = amount_out_raw / 10**output_decimals
    expected = amount_in * token1_per_token0 if token_in.lower() == config.TOKEN0_ADDRESS.lower() else amount_in / token1_per_token0
    expected *= 1 - known_route_fee_percent / 100
    if expected <= 0:
        return 100.0
    return max(0.0, (expected - amount_out) / expected * 100)


def _uniswap_quote(token_in: str, amount_in_raw: int, token1_per_token0: float) -> dict[str, Any]:
    zero_for_one = token_in.lower() == config.TOKEN0_ADDRESS.lower()
    if config.POOL_PROTOCOL == "v3":
        result = v3_quoter.functions.quoteExactInputSingle(
            (token_in, config.TOKEN1_ADDRESS if zero_for_one else config.TOKEN0_ADDRESS, amount_in_raw, config.POOL_FEE, 0)
        ).call()
        amount_out = int(result[0])
        return {
            "provider": "uniswap",
            "amount_out": amount_out,
            "impact": _impact_percent(
                token_in,
                amount_in_raw,
                amount_out,
                token1_per_token0,
                known_route_fee_percent=config.POOL_FEE / 10_000,
            ),
            "zero_for_one": zero_for_one,
        }
    # Quoting is read-only. Omitting `from` avoids Monad applying native EOA
    # reserve checks to an eth_call that never spends wallet funds.
    result = quoter.functions.quoteExactInputSingle((POOL_KEY, zero_for_one, amount_in_raw, b"")).call()
    amount_out = int(result[0])
    return {
        "provider": "uniswap",
        "amount_out": amount_out,
        # Price impact excludes the known direct-pool LP fee. Slippage remains
        # independently capped when the transaction minimum is encoded.
        "impact": _impact_percent(
            token_in,
            amount_in_raw,
            amount_out,
            token1_per_token0,
            known_route_fee_percent=config.POOL_FEE / 10_000,
        ),
        "zero_for_one": zero_for_one,
    }


def _http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "robinhood-multi-pool-v4-bot/1.0",
    }
    if "kyberswap.com" in url:
        headers["x-client-id"] = config.KYBER_CLIENT_ID
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT_SECONDS) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("route API returned a non-object response")
    return result


def _find_int(data: Any, names: tuple[str, ...]) -> int:
    if isinstance(data, dict):
        for name in names:
            if name in data:
                try:
                    return int(str(data[name]))
                except (TypeError, ValueError):
                    try:
                        return int(float(data[name]))
                    except (TypeError, ValueError):
                        pass
        for value in data.values():
            found = _find_int(value, names)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = _find_int(value, names)
            if found:
                return found
    return 0


def _int_value(value: Any) -> int:
    if isinstance(value, str) and value.lower().startswith("0x"):
        return int(value, 16)
    return int(value or 0)


def _find_tx(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        if data.get("to") and (data.get("data") or data.get("calldata")):
            tx = dict(data)
            tx["data"] = tx.get("data") or tx.get("calldata")
            return tx
        for value in data.values():
            found = _find_tx(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_tx(value)
            if found:
                return found
    return None


def _kyber_address(token_address: str) -> str:
    return config.KYBER_NATIVE_ADDRESS if token_address.lower() == ZERO_ADDRESS else token_address


def _kyber_route(token_in: str, token_out: str, amount_in_raw: int, token1_per_token0: float) -> dict[str, Any]:
    api_token_in = _kyber_address(token_in)
    api_token_out = _kyber_address(token_out)
    query = {
        "tokenIn": api_token_in,
        "tokenOut": api_token_out,
        "amountIn": str(amount_in_raw),
        "origin": config.WALLET_ADDRESS,
    }
    quote = _http_json(config.KYBER_ROUTES_URL + "?" + urllib.parse.urlencode(query))
    summary = quote.get("data", {}).get("routeSummary")
    if not isinstance(summary, dict):
        raise RuntimeError("Kyber routes response has no routeSummary")
    if str(summary.get("tokenIn", "")).lower() != api_token_in.lower():
        raise RuntimeError("Kyber quote tokenIn does not match the requested token")
    if str(summary.get("tokenOut", "")).lower() != api_token_out.lower():
        raise RuntimeError("Kyber quote tokenOut does not match the requested token")
    if _find_int(summary, ("amountIn",)) != amount_in_raw:
        raise RuntimeError("Kyber quote amountIn does not match the requested amount")
    amount_out = _find_int(summary, ("amountOut",))
    if not amount_out:
        raise RuntimeError("Kyber quote returned amountOut=0")
    return {
        "provider": "kyberswap",
        "amount_out": amount_out,
        "impact": _impact_percent(token_in, amount_in_raw, amount_out, token1_per_token0),
        "amount_in_usd": float(summary.get("amountInUsd") or 0),
        "amount_out_usd": float(summary.get("amountOutUsd") or 0),
        "route_summary": summary,
        "router_address": quote.get("data", {}).get("routerAddress"),
    }


def _kyber_stock_to_eth_route(amount_in_raw: int) -> dict[str, Any]:
    token_in = config.STOCK_TOKEN_ADDRESS
    query = {
        "tokenIn": _kyber_address(token_in),
        "tokenOut": _kyber_address(ZERO_ADDRESS),
        "amountIn": str(amount_in_raw),
        "origin": config.WALLET_ADDRESS,
    }
    quote = _http_json(config.KYBER_ROUTES_URL + "?" + urllib.parse.urlencode(query))
    summary = quote.get("data", {}).get("routeSummary")
    if not isinstance(summary, dict):
        raise RuntimeError("Kyber stock-to-ETH response has no routeSummary")
    if str(summary.get("tokenIn", "")).lower() != token_in.lower():
        raise RuntimeError("Kyber stock-to-ETH quote has the wrong tokenIn")
    if str(summary.get("tokenOut", "")).lower() != config.KYBER_NATIVE_ADDRESS.lower():
        raise RuntimeError("Kyber stock-to-ETH quote has the wrong tokenOut")
    if _find_int(summary, ("amountIn",)) != amount_in_raw:
        raise RuntimeError("Kyber stock-to-ETH quote has the wrong amountIn")
    amount_out = _find_int(summary, ("amountOut",))
    amount_in_usd = float(summary.get("amountInUsd") or 0)
    amount_out_usd = float(summary.get("amountOutUsd") or 0)
    if not amount_out or amount_in_usd <= 0 or amount_out_usd <= 0:
        raise RuntimeError("Kyber stock-to-ETH quote is missing output or USD valuation")

    # Kyber's USD difference includes the source pool's known LP fee. Remove
    # that fee before enforcing the separate price-impact guard.
    known_fee_percent = config.POOL_FEE / 10_000
    expected_after_fee = amount_in_usd * (1 - known_fee_percent / 100)
    impact = max(0.0, (expected_after_fee - amount_out_usd) / expected_after_fee * 100)
    return {
        "provider": "kyberswap",
        "amount_out": amount_out,
        "amount_in_usd": amount_in_usd,
        "amount_out_usd": amount_out_usd,
        "impact": impact,
        "route_summary": summary,
        "router_address": quote.get("data", {}).get("routerAddress"),
    }


def _kyber_funding_route(token_in: str, token_out: str, amount_in_raw: int) -> dict[str, Any]:
    """Quote ETH/USDG startup funding using Kyber's USD valuations."""
    api_token_in = _kyber_address(token_in)
    api_token_out = _kyber_address(token_out)
    query = {
        "tokenIn": api_token_in,
        "tokenOut": api_token_out,
        "amountIn": str(amount_in_raw),
        "origin": config.WALLET_ADDRESS,
    }
    quote = _http_json(config.KYBER_ROUTES_URL + "?" + urllib.parse.urlencode(query))
    summary = quote.get("data", {}).get("routeSummary")
    if not isinstance(summary, dict):
        raise RuntimeError("Kyber funding response has no routeSummary")
    if str(summary.get("tokenIn", "")).lower() != api_token_in.lower():
        raise RuntimeError("Kyber funding quote has the wrong tokenIn")
    if str(summary.get("tokenOut", "")).lower() != api_token_out.lower():
        raise RuntimeError("Kyber funding quote has the wrong tokenOut")
    if _find_int(summary, ("amountIn",)) != amount_in_raw:
        raise RuntimeError("Kyber funding quote has the wrong amountIn")
    amount_out = _find_int(summary, ("amountOut",))
    amount_in_usd = float(summary.get("amountInUsd") or 0)
    amount_out_usd = float(summary.get("amountOutUsd") or 0)
    if not amount_out or amount_in_usd <= 0 or amount_out_usd <= 0:
        raise RuntimeError("Kyber funding quote is missing output or USD valuation")
    impact = max(0.0, (amount_in_usd - amount_out_usd) / amount_in_usd * 100)
    return {
        "provider": "kyberswap",
        "amount_out": amount_out,
        "amount_in_usd": amount_in_usd,
        "amount_out_usd": amount_out_usd,
        "impact": impact,
        "route_summary": summary,
        "router_address": quote.get("data", {}).get("routerAddress"),
    }


def safe_routes(token_in: str, token_out: str, amount_in_raw: int, token1_per_token0: float) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    quote_functions = {
        "uniswap": lambda: _uniswap_quote(token_in, amount_in_raw, token1_per_token0),
        "kyberswap": lambda: _kyber_route(token_in, token_out, amount_in_raw, token1_per_token0),
    }
    for name in config.SWAP_ROUTE_PROVIDERS:
        try:
            if name not in quote_functions:
                raise RuntimeError("unknown route provider")
            route = quote_functions[name]()
            if route["impact"] > config.MAX_PRICE_IMPACT_PERCENT:
                raise RuntimeError(f"impact {route['impact']:.4f}% exceeds {config.MAX_PRICE_IMPACT_PERCENT:.4f}%")
            candidates.append(route)
            logger.info("Route candidate %s: out=%s, impact=%.4f%%", name, route["amount_out"], route["impact"])
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.info("Route %s unavailable: %s", name, exc)
    candidates.sort(key=lambda item: item["amount_out"], reverse=True)
    return candidates, errors


def _execute_uniswap(route: dict[str, Any], token_in: str, token_out: str, amount_in_raw: int) -> None:
    contract_in = _contract(token_in)
    if config.POOL_PROTOCOL == "v3":
        _approve_erc20(contract_in, config.V3_SWAP_ROUTER, amount_in_raw)
        minimum = int(route["amount_out"] * (1 - config.SLIPPAGE_PERCENT / 100))
        params = (
            token_in,
            token_out,
            config.POOL_FEE,
            config.WALLET_ADDRESS,
            amount_in_raw,
            minimum,
            0,
        )
        send_function(v3_router.functions.exactInputSingle(params))
        return
    _approve_permit2(contract_in, config.UNIVERSAL_ROUTER, amount_in_raw)
    minimum = int(route["amount_out"] * (1 - config.SLIPPAGE_PERCENT / 100))
    swap_param = encode(
        ["((address,address,uint24,int24,address),bool,uint128,uint128,uint256,bytes)"],
        [(POOL_KEY, route["zero_for_one"], amount_in_raw, minimum, 0, b"")],
    )
    settle_param = encode(["address", "uint256"], [token_in, amount_in_raw])
    take_param = encode(["address", "uint256"], [token_out, minimum])
    actions = bytes([0x06, 0x0C, 0x0F])
    command_input = encode(["bytes", "bytes[]"], [actions, [swap_param, settle_param, take_param]])
    native_value = amount_in_raw if token_in.lower() == ZERO_ADDRESS else 0
    send_function(
        universal_router.functions.execute(bytes([0x10]), [command_input], int(time.time()) + config.TX_DEADLINE_SECONDS),
        value=native_value,
    )


def _execute_api_transaction(route: dict[str, Any], token_in: str, amount_in_raw: int) -> Any:
    tx = route["tx"]
    target = Web3.to_checksum_address(tx["to"])
    spender = Web3.to_checksum_address(route.get("spender") or target)
    contract_in = _contract(token_in)
    _approve_erc20(contract_in, spender, amount_in_raw)
    raw_tx = {
        **_base_tx(),
        "to": target,
        "data": tx["data"],
        "value": _int_value(tx.get("value", 0)),
    }
    try:
        raw_tx["gas"] = int(w3.eth.estimate_gas({**raw_tx, "from": config.WALLET_ADDRESS}) * config.GAS_LIMIT_MULTIPLIER)
    except Exception as exc:
        raise RuntimeError(f"{route['provider']} transaction estimate failed: {exc}") from exc
    signed = account.sign_transaction(raw_tx)
    tx_hash = w3.eth.send_raw_transaction(_raw_signed_transaction(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"{route['provider']} transaction reverted: {tx_hash.hex()}")
    return receipt


def _execute_kyber(route: dict[str, Any], token_in: str, amount_in_raw: int) -> Any:
    payload = _http_json(
        config.KYBER_BUILD_URL,
        method="POST",
        payload={
            "routeSummary": route["route_summary"],
            "sender": config.WALLET_ADDRESS,
            "recipient": config.WALLET_ADDRESS,
            "slippageTolerance": max(1, int(round(config.SLIPPAGE_PERCENT * 100))),
            "deadline": int(time.time()) + config.TX_DEADLINE_SECONDS,
            "source": config.KYBER_CLIENT_ID,
            "enableGasEstimation": False,
        },
    )
    built = payload.get("data", {})
    target = built.get("routerAddress")
    data = built.get("data")
    if not target or not data:
        raise RuntimeError("Kyber build response has no routerAddress/calldata")
    expected_router = route.get("router_address")
    if expected_router and target.lower() != str(expected_router).lower():
        raise RuntimeError("Kyber build router differs from the quoted router")
    built_amount_in = _find_int(built, ("amountIn",))
    if built_amount_in and built_amount_in != amount_in_raw:
        raise RuntimeError("Kyber build amountIn differs from the quoted amount")
    transaction_value = _int_value(built.get("transactionValue", 0))
    if token_in.lower() == ZERO_ADDRESS:
        if transaction_value != amount_in_raw:
            raise RuntimeError("Kyber native transaction value does not equal amountIn")
    elif transaction_value != 0:
        raise RuntimeError("Kyber ERC-20 swap unexpectedly requests native ETH value")
    route = {
        **route,
        "tx": {"to": target, "data": data, "value": transaction_value},
        "spender": target,
    }
    return _execute_api_transaction(route, token_in, amount_in_raw)


def convert_startup_funding(token_in: str, token_out: str, amount_in: float) -> float:
    """Convert explicitly selected ETH/USDG startup funds through a safe route."""
    if amount_in <= 0:
        return 0.0
    input_decimals = 18 if token_in.lower() == ZERO_ADDRESS else config.USDG_DECIMALS
    output_decimals = 18 if token_out.lower() == ZERO_ADDRESS else config.USDG_DECIMALS
    amount_in_raw = int(amount_in * 10**input_decimals)
    if token_in.lower() == ZERO_ADDRESS:
        usable_raw = int(usable_native_eth() * 1e18)
        if amount_in_raw > usable_raw:
            raise RuntimeError(
                f"ETH funding exceeds usable balance after reserving {config.NATIVE_GAS_RESERVE_ETH:.4f} ETH for gas"
            )
    else:
        amount_in_raw = min(amount_in_raw, _raw_balance(token_in))
    if amount_in_raw <= 0:
        raise RuntimeError("Selected startup funding amount is zero")

    while True:
        try:
            route = _kyber_funding_route(token_in, token_out, amount_in_raw)
            if route["impact"] > config.MAX_PRICE_IMPACT_PERCENT:
                raise RuntimeError(
                    f"funding route impact {route['impact']:.4f}% exceeds "
                    f"{config.MAX_PRICE_IMPACT_PERCENT:.4f}%"
                )
            logger.info(
                "Safe startup funding route: KyberSwap | $%.4f -> $%.4f | impact=%.4f%%",
                route["amount_in_usd"],
                route["amount_out_usd"],
                route["impact"],
            )
            before = _raw_balance(token_out)
            receipt = _execute_kyber(route, token_in, amount_in_raw)
            after = _raw_balance(token_out)
            received_raw = max(0, after - before)
            if token_out.lower() == ZERO_ADDRESS:
                gas_price = int(getattr(receipt, "effectiveGasPrice", 0) or receipt.get("effectiveGasPrice", 0))
                gas_used = int(getattr(receipt, "gasUsed", 0) or receipt.get("gasUsed", 0))
                received_raw += gas_price * gas_used
            received = received_raw / 10**output_decimals
            if received <= 0:
                # Native-output accounting can vary by RPC. The validated quote
                # remains an upper bound; live balances clamp the later deposit.
                received = route["amount_out"] / 10**output_decimals
            return received
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning(
                "Startup funding swap not safe/ready: %s. Retrying in %ss.",
                exc,
                config.SWAP_RETRY_SECONDS,
            )
            time.sleep(config.SWAP_RETRY_SECONDS)


def execute_swap(token_in: str, token_out: str, amount_in: float) -> tuple[str, float, float]:
    input_decimals = config.TOKEN0_DECIMALS if token_in.lower() == config.TOKEN0_ADDRESS.lower() else config.TOKEN1_DECIMALS
    output_decimals = config.TOKEN0_DECIMALS if token_out.lower() == config.TOKEN0_ADDRESS.lower() else config.TOKEN1_DECIMALS
    amount_in_raw = int(amount_in * 10**input_decimals)
    while True:
        try:
            token1_per_token0 = get_token1_per_token0()
            routes, quote_errors = safe_routes(token_in, token_out, amount_in_raw, token1_per_token0)
            execution_errors: list[str] = []
            for route in routes:
                try:
                    logger.info("Executing best remaining route: %s", route["provider"])
                    before = _raw_balance(token_out)
                    if route["provider"] == "uniswap":
                        _execute_uniswap(route, token_in, token_out, amount_in_raw)
                    elif route["provider"] == "kyberswap":
                        _execute_kyber(route, token_in, amount_in_raw)
                    else:
                        _execute_api_transaction(route, token_in, amount_in_raw)
                    after = _raw_balance(token_out)
                    received = max(0, after - before) / 10**output_decimals
                    if received <= 0:
                        raise RuntimeError("confirmed swap produced no output-token balance increase")
                    return route["provider"], amount_in, received
                except Exception as exc:
                    execution_errors.append(f"{route['provider']}: {exc}")
                    logger.warning("Route %s failed; trying next safe route: %s", route["provider"], exc)
            raise RuntimeError(" | ".join(quote_errors + execution_errors) or "no safe routes")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning("Swap not safe/ready: %s. Retrying in %ss.", exc, config.SWAP_RETRY_SECONDS)
            time.sleep(config.SWAP_RETRY_SECONDS)


def sell_stock_fees_to_eth(amount: float) -> tuple[float, float, float]:
    """Sell only the tracked claimed stock fees; never sweep wallet principal."""
    requested_raw = int(amount * 10**config.STOCK_TOKEN_DECIMALS)
    available_raw = _raw_balance(config.STOCK_TOKEN_ADDRESS)
    amount_in_raw = min(requested_raw, available_raw)
    if amount_in_raw <= 0:
        raise RuntimeError(f"No claimed {config.STOCK_TOKEN_SYMBOL} fees are available to sell")

    route = _kyber_stock_to_eth_route(amount_in_raw)
    if route["amount_in_usd"] < config.MIN_STOCK_FEE_SELL_USD:
        raise RuntimeError(
            f"claimed {config.STOCK_TOKEN_SYMBOL} is worth ${route['amount_in_usd']:.4f}, "
            f"below the ${config.MIN_STOCK_FEE_SELL_USD:.2f} sale minimum"
        )
    if route["impact"] > config.MAX_PRICE_IMPACT_PERCENT:
        raise RuntimeError(
            f"stock-fee sale impact {route['impact']:.4f}% exceeds "
            f"{config.MAX_PRICE_IMPACT_PERCENT:.4f}%"
        )
    logger.info(
        "Selling claimed %s fees through KyberSwap: $%.4f, impact=%.4f%%",
        config.STOCK_TOKEN_SYMBOL,
        route["amount_in_usd"],
        route["impact"],
    )
    _execute_kyber(route, config.STOCK_TOKEN_ADDRESS, amount_in_raw)
    sold = amount_in_raw / 10**config.STOCK_TOKEN_DECIMALS
    received_eth = route["amount_out"] / 1e18
    return sold, received_eth, route["amount_in_usd"]


def _range_target_amounts(amount0: float, amount1: float, display_spot: float) -> tuple[float, float]:
    tick_lower, tick_upper, _, _ = ticks_for_range(display_spot)
    sqrt_price_x96, _, _ = get_slot0()
    sqrt_price = sqrt_price_x96 / 2**96
    sqrt_lower = 1.0001 ** (tick_lower / 2)
    sqrt_upper = 1.0001 ** (tick_upper / 2)
    amount0_per_liquidity = (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper) / 10**config.TOKEN0_DECIMALS
    amount1_per_liquidity = (sqrt_price - sqrt_lower) / 10**config.TOKEN1_DECIMALS
    token1_per_token0 = get_token1_per_token0()
    total_value_token1 = amount0 * token1_per_token0 + amount1
    value0_weight = amount0_per_liquidity * token1_per_token0
    value1_weight = amount1_per_liquidity
    target_value0 = total_value_token1 * value0_weight / (value0_weight + value1_weight)
    target0 = target_value0 / token1_per_token0
    target1 = total_value_token1 - target_value0
    return target0, target1


def balance_selected_to_50_50(amount0: float, amount1: float) -> tuple[float, float]:
    display_spot = get_spot_price()
    token1_per_token0 = get_token1_per_token0()
    target0, target1 = _range_target_amounts(amount0, amount1, display_spot)
    difference = amount0 - target0
    # `difference` is denominated in token0. Native ETH is currency0 by
    # canonical address ordering, so it is also the ETH-equivalent imbalance
    # for stock/ETH pools. USDG pools keep the quote-token threshold.
    swap_value_quote = token0_value_in_quote(abs(difference), token1_per_token0, config.BASE_INDEX)
    logger.info(
        "Balanced-range target | selected=%.8f %s + %.8f %s | target=%.8f %s + %.8f %s",
        amount0, config.TOKEN0_SYMBOL, amount1, config.TOKEN1_SYMBOL,
        target0, config.TOKEN0_SYMBOL, target1, config.TOKEN1_SYMBOL,
    )
    if config.HAS_NATIVE0:
        below_swap_minimum = abs(difference) < config.MIN_SWAP_VALUE_ETH
        minimum_value = config.MIN_SWAP_VALUE_ETH
        minimum_symbol = "ETH equivalent"
        imbalance_value = abs(difference)
    else:
        below_swap_minimum = swap_value_quote < config.MIN_SWAP_VALUE_QUOTE
        minimum_value = config.MIN_SWAP_VALUE_QUOTE
        minimum_symbol = config.QUOTE_SYMBOL
        imbalance_value = swap_value_quote
    if below_swap_minimum:
        logger.info(
            "Range-ratio imbalance %.8f %s is below the %.8f swap minimum; minting current balances.",
            imbalance_value,
            minimum_symbol,
            minimum_value,
        )
        return amount0, amount1
    if difference > 0:
        provider, sold, bought = execute_swap(config.TOKEN0_ADDRESS, config.TOKEN1_ADDRESS, difference)
        logger.info("Swap via %s: %.8f %s -> %.8f %s", provider, sold, config.TOKEN0_SYMBOL, bought, config.TOKEN1_SYMBOL)
        return max(0, amount0 - sold), amount1 + bought
    else:
        amount_token1 = abs(difference) * token1_per_token0
        provider, sold, bought = execute_swap(config.TOKEN1_ADDRESS, config.TOKEN0_ADDRESS, amount_token1)
        logger.info("Swap via %s: %.8f %s -> %.8f %s", provider, sold, config.TOKEN1_SYMBOL, bought, config.TOKEN0_SYMBOL)
        return amount0 + bought, max(0, amount1 - sold)


def mint_position(amount0: float, amount1: float) -> tuple[int, float, float]:
    spot = get_spot_price()
    tick_lower, tick_upper, actual_low, actual_high = ticks_for_range(spot)
    liquidity = liquidity_for_amounts(amount0, amount1, tick_lower, tick_upper)
    liquidity = int(liquidity * (1 - config.LP_MINT_TOLERANCE_PERCENT / 100))
    raw0 = int(amount0 * 10**config.TOKEN0_DECIMALS)
    raw1 = int(amount1 * 10**config.TOKEN1_DECIMALS)
    if config.POOL_PROTOCOL == "v3":
        _approve_erc20(token0, config.V3_POSITION_MANAGER, raw0)
        _approve_erc20(token1, config.V3_POSITION_MANAGER, raw1)
        expected0, expected1 = amounts_for_liquidity(liquidity, tick_lower, tick_upper)
        tolerance = 1 - config.LP_MINT_TOLERANCE_PERCENT / 100
        min0 = int(expected0 * tolerance * 10**config.TOKEN0_DECIMALS)
        min1 = int(expected1 * tolerance * 10**config.TOKEN1_DECIMALS)
        params = (
            config.TOKEN0_ADDRESS,
            config.TOKEN1_ADDRESS,
            config.POOL_FEE,
            tick_lower,
            tick_upper,
            raw0,
            raw1,
            min0,
            min1,
            config.WALLET_ADDRESS,
            int(time.time()) + config.TX_DEADLINE_SECONDS,
        )
        tx_hash = send_function(v3_position_manager.functions.mint(params))
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        token_id = _minted_token_id(receipt, config.V3_POSITION_MANAGER)
        if not token_id:
            raise RuntimeError("Mint confirmed, but the new V3 position token ID was not found")
        logger.info(
            "V3 position minted: token_id=%s, range=[%.8f, %.8f] %s per %s",
            token_id,
            actual_low,
            actual_high,
            config.QUOTE_SYMBOL,
            config.BASE_SYMBOL,
        )
        return token_id, actual_low, actual_high
    _approve_permit2(token0, config.POSITION_MANAGER, raw0)
    _approve_permit2(token1, config.POSITION_MANAGER, raw1)
    mint_params = encode(
        [
            "(address,address,uint24,int24,address)",
            "int24",
            "int24",
            "uint256",
            "uint128",
            "uint128",
            "address",
            "bytes",
        ],
        [POOL_KEY, tick_lower, tick_upper, liquidity, raw0, raw1, config.WALLET_ADDRESS, b""],
    )
    settle_params = encode(["address", "address"], [config.TOKEN0_ADDRESS, config.TOKEN1_ADDRESS])
    actions = bytes([0x02, 0x0D])
    action_params = [mint_params, settle_params]
    if config.HAS_NATIVE0 or config.HAS_NATIVE1:
        # SETTLE_PAIR pays only the ETH actually used by the position. Return
        # the unused msg.value so a later PositionManager call cannot spend it.
        actions += bytes([0x14])
        action_params.append(encode(["address", "address"], [ZERO_ADDRESS, config.WALLET_ADDRESS]))
    unlock_data = encode(["bytes", "bytes[]"], [actions, action_params])
    native_value = raw0 if config.HAS_NATIVE0 else raw1 if config.HAS_NATIVE1 else 0
    tx_hash = send_function(
        position_manager.functions.modifyLiquidities(unlock_data, int(time.time()) + config.TX_DEADLINE_SECONDS),
        value=native_value,
    )
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    token_id = _minted_token_id(receipt, config.POSITION_MANAGER)
    if not token_id:
        raise RuntimeError("Mint confirmed, but the new V4 position token ID was not found")
    logger.info("Position minted: token_id=%s, range=[%.8f, %.8f] %s per %s", token_id, actual_low, actual_high, config.TOKEN0_SYMBOL, config.TOKEN1_SYMBOL)
    return token_id, actual_low, actual_high


def _minted_token_id(receipt: Any, manager_address: str) -> int:
    for log in receipt.logs:
        if log.address.lower() != manager_address.lower() or not log.topics:
            continue
        if (
            log.topics[0].hex().removeprefix("0x").lower()
            != TRANSFER_TOPIC.removeprefix("0x").lower()
            or len(log.topics) < 4
        ):
            continue
        from_address = int(log.topics[1].hex(), 16)
        to_address = int(log.topics[2].hex(), 16) & ((1 << 160) - 1)
        if from_address == 0 and to_address == int(config.WALLET_ADDRESS, 16):
            return int(log.topics[3].hex(), 16)
    return 0


def burn_position(token_id: int) -> tuple[float, float]:
    before0, before1, _ = balances()
    if config.POOL_PROTOCOL == "v3":
        position = v3_position_manager.functions.positions(token_id).call()
        liquidity = int(position[7])
        if liquidity:
            send_function(
                v3_position_manager.functions.decreaseLiquidity(
                    (
                        token_id,
                        liquidity,
                        0,
                        0,
                        int(time.time()) + config.TX_DEADLINE_SECONDS,
                    )
                )
            )
        send_function(
            v3_position_manager.functions.collect(
                (token_id, config.WALLET_ADDRESS, UINT128_MAX, UINT128_MAX)
            )
        )
        after0, after1, _ = balances()
        return max(0, after0 - before0), max(0, after1 - before1)
    burn_params = encode(["uint256", "uint128", "uint128", "bytes"], [token_id, 0, 0, b""])
    take_params = encode(
        ["address", "address", "address"],
        [config.TOKEN0_ADDRESS, config.TOKEN1_ADDRESS, config.WALLET_ADDRESS],
    )
    unlock_data = encode(["bytes", "bytes[]"], [bytes([0x03, 0x11]), [burn_params, take_params]])
    send_function(position_manager.functions.modifyLiquidities(unlock_data, int(time.time()) + config.TX_DEADLINE_SECONDS))
    after0, after1, _ = balances()
    return max(0, after0 - before0), max(0, after1 - before1)


def collect_fees(token_id: int) -> tuple[float, float]:
    """Collect fees without removing liquidity from the position."""
    before0, before1, _ = balances()
    if config.POOL_PROTOCOL == "v3":
        # V3 accounts fresh fees when liquidity is modified. Removing one
        # liquidity unit is economically negligible and updates tokensOwed
        # before collect; a plain collect can otherwise report stale zeroes.
        position = v3_position_manager.functions.positions(token_id).call()
        if int(position[7]) > 1:
            send_function(
                v3_position_manager.functions.decreaseLiquidity(
                    (
                        token_id,
                        1,
                        0,
                        0,
                        int(time.time()) + config.TX_DEADLINE_SECONDS,
                    )
                )
            )
        send_function(
            v3_position_manager.functions.collect(
                (token_id, config.WALLET_ADDRESS, UINT128_MAX, UINT128_MAX)
            )
        )
        after0, after1, _ = balances()
        fee0 = max(0, after0 - before0)
        fee1 = max(0, after1 - before1)
        logger.info(
            "V3 fees claimed: %.8f %s + %.8f %s",
            fee0,
            config.TOKEN0_SYMBOL,
            fee1,
            config.TOKEN1_SYMBOL,
        )
        return fee0, fee1
    decrease_params = encode(
        ["uint256", "uint128", "uint128", "uint128", "bytes"],
        [token_id, 0, 0, 0, b""],
    )
    take_params = encode(
        ["address", "address", "address"],
        [config.TOKEN0_ADDRESS, config.TOKEN1_ADDRESS, config.WALLET_ADDRESS],
    )
    unlock_data = encode(["bytes", "bytes[]"], [bytes([0x01, 0x11]), [decrease_params, take_params]])
    send_function(
        position_manager.functions.modifyLiquidities(
            unlock_data,
            int(time.time()) + config.TX_DEADLINE_SECONDS,
        )
    )
    after0, after1, _ = balances()
    fee0 = max(0, after0 - before0)
    fee1 = max(0, after1 - before1)
    logger.info(
        "Fees claimed without removing liquidity: %.8f %s + %.8f %s",
        fee0,
        config.TOKEN0_SYMBOL,
        fee1,
        config.TOKEN1_SYMBOL,
    )
    return fee0, fee1


def read_position(token_id: int) -> PositionSnapshot:
    if config.POOL_PROTOCOL == "v3":
        owner = v3_position_manager.functions.ownerOf(token_id).call()
        if owner.lower() != config.WALLET_ADDRESS.lower():
            raise RuntimeError(f"Position {token_id} is owned by {owner}")
        position = v3_position_manager.functions.positions(token_id).call()
        if (
            position[2].lower() != config.TOKEN0_ADDRESS.lower()
            or position[3].lower() != config.TOKEN1_ADDRESS.lower()
            or int(position[4]) != config.POOL_FEE
        ):
            raise RuntimeError(f"V3 position {token_id} belongs to a different pool")
        tick_lower = int(position[5])
        tick_upper = int(position[6])
        liquidity = int(position[7])
        amount0, amount1 = amounts_for_liquidity(liquidity, tick_lower, tick_upper)
        value_quote = portfolio_value_quote(amount0, amount1)
        return PositionSnapshot(
            token_id,
            tick_lower,
            tick_upper,
            liquidity,
            amount0,
            amount1,
            value_quote,
        )
    owner = position_manager.functions.ownerOf(token_id).call()
    if owner.lower() != config.WALLET_ADDRESS.lower():
        raise RuntimeError(f"Position {token_id} is owned by {owner}")
    _, packed = position_manager.functions.getPoolAndPositionInfo(token_id).call()
    tick_lower = ((int(packed) >> 8) & 0xFFFFFF)
    tick_upper = ((int(packed) >> 32) & 0xFFFFFF)
    tick_lower = tick_lower - (1 << 24) if tick_lower & (1 << 23) else tick_lower
    tick_upper = tick_upper - (1 << 24) if tick_upper & (1 << 23) else tick_upper
    liquidity = int(position_manager.functions.getPositionLiquidity(token_id).call())
    amount0, amount1 = amounts_for_liquidity(liquidity, tick_lower, tick_upper)
    spot = get_spot_price()
    token1_per_token0 = get_token1_per_token0()
    value_quote = portfolio_value_quote(amount0, amount1)
    return PositionSnapshot(token_id, tick_lower, tick_upper, liquidity, amount0, amount1, value_quote)


def find_existing_positions() -> list[PositionSnapshot]:
    """Find wallet-owned LP NFTs for the currently selected pool."""
    manager = (
        config.V3_POSITION_MANAGER
        if config.POOL_PROTOCOL == "v3"
        else config.POSITION_MANAGER
    )
    latest = int(w3.eth.block_number)
    scan_blocks = max(1, int(config.POSITION_SCAN_BLOCKS))
    chunk_size = max(1, int(config.POSITION_SCAN_CHUNK_BLOCKS))
    start = max(0, latest - scan_blocks)
    wallet_topic = "0x" + ("0" * 24) + config.WALLET_ADDRESS[2:].lower()
    token_ids: set[int] = set()
    logger.info(
        "Scanning blocks %s..%s for existing %s LP NFTs.",
        start,
        latest,
        config.POOL_PROTOCOL.upper(),
    )
    for chunk_start in range(start, latest + 1, chunk_size):
        chunk_end = min(latest, chunk_start + chunk_size - 1)
        logs = _get_logs_with_range_splitting(
            {
                "address": manager,
                "fromBlock": chunk_start,
                "toBlock": chunk_end,
                "topics": [TRANSFER_TOPIC, None, wallet_topic],
            }
        )
        for event in logs:
            if len(event["topics"]) >= 4:
                token_ids.add(int(event["topics"][3].hex(), 16))

    positions: list[PositionSnapshot] = []
    for token_id in sorted(token_ids, reverse=True):
        try:
            position = read_position(token_id)
        except Exception:
            continue
        if position.liquidity > 0:
            positions.append(position)
    logger.info(
        "Existing-position scan found %s active NFT(s) for %s.",
        len(positions),
        config.POOL_LABEL,
    )
    return positions


def _get_logs_with_range_splitting(filter_params: dict[str, Any]) -> list[Any]:
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
