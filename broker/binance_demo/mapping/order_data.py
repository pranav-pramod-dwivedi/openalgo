"""
Binance Demo Mapping Module
Formats and passes through order, trade, position, and holdings data.
"""

from typing import Any, List


def map_order_data(order_data: Any) -> Any:
    return order_data or []


def transform_order_data(order_data: Any) -> Any:
    return order_data or []


def map_trade_data(trade_data: Any) -> Any:
    return trade_data or []


def transform_tradebook_data(trade_data: Any) -> Any:
    return trade_data or []


def map_position_data(position_data: Any) -> Any:
    return position_data or []


def transform_positions_data(position_data: Any) -> Any:
    return position_data or []


def map_holding_data(holding_data: Any) -> Any:
    return holding_data or {}


def transform_holdings_data(holding_data: Any) -> Any:
    return holding_data or {}
