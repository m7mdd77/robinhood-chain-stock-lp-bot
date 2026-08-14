from __future__ import annotations

import ast
import pathlib
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
BLOCKCHAIN_SOURCE = (ROOT / "blockchain.py").read_text(encoding="utf-8")
LP_BOT_SOURCE = (ROOT / "lp_bot.py").read_text(encoding="utf-8")
POOLS_SOURCE = (ROOT / "pools.py").read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


class SecurityRegressionTests(unittest.TestCase):
    def test_confirmed_swap_balance_read_failure_never_rebroadcasts(self):
        source = function_source(BLOCKCHAIN_SOURCE, "execute_swap")
        calls = {"execute": 0, "balance": 0}

        def raw_balance(_token):
            calls["balance"] += 1
            if calls["balance"] == 1:
                return 0
            raise ConnectionError("RPC balance read unavailable")

        def execute(_route, _token_in, _token_out, _amount):
            calls["execute"] += 1

        config = types.SimpleNamespace(
            TOKEN0_ADDRESS="0x01",
            TOKEN1_ADDRESS="0x02",
            TOKEN0_DECIMALS=6,
            TOKEN1_DECIMALS=6,
            MAX_SWAP_ATTEMPTS=2,
            RECEIPT_READ_ATTEMPTS=2,
            RECEIPT_RETRY_SECONDS=0,
            SWAP_RETRY_SECONDS=0,
        )
        namespace = {
            "Any": object,
            "config": config,
            "get_token1_per_token0": lambda: 1.0,
            "safe_routes": lambda *_args: ([{"provider": "uniswap", "amount_out": 995_000}], []),
            "_raw_balance": raw_balance,
            "_execute_uniswap": execute,
            "_execute_kyber": execute,
            "_execute_api_transaction": execute,
            "UnsafeToRetryError": type("UnsafeToRetryError", (RuntimeError,), {}),
            "ConfirmedTransactionInvariantError": type(
                "ConfirmedTransactionInvariantError", (RuntimeError,), {}
            ),
            "logger": types.SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None),
            "time": types.SimpleNamespace(sleep=lambda *_a: None),
        }
        exec("from __future__ import annotations\n" + source, namespace)
        provider, sold, received = namespace["execute_swap"]("0x01", "0x02", 1.0)
        self.assertEqual((provider, sold, received), ("uniswap", 1.0, 0.995))
        self.assertEqual(calls["execute"], 1, "a confirmed swap must never be broadcast twice")

    def test_v4_position_must_match_selected_pool_key(self):
        source = function_source(BLOCKCHAIN_SOURCE, "read_position")
        self.assertIn("returned_key != expected_key", source)
        self.assertIn("belongs to a different pool", source)

    def test_withdrawal_has_nonzero_slippage_minimums(self):
        source = function_source(BLOCKCHAIN_SOURCE, "burn_position")
        self.assertIn("LP_WITHDRAW_TOLERANCE_PERCENT", source)
        self.assertIn("[token_id, min0, min1, b\"\"]", source)
        self.assertNotIn("[token_id, 0, 0, b\"\"]", source)

    def test_kyber_is_host_and_router_allowlisted(self):
        http_source = function_source(BLOCKCHAIN_SOURCE, "_http_json")
        execute_source = function_source(BLOCKCHAIN_SOURCE, "_execute_kyber")
        self.assertIn("KYBER_ALLOWED_HOST", http_source)
        self.assertIn("KYBER_ALLOWED_ROUTERS", execute_source)
        self.assertIn("unapproved router", execute_source)

    def test_stock_fee_impact_uses_complete_usd_loss(self):
        source = function_source(BLOCKCHAIN_SOURCE, "_kyber_stock_to_eth_route")
        self.assertIn("(amount_in_usd - amount_out_usd) / amount_in_usd", source)
        self.assertNotIn("known_fee_percent", source)

    def test_retries_are_bounded_and_partial_refresh_keeps_verified_catalog(self):
        self.assertIn("MAX_SWAP_ATTEMPTS", function_source(BLOCKCHAIN_SOURCE, "execute_swap"))
        self.assertIn("MAX_OPEN_ATTEMPTS", function_source(LP_BOT_SOURCE, "open_position"))
        merge_source = function_source(POOLS_SOURCE, "_merge_catalogs")
        self.assertIn("for pool in cached.values()", merge_source)
        self.assertIn("by_id.update", merge_source)
        self.assertIn("for pool in discovered.values()", merge_source)

    def test_public_rpc_position_scans_retry_split_and_fail_closed(self):
        startup_source = (ROOT / "startup_positions.py").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for source in (startup_source, BLOCKCHAIN_SOURCE):
            self.assertIn('"i/o timeout"', source)
            self.assertIn('"connection reset"', source)
            self.assertIn("midpoint = (start + end) // 2", source)
        self.assertIn('os.getenv("REQUIRE_EXISTING_POSITION_SCAN", "true")', startup_source)
        self.assertIn("REQUIRE_EXISTING_POSITION_SCAN=true", env_example)


if __name__ == "__main__":
    unittest.main()
