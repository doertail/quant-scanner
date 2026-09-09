import daily_report as dr


SIG = {
    "date": "2026-06-24",
    "regime": {"market_regime": "BULL", "vix": 16.5, "vix_zone": "NORMAL",
               "hyg_ok": True, "allow_entry_a": True, "allow_entry_b": True},
    "portfolio_cash": 3820.04,
    "exits": [],
    "entries": {"A": [{"ticker": "DOW", "rsi": 28, "score": 0.3147},
                      {"ticker": "GD", "rsi": 29, "score": 0.2}],
                "B": [{"ticker": "NBIS", "rsi": 57, "score": 0.688}], "C": [], "D": []},
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


def test_score_and_rank_shown():
    # 23:00에도 리포트가 오는데 순위·점수가 없으면 5개 중 뭘 살지 못 고른다.
    r = dr.build_report(SIG, HOLD, FWD)
    assert "A1 0.31" in r and "A2 0.20" in r
    assert "B1 0.69" in r          # 순위는 전략 안에서만 — A/B 점수는 스케일이 다르다
    assert "✅ =" not in r         # 아침 스냅샷이 없으면 범례도 없다


def test_morning_confirmation_mark():
    # 23:00 인트라데이 후보 중 아침 종가 스캔에도 있던 것에만 ✅ (백테스트와 같은 기준)
    buys = [x for x in dr.build_report(SIG, HOLD, FWD, confirmed={"DOW"}).splitlines()
            if x.startswith("🟢 매수")]
    assert [x.endswith("✅") for x in buys] == [True, False, False]   # DOW만


def test_morning_tickers_ignores_other_day(tmp_path, monkeypatch):
    import json
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"date": "2026-06-24",
                             "entries": {"A": [{"ticker": "DOW"}], "B": [{"ticker": "NBIS"}]}}))
    monkeypatch.setattr(dr, "MORNING", f)
    assert dr.morning_tickers("2026-06-24") == {"DOW", "NBIS"}
    assert dr.morning_tickers("2026-06-25") == set()   # 어제치가 남아 있어도 안 쓴다
    monkeypatch.setattr(dr, "MORNING", tmp_path / "none.json")
    assert dr.morning_tickers("2026-06-24") == set()   # 07:30 실행엔 파일이 없다


def test_stale_warning():
    from datetime import datetime, timedelta
    import daily_report as dr
    today = datetime.today()
    assert dr.stale_warning(today.strftime("%Y-%m-%d")) == ""
    assert dr.stale_warning((today - timedelta(days=3)).strftime("%Y-%m-%d")) == ""   # 화요일 아침 = 토요일치
    assert "낡았다" in dr.stale_warning((today - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert "60일 전" in dr.stale_warning((today - timedelta(days=60)).strftime("%Y-%m-%d"))
    assert "불명" in dr.stale_warning("어제")
