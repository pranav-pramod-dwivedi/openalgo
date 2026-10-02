"""
Binance Demo Mapping Module
Formats and passes through order, trade, position, and holdings data.
"""

from typing import Any

# Agent products map to Binance venues for crypto: leveraged NRML/MIS become
# FUTURES (perpetuals) while cash CNC becomes SPOT. FUTURES/SPOT pass through.
AGENT_TO_BINANCE_PRODUCT: dict[str, str] = {
    "NRML": "FUTURES",
    "MIS": "FUTURES",
    "CNC": "SPOT",
    "FUTURES": "FUTURES",
    "SPOT": "SPOT",
}

# SL/SL-M/TARGET are allowed through validation because the broker layer maps
# them to Binance native stop/take types (STOP_MARKET/TAKE_PROFIT_MARKET on
# futures, STOP_LOSS/TAKE_PROFIT on spot) instead of rejecting them.
SUPPORTED_PRICE_TYPES: frozenset[str] = frozenset(
    {"MARKET", "LIMIT", "SL", "SL-M", "TARGET"}
)


def map_agent_product_to_binance(product: Any) -> str:
    """Translate an agent product to its Binance venue."""
    text = "" if product is None else str(product).strip().upper()
    return AGENT_TO_BINANCE_PRODUCT.get(text, text)


def map_product(product: Any) -> str:
    """Alias for :func:`map_agent_product_to_binance`."""
    return map_agent_product_to_binance(product)


def translate_product(product: Any) -> str:
    """Alias for :func:`map_agent_product_to_binance`."""
    return map_agent_product_to_binance(product)


def normalize_product(product: Any) -> str:
    """Alias for :func:`map_agent_product_to_binance`."""
    return map_agent_product_to_binance(product)


def is_supported_pricetype(pricetype: Any) -> bool:
    """Return True when a pricetype is allowed through for Binance mapping."""
    if pricetype is None:
        return False
    text = str(pricetype).strip().upper()
    if text == "SLM":
        text = "SL-M"
    return text in SUPPORTED_PRICE_TYPES


def validate_pricetype(pricetype: Any) -> bool:
    """Alias for :func:`is_supported_pricetype`."""
    return is_supported_pricetype(pricetype)


def normalize_pricetype(pricetype: Any) -> str:
    """Return the canonical pricetype, accepting SLM as a spelling of SL-M."""
    text = "" if pricetype is None else str(pricetype).strip().upper()
    if text == "SLM":
        return "SL-M"
    return text


def _translate_product_field(data: Any) -> Any:
    """Translate any product field in a mapping payload in place."""
    if isinstance(data, dict):
        for key in ("product", "productType", "producttype"):
            if key in data and isinstance(data[key], str):
                data[key] = map_agent_product_to_binance(data[key])
        return data
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                _translate_product_field(row)
        return data
    return data


def map_order_data(order_data: Any) -> Any:
    if order_data is None:
        return []
    return _translate_product_field(order_data) or []


def transform_order_data(order_data: Any) -> Any:
    if order_data is None:
        return []
    return _translate_product_field(order_data) or []


def map_trade_data(trade_data: Any) -> Any:
    if trade_data is None:
        return []
    return _translate_product_field(trade_data) or []


def transform_tradebook_data(trade_data: Any) -> Any:
    if trade_data is None:
        return []
    return _translate_product_field(trade_data) or []


def map_position_data(position_data: Any) -> Any:
    if position_data is None:
        return []
    return _translate_product_field(position_data) or []


def transform_positions_data(position_data: Any) -> Any:
    if position_data is None:
        return []
    return _translate_product_field(position_data) or []


def map_holding_data(holding_data: Any) -> Any:
    if holding_data is None:
        return {}
    if isinstance(holding_data, dict):
        return _translate_product_field(holding_data) or {}
    return _translate_product_field(holding_data) or {}


def transform_holdings_data(holding_data: Any) -> Any:
    if holding_data is None:
        return {}
    if isinstance(holding_data, dict):
        return _translate_product_field(holding_data) or {}
    return _translate_product_field(holding_data) or {}
