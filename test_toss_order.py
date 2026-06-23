import pytest

from toss_client import TossClient, TossAPIError


def test_build_market_sell_quantity():
    b = TossClient.build_order_body("PPL", "sell", quantity="12")
    assert b == {"symbol": "PPL", "side": "SELL", "orderType": "MARKET", "quantity": "12"}


def test_build_limit_buy_requires_price():
    b = TossClient.build_order_body("005930", "BUY", quantity="10",
                                    order_type="LIMIT", price="70000")
    assert b["orderType"] == "LIMIT" and b["price"] == "70000" and b["side"] == "BUY"


def test_build_amount_based():
    b = TossClient.build_order_body("AAPL", "BUY", order_amount="100.5")
    assert b["orderAmount"] == "100.5" and "quantity" not in b


def test_build_rejects_limit_without_price():
    with pytest.raises(TossAPIError):
        TossClient.build_order_body("PPL", "SELL", quantity="12", order_type="LIMIT")


def test_build_rejects_no_qty_no_amount():
    with pytest.raises(TossAPIError):
        TossClient.build_order_body("PPL", "SELL")


def test_build_rejects_bad_side():
    with pytest.raises(TossAPIError):
        TossClient.build_order_body("PPL", "HOLD", quantity="1")
