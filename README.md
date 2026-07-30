# Robinhood Chain Multi-Pool Uniswap V3/V4 LP Bot

This is the final Robinhood Chain stock LP bot. It discovers supported
stock/ETH and stock/USDG Uniswap V3/V4 pools, displays them from highest live APR
to lowest, asks the user to select a pool, and asks for the symmetric price
range to use.

## Main behavior

- Loads Robinhood's official live asset registry.
- Discovers compatible Uniswap V3/V4 stock/ETH and stock/USDG pools.
- Keeps every eligible protocol and fee-tier variant, so multiple pools for
  the same stock/quote pair remain selectable.
- Removes duplicate pools from the startup catalog.
- Keeps specifically pinned pools on their requested fee tier.
- Skips dynamically discovered pools below $100 TVL or above a 5% fee by
  default; both safety filters are configurable in `.env`.
- Shows each pool's latest available APR and TVL.
- Sorts selectable pools from highest APR to lowest APR.
- Accepts a menu number, ticker, exact pool ID, or full Uniswap pool URL.
- Refreshes live discovery at startup and merges it with the verified bundled
  catalog, so a partial API response cannot erase previously working pools.
- Asks for the range percentage at every interactive startup.
- Scans the selected pool's position manager before any deposit prompt. A
  wallet-owned active LP NFT is resumed even when the local state file is
  missing.
- Uses the same percentage above and below the current market price.
- Checks the position every 10 minutes.
- Uses no rebalance buffer.
- Requires two out-of-range confirmations.
- The second check must remain outside on the same side and move farther than
  the first check before rebalancing.

The displayed APR annualizes recent pool fees. It is not guaranteed and can
change quickly, especially in low-TVL pools.

## Deposit funding

The bot accepts the two selected pool currencies and can also use the other
supported quote asset as startup funding.

### Stock/USDG pool

- The user may deposit wallet USDG and stock tokens directly.
- The user may additionally select native ETH to convert into USDG.
- Only ETH above the protected gas reserve is offered.

### Stock/ETH pool

- The user may deposit native ETH and stock tokens directly.
- The user may additionally select wallet USDG to convert into ETH.
- The converted ETH is included in the selected LP capital.

The bot always keeps at least `0.01 ETH` untouched for transaction fees. WETH
does not replace this native ETH reserve.

## Position balancing

The bot calculates the actual token ratio required by the selected
concentrated range. It does not assume that every position requires an exact
wallet-level 50/50 token count.

- Stock/USDG balancing swaps below `1 USDG` are treated as dust.
- Stock/ETH balancing swaps below `0.0005 ETH` equivalent are treated as dust.
- A stock token amount is never incorrectly compared with the `1 USDG`
  threshold.
- Larger imbalances are swapped before minting.

These defaults can be changed with `MIN_SWAP_VALUE_QUOTE` and
`MIN_SWAP_VALUE_ETH` in `.env`.

## Swap routing and safety

The bot compares:

- Uniswap V3 or V4, matching the selected pool
- KyberSwap Robinhood routing API

It selects the highest-output route that passes all configured safety checks.
If that route fails transaction simulation or execution, it tries the next
safe route.

Default safety limits:

- Maximum slippage: `0.01%`
- Maximum price impact: `0.05%`
- Startup retry delay: `15 seconds`

No transaction is intentionally broadcast when its gas simulation indicates
that it is likely to revert.

## Rebalancing

When the position qualifies for rebalance, the bot:

1. Claims accrued fees.
2. Converts only the tracked stock-token fees to native ETH when enabled.
3. Removes the old Uniswap V3 or V4 position.
4. Reads the live wallet balances returned by the position.
5. Calculates the new range ratio.
6. Performs any required safe balancing swap.
7. Mints the replacement position around the current price.

The bot does not rebalance merely because six checks passed. Rebalancing is
based on two directional out-of-range confirmations.

## Fee handling

- Claims LP fees every four hours by default.
- Keeps claimed ETH or USDG paired-token fees in the wallet.
- Tracks the exact amount of claimed stock-token fees separately from
  principal.
- Sells only those tracked stock fees to native ETH through KyberSwap.
- Existing stock principal already held in the wallet is not swept into the
  fee sale.
- Unsafe or uneconomically small fee sales are deferred.

For example, an SPCX/USDG fee claim keeps USDG and sells only the newly claimed
SPCX fee amount to ETH.

## Ctrl+C behavior

With `WITHDRAW_ON_CTRL_C=true`, pressing `Ctrl+C` requests withdrawal of the
active LP position before the bot exits.

Do not force-close the terminal while the withdrawal transaction is pending.

## Files

- `pools.py`: official asset discovery, pool catalog, pinned pools and cache.
- `pool_apr.py`: live APR and TVL lookup.
- `config.py`: startup pool/range selection and environment settings.
- `blockchain.py`: balances, routing, swaps, minting, claims and withdrawals.
- `lp_bot.py`: startup prompts, state, reports and rebalance loop.
- `check_pool_catalog.py`: verifies all cached V3 addresses and V4 pool keys.
- `notifications.py`: optional Telegram reports.
- `diagnose.py`: read-only environment and pool diagnostics.
- `.env.example`: safe configuration template.
- `RUN_ON_KALI.txt`: short installation commands.

## Kali installation

```bash
git clone https://github.com/m7mdd77/robinhood-chain-stock-lp-bot.git
cd robinhood-chain-stock-lp-bot
cp .env.example .env
nano .env
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m py_compile pools.py pool_apr.py config.py abis.py blockchain.py notifications.py lp_bot.py diagnose.py check_pool_catalog.py
python3 check_pool_catalog.py
python3 -m unittest test_strategy_math.py test_uniswap_router_211_encoding.py
python3 lp_bot.py
```

At minimum, fill in:

```env
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS
```

Telegram is optional. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` only if
reports are required.

## Important settings

```env
SLIPPAGE_PERCENT=0.01
MAX_PRICE_IMPACT_PERCENT=0.05
MIN_SWAP_VALUE_QUOTE=1
MIN_SWAP_VALUE_ETH=0.0005
NATIVE_GAS_RESERVE_ETH=0.01
STATUS_REPORT_HOURS=4
FEE_CLAIM_HOURS=4
SELL_STOCK_FEES_TO_ETH=true
WITHDRAW_ON_CTRL_C=true
```

Pool selection and range remain interactive when these values are blank:

```env
POOL_CHOICE=
RANGE_PERCENT=
```

## Security

- Never share `.env`, private keys, Telegram tokens or bot state files.
- Use a dedicated wallet with limited funds.
- Keep enough native ETH for gas even though the bot protects `0.01 ETH`.
- Review newly discovered pools, TVL and live APR before depositing.
- APR, fees, slippage guards and price-impact estimates do not remove smart
  contract, token, oracle, RPC or market risk.
