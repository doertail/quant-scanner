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


def test_coverage_warning():
    # 커버리지가 온전하면 조용, 쪼그라들면 리포트에 박힌다 (2026-09-08: launchd 실행마다 30%였다)
    assert "커버리지" not in dr.build_report({**SIG, "coverage_pct": 100.0}, HOLD, FWD)
    assert "커버리지" not in dr.build_report(SIG, HOLD, FWD)          # 옛 signals.json — 없으면 조용
    assert "커버리지 30%" in dr.build_report({**SIG, "coverage_pct": 30.3}, HOLD, FWD)


def test_sync_warning():
    # 토스 동기화가 밀리면 보유·현금이 실계좌와 다른 채로 리포트가 나간다.
    # 실패는 forward_cron.log에만 남아 안 보였다 (2026-09-08).
    from datetime import datetime, timedelta
    today = datetime.today()
    assert dr.sync_warning(today.strftime("%Y-%m-%d")) == ""
    assert dr.sync_warning((today - timedelta(days=3)).strftime("%Y-%m-%d")) == ""  # 화 아침 = 토요일치
    assert "4일 전" in dr.sync_warning((today - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert "기록 없음" in dr.sync_warning(None)   # 파일 없음 = 성공한 적 없음
    assert "토스 동기화" in dr.build_report(SIG, HOLD, FWD, sync_line="⚠️ 토스 동기화 기록 없음")
    assert "토스 동기화" not in dr.build_report(SIG, HOLD, FWD)


def test_stale_warning():
    from datetime import datetime, timedelta
    import daily_report as dr
    today = datetime.today()
    assert dr.stale_warning(today.strftime("%Y-%m-%d")) == ""
    assert dr.stale_warning((today - timedelta(days=3)).strftime("%Y-%m-%d")) == ""   # 화요일 아침 = 토요일치
    assert "낡았다" in dr.stale_warning((today - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert "60일 전" in dr.stale_warning((today - timedelta(days=60)).strftime("%Y-%m-%d"))
    assert "불명" in dr.stale_warning("어제")
