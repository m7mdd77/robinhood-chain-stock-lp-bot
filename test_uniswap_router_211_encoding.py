from pathlib import Path


EXACT_INPUT_SINGLE_TYPE = "((address,address,uint24,int24,address),bool,uint128,uint128,uint256,bytes)"


def test_router_211_exact_input_single_layout() -> None:
    source = Path(__file__).with_name("blockchain.py").read_text(encoding="utf-8")
    assert EXACT_INPUT_SINGLE_TYPE in source
    assert 'amount_in_raw, minimum, 0, b"")' in source
    assert "uint128,uint128,bytes)" not in source
