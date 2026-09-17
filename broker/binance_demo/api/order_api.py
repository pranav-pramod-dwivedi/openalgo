"""
Binance Demo Order API Module
Connects OpenAlgo's broker order API interface to BinanceDemoService.
"""

from typing import Any, Dict, List, Optional, Tuple
from services.binance_demo_service import binance_demo_service
from utils.logging import get_logger

logger = get_logger(__name__)


def get_order_book(auth: str = "") -> List[Dict[str, Any]]:
    """Get orders formatted for OpenAlgo."""
    return binance_demo_service.get_orderbook_formatted()


def get_trade_book(auth: str = "") -> List[Dict[str, Any]]:
    """Get trade history formatted for OpenAlgo."""
    return binance_demo_service.get_tradebook_formatted()


def get_positions(auth: str = "") -> List[Dict[str, Any]]:
    """Get positions formatted for OpenAlgo."""
    return binance_demo_service.get_positionbook_formatted()


def get_holdings(auth: str = "") -> Dict[str, Any]:
    """Get spot holdings formatted for OpenAlgo."""
    return binance_demo_service.get_holdings_formatted()


def get_open_position(
    tradingsymbol: str, exchange: str = "CRYPTO", product: str = "FUTURES", auth: str = ""
) -> int:
    """Get open position quantity for a symbol."""
    clean_sym = binance_demo_service.clean_symbol(tradingsymbol)
    positions = binance_demo_service.get_positions()
    for p in positions:
        if p.get("symbol") == clean_sym:
            return int(p.get("amount", 0))
    return 0


def place_order_api(data: Dict[str, Any], auth: str = "") -> Tuple[Any, Dict[str, Any], str]:
    """Place order on Binance Demo."""
    symbol = data.get("symbol", "")
    side = data.get("action", "BUY").upper()
    try:
        qty = float(data.get("quantity", 0))
    except (ValueError, TypeError):
        qty = 0.0
    pricetype = data.get("pricetype", "MARKET").upper()
    product = data.get("product", "FUTURES").upper()
    is_futures = product == "FUTURES" or "(Perp)" in symbol

    clean_sym = binance_demo_service.clean_symbol(symbol)
    res = binance_demo_service.place_order(
        symbol=clean_sym,
        side=side,
        quantity=qty,
        order_type=pricetype,
        is_futures=is_futures,
    )
    if res.get("status_code") == 200:
        orderid = str(res.get("response", {}).get("orderId", ""))
        return res, {"status": "success", "message": "Order Placed Successfully", "orderid": orderid}, orderid
    else:
        msg = res.get("response", {}).get("msg", "Failed to place order")
        return None, {"status": "error", "message": msg}, ""


def place_smartorder_api(data: Dict[str, Any], auth: str = "") -> Tuple[Any, Dict[str, Any], str]:
    """Smart order / exit on Binance Demo."""
    symbol = data.get("symbol", "")
    position_size = data.get("position_size")
    if position_size is not None and float(position_size) == 0:
        # Close position request
        success, resp, status_code = binance_demo_service.close_position(symbol=symbol)
        if success:
            orderid = resp.get("orderid", "")
            return resp, resp, orderid
        return None, resp, ""

    return place_order_api(data, auth)


def cancel_order(orderid: str, auth: str = "") -> Tuple[Dict[str, Any], int]:
    """Cancel a single order on Binance Demo."""
    success, resp, status_code = binance_demo_service.cancel_order(orderid)
    return resp, status_code


def cancel_all_orders_api(data: Optional[Dict[str, Any]] = None, auth: str = "") -> Tuple[List[str], List[str]]:
    """Cancel all open orders on Binance Demo."""
    success, resp, status_code = binance_demo_service.cancel_all_orders(data)
    canceled = resp.get("canceled_orders", [])
    failed = resp.get("failed_cancellations", [])
    return canceled, failed


def close_all_positions(current_api_key: str = "", auth: str = "") -> Tuple[Dict[str, Any], int]:
    """Close all open positions on Binance Demo."""
    success, resp, status_code = binance_demo_service.close_all_positions()
    return resp, status_code


def modify_order(data: Dict[str, Any], auth: str = "") -> Tuple[Dict[str, Any], int]:
    """Modify order on Binance Demo by cancelling and placing updated order."""
    orderid = data.get("orderid", "")
    if orderid:
        binance_demo_service.cancel_order(orderid)
    res, resp, new_orderid = place_order_api(data, auth)
    return resp, 200 if res else 400

