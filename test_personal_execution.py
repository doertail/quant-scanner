from datetime import date

import personal_execution as pe


def test_panic_ladder_thirds_and_keeps_leftover():
    s, it = pe.panic_step({}, 31.0, 900.0)          # 1차
    assert s["active"] and s["done"] == 1 and s["tranche"] == 300.0
    assert [(i["side"], i["ticker"], i["usd"]) for i in it] == [("SELL", "TLT", 300.0), ("BUY", "QQQM", 300.0)]
    s, it = pe.panic_step(s, 33.0, 600.0)           # 35 미달 → 없음
    assert it == [] and s["done"] == 1
    s, it = pe.panic_step(s, 41.0, 600.0)           # 하루에 2·3차
    assert s["done"] == 3 and sum(i["usd"] for i in it if i["side"] == "BUY") == 600.0
    s, it = pe.panic_step(s, 19.0, 0.0)             # 종료 → 재무장, flush 없음
    assert not s["active"] and it == []


def test_panic_end_without_flush():
    s, _ = pe.panic_step({}, 31.0, 900.0)
    s, it = pe.panic_step(s, 18.0, 600.0)
    assert it == [] and not s["active"]             # 남은 $600은 다음 공포용


def test_weight_cap_sells_excess_only_for_single_stocks():
    held = {"CRWD": {"shares": 6, "last": 250.0}, "QQQM": {"shares": 20, "last": 300.0}}
    it = pe.weight_intents(held, 10_000.0)
    assert len(it) == 1 and it[0]["ticker"] == "CRWD" and it[0]["usd"] == 500.0


def test_standing_orders_expire_and_cap_to_held():
    orders = [{"symbol": "CRWD", "side": "SELL", "qty": 3, "limit": 250.0, "until": "2026-11-10"}]
    held = {"CRWD": {"shares": 2, "last": 245.0}}
    assert pe.standing_intents(orders, held, date(2026, 11, 4))[0]["qty"] == 2
    assert pe.standing_intents(orders, held, date(2026, 11, 11)) == []
    assert pe.standing_intents(orders, {}, date(2026, 11, 4)) == []
