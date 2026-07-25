from __future__ import annotations

import blockchain as bc
import config


def main() -> None:
    print("\nRobinhood multi-pool V4 bot - read-only diagnostics")
    print(f"Pool : {config.POOL_LABEL}")
    print(f"ID   : {config.POOL_ID}")
    bc.verify_pool()
    sqrt_price, tick, fee = bc.get_slot0()
    balance0, balance1, native = bc.balances()
    print(f"Price: {bc.get_spot_price():.10f} {config.QUOTE_SYMBOL} per {config.BASE_SYMBOL}")
    print(f"Slot0: sqrtPriceX96={sqrt_price}, tick={tick}, lpFee={fee}")
    print(f"Funds: {balance0:.8f} {config.TOKEN0_SYMBOL}, {balance1:.8f} {config.TOKEN1_SYMBOL}, ETH={native:.8f}")
    print("Pool key and live state verified. No transaction was sent.")


if __name__ == "__main__":
    main()
