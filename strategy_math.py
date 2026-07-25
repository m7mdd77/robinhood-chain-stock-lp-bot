def token0_value_in_quote(amount0: float, token1_per_token0: float, base_index: int) -> float:
    """Convert a token0 amount to the pool's displayed quote currency."""
    if base_index == 0:
        return amount0 * token1_per_token0
    if base_index == 1:
        return amount0
    raise ValueError("base_index must be 0 or 1")
