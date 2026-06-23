import daily_report as dr


SIG = {
    "date": "2026-06-24",
    "regime": {"market_regime": "BULL", "vix": 16.5, "vix_zone": "NORMAL",
               "hyg_ok": True, "allow_entry_a": True, "allow_entry_b": True},
    "portfolio_cash": 3820.04,
    "exits": [],
    "entries": {"A": [{"ticker": "DOW", "rsi": 28}], "B": [], "C": [], "D": []},
}
HOLD = {"QQQM": {"shares": 7.78, "buy_price": 274.32, "strategy": "Core"},
        "HSY": {"shares": 2.0, "buy_price": 197.42, "strategy": "A"}}
FWD = {"value": 10120.0, "qqq": 10050.0, "edge_vs_qqq": 70.0}


def test_report_has_regime_and_holdings():
    r = dr.build_report(SIG, HOLD, FWD, prices={"QQQM": 300.0, "HSY": 170.0})
    assert "BULL" in r and "VIX 16.5" in r
    assert "QQQM" in r and "HSY" in r
    assert "DOW" in r            # 진입 후보
    assert "QQQ" in r            # forward 벤치마크


def test_report_pnl_emoji():
    r = dr.build_report(SIG, HOLD, FWD, prices={"QQQM": 300.0, "HSY": 170.0})
    assert "🟢" in r            # QQQM 평단 274 < 300 → 이익
    assert "🔴" in r            # HSY 평단 197 > 170 → 손실


def test_report_no_action_when_empty():
    r = dr.build_report(SIG, HOLD, FWD)
    assert "매도 신호 없음" in r


def test_report_handles_missing_forward():
    r = dr.build_report(SIG, HOLD, None)
    assert "일일 리포트" in r   # forward 없어도 안 깨짐
