ERC20_ABI = [
    {"type": "function", "name": "balanceOf", "stateMutability": "view", "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "allowance", "stateMutability": "view", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
]

STATE_VIEW_ABI = [{
    "type": "function", "name": "getSlot0", "stateMutability": "view",
    "inputs": [{"name": "poolId", "type": "bytes32"}],
    "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"}, {"name": "protocolFee", "type": "uint24"}, {"name": "lpFee", "type": "uint24"}],
}]

V4_QUOTER_ABI = [{
    "type": "function", "name": "quoteExactInputSingle", "stateMutability": "nonpayable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "poolKey", "type": "tuple", "components": [
            {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
            {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"}, {"name": "hooks", "type": "address"},
        ]},
        {"name": "zeroForOne", "type": "bool"}, {"name": "exactAmount", "type": "uint128"}, {"name": "hookData", "type": "bytes"},
    ]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "gasEstimate", "type": "uint256"}],
}]

PERMIT2_ABI = [
    {"type": "function", "name": "allowance", "stateMutability": "view", "inputs": [{"name": "user", "type": "address"}, {"name": "token", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"name": "amount", "type": "uint160"}, {"name": "expiration", "type": "uint48"}, {"name": "nonce", "type": "uint48"}]},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable", "inputs": [{"name": "token", "type": "address"}, {"name": "spender", "type": "address"}, {"name": "amount", "type": "uint160"}, {"name": "expiration", "type": "uint48"}], "outputs": []},
]

POSITION_MANAGER_ABI = [
    {"type": "function", "name": "modifyLiquidities", "stateMutability": "payable", "inputs": [{"name": "unlockData", "type": "bytes"}, {"name": "deadline", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "ownerOf", "stateMutability": "view", "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "getPositionLiquidity", "stateMutability": "view", "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "uint128"}]},
    {"type": "function", "name": "getPoolAndPositionInfo", "stateMutability": "view", "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [
        {"name": "poolKey", "type": "tuple", "components": [
            {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
            {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"}, {"name": "hooks", "type": "address"},
        ]},
        {"name": "info", "type": "uint256"},
    ]},
]

UNIVERSAL_ROUTER_ABI = [{
    "type": "function", "name": "execute", "stateMutability": "payable",
    "inputs": [{"name": "commands", "type": "bytes"}, {"name": "inputs", "type": "bytes[]"}, {"name": "deadline", "type": "uint256"}], "outputs": [],
}]

V3_POOL_ABI = [
    {"type": "function", "name": "factory", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "token0", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "token1", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "fee", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint24"}]},
    {"type": "function", "name": "tickSpacing", "stateMutability": "view", "inputs": [], "outputs": [{"type": "int24"}]},
    {"type": "function", "name": "slot0", "stateMutability": "view", "inputs": [], "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"},
        {"name": "unlocked", "type": "bool"},
    ]},
]

V3_QUOTER_ABI = [{
    "type": "function", "name": "quoteExactInputSingle", "stateMutability": "nonpayable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ]}],
    "outputs": [
        {"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
        {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"},
    ],
}]

V3_SWAP_ROUTER_ABI = [{
    "type": "function", "name": "exactInputSingle", "stateMutability": "payable",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "recipient", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "amountOutMinimum", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}],
}]

V3_POSITION_MANAGER_ABI = [
    {"type": "function", "name": "ownerOf", "stateMutability": "view", "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "mint", "stateMutability": "payable", "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "token0", "type": "address"}, {"name": "token1", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "tickLower", "type": "int24"},
        {"name": "tickUpper", "type": "int24"}, {"name": "amount0Desired", "type": "uint256"},
        {"name": "amount1Desired", "type": "uint256"}, {"name": "amount0Min", "type": "uint256"},
        {"name": "amount1Min", "type": "uint256"}, {"name": "recipient", "type": "address"},
        {"name": "deadline", "type": "uint256"},
    ]}], "outputs": [
        {"name": "tokenId", "type": "uint256"}, {"name": "liquidity", "type": "uint128"},
        {"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"},
    ]},
    {"type": "function", "name": "positions", "stateMutability": "view", "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [
        {"name": "nonce", "type": "uint96"}, {"name": "operator", "type": "address"},
        {"name": "token0", "type": "address"}, {"name": "token1", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "tickLower", "type": "int24"},
        {"name": "tickUpper", "type": "int24"}, {"name": "liquidity", "type": "uint128"},
        {"name": "feeGrowthInside0LastX128", "type": "uint256"}, {"name": "feeGrowthInside1LastX128", "type": "uint256"},
        {"name": "tokensOwed0", "type": "uint128"}, {"name": "tokensOwed1", "type": "uint128"},
    ]},
    {"type": "function", "name": "decreaseLiquidity", "stateMutability": "payable", "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenId", "type": "uint256"}, {"name": "liquidity", "type": "uint128"},
        {"name": "amount0Min", "type": "uint256"}, {"name": "amount1Min", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ]}], "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}]},
    {"type": "function", "name": "collect", "stateMutability": "payable", "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenId", "type": "uint256"}, {"name": "recipient", "type": "address"},
        {"name": "amount0Max", "type": "uint128"}, {"name": "amount1Max", "type": "uint128"},
    ]}], "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}]},
]
