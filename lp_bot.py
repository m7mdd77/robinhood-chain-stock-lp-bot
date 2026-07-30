from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import blockchain as bc
import config
import notifications


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("lp_bot")


@dataclass
class BotState:
    token_id: int = 0
    range_low: float = 0.0
    range_high: float = 0.0
    out_anchor_price: float = 0.0
    out_direction: str = ""
    starting_value: float = 0.0
    rebalances: int = 0
    opened_iso: str = ""
    last_fee_claim_iso: str = ""
    pending_stock_fees: float = 0.0
    claimed_stock_fees: float = 0.0
    sold_stock_fees: float = 0.0
    eth_from_stock_fees: float = 0.0


def load_state() -> BotState:
    if not config.STATE_FILE.exists():
        return BotState()
    try:
        return BotState(**json.loads(config.STATE_FILE.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("Ignoring invalid state file: %s", exc)
        return BotState()


state = load_state()


def save_state() -> None:
    config.STATE_FILE.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def ask_amount(label: str, maximum: float) -> float:
    while True:
        raw = input(f"{label} to use (max {maximum:.6f}): ").strip()
        try:
            value = float(raw)
            if 0 <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"Enter a number from 0 to {maximum:.6f}.")


def open_position(context: str, amount0: float, amount1: float) -> None:
    global state
    attempt = 0
    while True:
        attempt += 1
        try:
            live0, live1, _ = bc.balances()
            selected0 = min(amount0, live0)
            selected1 = min(amount1, live1)
            final0, final1 = bc.balance_selected_to_50_50(selected0, selected1)
            token_id, low, high = bc.mint_position(final0, final1)
            spot = bc.get_spot_price()
            if not state.starting_value:
                state.starting_value = bc.portfolio_value_quote(selected0, selected1)
            state.token_id = token_id
            state.range_low = low
            state.range_high = high
            state.out_anchor_price = 0
            state.out_direction = ""
            state.opened_iso = datetime.now(timezone.utc).isoformat()
            save_state()
            notifications.send(
                f"Robinhood LP opened\nPool: {config.POOL_LABEL}\n"
                f"Token ID: {token_id}\nRange: {low:.8f} - {high:.8f}\nSpot: {spot:.8f}"
            )
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.error("[%s] Open attempt %s failed: %s. Retrying in 15s.", context, attempt, exc)
            notifications.send(f"Robinhood LP {context} deposit retry\n{exc}")
            time.sleep(config.SWAP_RETRY_SECONDS)
            amount0, amount1, _ = bc.balances()


def initial_deposit() -> None:
    balance0, balance1, native = bc.balances()
    usable_eth = bc.usable_native_eth()
    wallet_usdg = bc.usdg_balance()
    spot = bc.get_spot_price()
    print(f"\n=== {config.POOL_LABEL} deposit setup ===")
    print(f"Live price    : {spot:.8f} {config.QUOTE_SYMBOL} per {config.BASE_SYMBOL}")
    print(f"Wallet balance: {balance0:.8f} {config.TOKEN0_SYMBOL}, {balance1:.8f} {config.TOKEN1_SYMBOL}, {native:.6f} ETH")
    print(f"Gas reserve   : {config.NATIVE_GAS_RESERVE_ETH:.6f} ETH (never swapped or deposited)")
    amount0 = ask_amount(config.TOKEN0_SYMBOL, balance0)
    amount1 = ask_amount(config.TOKEN1_SYMBOL, balance1)

    if config.HAS_NATIVE0 or config.HAS_NATIVE1:
        extra_usdg = ask_amount("extra USDG to convert into pool ETH", wallet_usdg)
        if extra_usdg > 0:
            received_eth = bc.convert_startup_funding(
                config.USDG_ADDRESS,
                bc.ZERO_ADDRESS,
                extra_usdg,
            )
            if config.HAS_NATIVE0:
                amount0 += received_eth
            else:
                amount1 += received_eth
            logger.info("Startup funding converted: %.6f USDG -> %.8f ETH", extra_usdg, received_eth)
    elif config.TOKEN0_ADDRESS.lower() == config.USDG_ADDRESS.lower() or config.TOKEN1_ADDRESS.lower() == config.USDG_ADDRESS.lower():
        extra_eth = ask_amount("extra ETH to convert into pool USDG", usable_eth)
        if extra_eth > 0:
            received_usdg = bc.convert_startup_funding(
                bc.ZERO_ADDRESS,
                config.USDG_ADDRESS,
                extra_eth,
            )
            if config.TOKEN0_ADDRESS.lower() == config.USDG_ADDRESS.lower():
                amount0 += received_usdg
            else:
                amount1 += received_usdg
            logger.info("Startup funding converted: %.8f ETH -> %.6f USDG", extra_eth, received_usdg)

    if bc.portfolio_value_quote(amount0, amount1) <= 0:
        raise RuntimeError("Deposit amount cannot be zero")
    open_position("initial", amount0, amount1)


def resume_discovered_position() -> bool:
    if not config.SCAN_EXISTING_POSITIONS:
        return False
    positions = _read_with_retry(
        "existing LP position scan",
        bc.find_existing_positions,
        attempts=3,
        delay_seconds=5,
    )
    if not positions:
        return False
    position = positions[0]
    low, high = bc.display_range_for_ticks(
        position.tick_lower,
        position.tick_upper,
    )
    if len(positions) > 1:
        logger.warning(
            "Found %s active positions for the selected pool; resuming newest "
            "token_id=%s. Other positions are left untouched.",
            len(positions),
            position.token_id,
        )
    state.token_id = position.token_id
    state.range_low = low
    state.range_high = high
    state.out_anchor_price = 0
    state.out_direction = ""
    if not state.starting_value:
        state.starting_value = position.value_quote
    if not state.opened_iso:
        state.opened_iso = datetime.now(timezone.utc).isoformat()
    save_state()
    logger.info(
        "Resuming discovered position token_id=%s, range=[%.8f, %.8f].",
        position.token_id,
        low,
        high,
    )
    notifications.send(
        "Robinhood LP existing position resumed\n"
        f"Pool: {config.POOL_LABEL}\n"
        f"Token ID: {position.token_id}\n"
        f"Range: {low:.8f} - {high:.8f}"
    )
    return True


def _fee_claim_due() -> bool:
    reference = state.last_fee_claim_iso or state.opened_iso
    if not reference:
        return False
    try:
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(reference)
    except (ValueError, TypeError):
        return True
    return elapsed.total_seconds() >= config.FEE_CLAIM_HOURS * 3600


def claim_fees_and_convert(context: str) -> None:
    fee0, fee1 = bc.collect_fees(state.token_id)
    stock_fee = fee0 if config.STOCK_INDEX == 0 else fee1
    paired_fee = fee1 if config.STOCK_INDEX == 0 else fee0
    paired_symbol = config.TOKEN1_SYMBOL if config.STOCK_INDEX == 0 else config.TOKEN0_SYMBOL
    state.last_fee_claim_iso = datetime.now(timezone.utc).isoformat()
    state.pending_stock_fees += stock_fee
    state.claimed_stock_fees += stock_fee
    save_state()

    sale_line = f"Pending stock fees: {state.pending_stock_fees:.8f} {config.STOCK_TOKEN_SYMBOL}"
    if config.SELL_STOCK_FEES_TO_ETH and state.pending_stock_fees > 0:
        try:
            sold, received_eth, input_usd = bc.sell_stock_fees_to_eth(state.pending_stock_fees)
            state.pending_stock_fees = max(0, state.pending_stock_fees - sold)
            state.sold_stock_fees += sold
            state.eth_from_stock_fees += received_eth
            save_state()
            sale_line = (
                f"Sold {sold:.8f} {config.STOCK_TOKEN_SYMBOL} (${input_usd:.4f}) "
                f"to {received_eth:.8f} ETH"
            )
            logger.info("%s fee conversion: %s", context, sale_line)
        except Exception as exc:
            logger.warning(
                "%s stock-fee conversion deferred; tracked fees remain outside LP principal: %s",
                context,
                exc,
            )
            sale_line = f"Stock-fee sale deferred safely: {exc}"

    notifications.send(
        "Robinhood LP fees claimed\n"
        f"Pool: {config.POOL_LABEL}\n"
        f"Claimed: {fee0:.8f} {config.TOKEN0_SYMBOL} + {fee1:.8f} {config.TOKEN1_SYMBOL}\n"
        f"Paired-token fees kept: {paired_fee:.8f} {paired_symbol}\n"
        f"{sale_line}"
    )


def rebalance() -> None:
    logger.warning("=== REBALANCE TRIGGERED ===")
    try:
        claim_fees_and_convert("pre-rebalance")
    except Exception as exc:
        logger.warning("Pre-rebalance fee claim failed; continuing with principal rebalance: %s", exc)
    withdrawn0, withdrawn1 = bc.burn_position(state.token_id)
    state.token_id = 0
    save_state()
    logger.info("Withdrawn %.8f %s and %.8f %s", withdrawn0, config.TOKEN0_SYMBOL, withdrawn1, config.TOKEN1_SYMBOL)
    live0, live1, _ = bc.balances()
    if config.STOCK_INDEX == 0:
        live0 = max(0, live0 - state.pending_stock_fees)
    else:
        live1 = max(0, live1 - state.pending_stock_fees)
    open_position("rebalance", live0, live1)
    state.rebalances += 1
    save_state()


def status_message(spot: float, position: bc.PositionSnapshot) -> str:
    total = position.value_quote
    pnl = total - state.starting_value
    return (
        "ROBINHOOD MULTI-POOL LP STATUS\n"
        f"Pool: {config.POOL_LABEL}\n"
        f"Spot: {spot:.8f} {config.QUOTE_SYMBOL} per {config.BASE_SYMBOL}\n"
        f"Range: {state.range_low:.8f} - {state.range_high:.8f}\n"
        f"Liquidity: {position.amount0:.8f} {config.TOKEN0_SYMBOL} + {position.amount1:.8f} {config.TOKEN1_SYMBOL}\n"
        f"Value: {total:.4f} {config.QUOTE_SYMBOL}\n"
        f"PnL vs start: {pnl:+.4f} {config.QUOTE_SYMBOL}\n"
        f"Claimed {config.STOCK_TOKEN_SYMBOL} fees: {state.claimed_stock_fees:.8f}\n"
        f"Sold to ETH: {state.sold_stock_fees:.8f} {config.STOCK_TOKEN_SYMBOL} -> "
        f"{state.eth_from_stock_fees:.8f} ETH\n"
        f"Rebalances: {state.rebalances}"
    )


def _read_with_retry(label: str, read_fn, attempts: int = 3, delay_seconds: int = 5):
    for attempt in range(1, attempts + 1):
        try:
            return read_fn()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if attempt >= attempts:
                raise
            logger.warning(
                "%s read failed (%s/%s): %s. Retrying in %ss.",
                label,
                attempt,
                attempts,
                exc,
                delay_seconds,
            )
            time.sleep(delay_seconds)


def monitor() -> None:
    next_report = time.time() + config.STATUS_REPORT_HOURS * 3600
    while True:
        started = time.time()
        try:
            spot = _read_with_retry("spot price", bc.get_spot_price)
            in_range = state.range_low <= spot <= state.range_high
            logger.info(
                "Spot=%.8f | Range=[%.8f, %.8f] | In range=%s",
                spot, state.range_low, state.range_high, in_range,
            )
            if in_range:
                if state.out_anchor_price:
                    logger.info("Price returned in range; clearing confirmation anchor.")
                state.out_anchor_price = 0
                state.out_direction = ""
                save_state()
            else:
                direction = "below" if spot < state.range_low else "above"
                if not state.out_anchor_price or state.out_direction != direction:
                    state.out_anchor_price = spot
                    state.out_direction = direction
                    save_state()
                    logger.warning(
                        "Out of range (%s); confirmation 1/2 anchored at %.8f. Checking again in %.1f minutes.",
                        direction,
                        spot,
                        config.CHECK_INTERVAL_MINUTES,
                    )
                else:
                    moved_farther = (
                        spot < state.out_anchor_price if direction == "below" else spot > state.out_anchor_price
                    )
                    if moved_farther:
                        logger.warning(
                            "Out of range confirmation 2/2: anchor=%.8f, now=%.8f, moved farther=True.",
                            state.out_anchor_price,
                            spot,
                        )
                        rebalance()
                    else:
                        logger.info(
                            "Still out of range, but price did not move farther than anchor %.8f; waiting.",
                            state.out_anchor_price,
                        )
            if _fee_claim_due():
                claim_fees_and_convert("periodic")
            if time.time() >= next_report:
                position = _read_with_retry(
                    "position status",
                    lambda: bc.read_position(state.token_id),
                )
                notifications.send(status_message(spot, position))
                next_report = time.time() + config.STATUS_REPORT_HOURS * 3600
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.error("Main loop error after read retries: %s. Retrying in 60s.", exc)
            notifications.send(f"Robinhood LP temporary error\n{exc}\nRetrying automatically.")
            time.sleep(60)
        elapsed = time.time() - started
        time.sleep(max(1, config.CHECK_INTERVAL_MINUTES * 60 - elapsed))


def main() -> None:
    bc.verify_pool()
    spot = bc.get_spot_price()
    logger.info("=" * 68)
    logger.info("ROBINHOOD MULTI-POOL - Uniswap %s LP bot", config.POOL_PROTOCOL.upper())
    logger.info("Pool   : %s", config.POOL_LABEL)
    logger.info("Pool   : %s", config.POOL_ID)
    logger.info("Price  : %.8f %s per %s", spot, config.QUOTE_SYMBOL, config.BASE_SYMBOL)
    logger.info("Range  : -%.2f%% / +%.2f%%", config.RANGE_DOWN_PERCENT, config.RANGE_UP_PERCENT)
    logger.info("Check  : every %.1f min; rebalance after a farther second out-of-range check", config.CHECK_INTERVAL_MINUTES)
    logger.info("Buffer : none")
    logger.info("Swap   : best safe Uniswap/KyberSwap; impact <= %.2f%%; slippage <= %.2f%%", config.MAX_PRICE_IMPACT_PERCENT, config.SLIPPAGE_PERCENT)
    logger.info(
        "Fees   : claim every %.1fh; keep paired-token fees; sell claimed %s fees to ETH=%s",
        config.FEE_CLAIM_HOURS,
        config.STOCK_TOKEN_SYMBOL,
        config.SELL_STOCK_FEES_TO_ETH,
    )
    logger.info("=" * 68)

    if state.token_id:
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
    monitor()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
        if config.WITHDRAW_ON_CTRL_C and state.token_id:
            try:
                try:
                    claim_fees_and_convert("Ctrl+C")
                except Exception as exc:
                    logger.warning("Ctrl+C fee claim/conversion failed; continuing with withdrawal: %s", exc)
                logger.info("Ctrl+C safety: withdrawing position %s.", state.token_id)
                bc.burn_position(state.token_id)
                state.token_id = 0
                save_state()
                notifications.send("Robinhood LP stopped; position withdrawn to wallet.")
            except Exception as exc:
                logger.error("Ctrl+C withdrawal failed: %s", exc)
                notifications.send(f"Robinhood LP Ctrl+C withdrawal failed\n{exc}")
                sys.exit(1)
