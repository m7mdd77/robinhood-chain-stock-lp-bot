import unittest

from strategy_math import token0_value_in_quote


class Token0QuoteValueTests(unittest.TestCase):
    def test_token0_is_quote_for_usdg_msft(self):
        self.assertAlmostEqual(token0_value_in_quote(132.7128, 1 / 392.38188287, 1), 132.7128)

    def test_token0_is_base_for_msft_usdg(self):
        self.assertAlmostEqual(token0_value_in_quote(0.33822361, 392.38188287, 0), 132.7128, places=3)


if __name__ == "__main__":
    unittest.main()
