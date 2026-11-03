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


def test_report_has_regime_and_holdings():
    r = dr.build_report(SIG, HOLD, prices={"QQQM": 300.0, "HSY": 170.0})
    assert "BULL" in r and "VIX 16.5" in r
    assert "QQQM" in r and "HSY" in r
    assert "DOW" in r            # 진입 후보
    assert "내 투자 정책" in r and "기존 퀀트 전략 신호" in r
    assert "장기 핵심 적립" in r


def test_panic_review_is_manual_and_requires_vix_over_30():
    from datetime import datetime

    fresh = {**SIG, "date": datetime.today().strftime("%Y-%m-%d")}
    normal = dr.build_report(SIG, HOLD)
    panic = dr.build_report({**fresh, "regime": {**SIG["regime"], "vix": 31.0}}, HOLD)
    assert "추가 매수 여부와 금액" not in normal
    assert "실제 예수금 확인 후 직접 판단" in panic
    assert "추가 $100 매수마다 적립 여력 5거래일분 감소" in panic
    stale = dr.build_report({**fresh, "regime": {**SIG["regime"], "vix": 31.0}},
                            HOLD, sync_line="⚠️ 토스 동기화 기록 없음")
    assert "적립 여력 5거래일분" not in stale
    old_signal = dr.build_report({**SIG, "regime": {**SIG["regime"], "vix": 31.0}}, HOLD)
    assert "새 스캔 전까지 추가 매수 판단 보류" in old_signal


def test_protected_holding_exit_is_not_shown_as_sell_action():
    sig = {**SIG, "exits": [
        {"ticker": "TLT", "signal": "STOP"},
        {"ticker": "HSY", "signal": "STOP"},
    ]}
    r = dr.build_report(sig, HOLD)
    assert "🔒 보호 보유 TLT" in r
    assert "🔴 매도  TLT" not in r
    assert "🔴 매도  HSY" in r


def test_report_pnl_emoji():
    r = dr.build_report(SIG, HOLD, prices={"QQQM": 300.0, "HSY": 170.0})
    assert "🟢" in r            # QQQM 평단 274 < 300 → 이익
    assert "🔴" in r            # HSY 평단 197 > 170 → 손실


def test_report_no_action_when_empty():
    r = dr.build_report(SIG, HOLD)
    assert "매도 신호 없음" in r


def test_coverage_warning():
    # 커버리지가 온전하면 조용, 쪼그라들면 리포트에 박힌다 (2026-09-08: launchd 실행마다 30%였다)
    assert "커버리지" not in dr.build_report({**SIG, "coverage_pct": 100.0}, HOLD)
    assert "커버리지" not in dr.build_report(SIG, HOLD)          # 옛 signals.json — 없으면 조용
    assert "커버리지 30%" in dr.build_report({**SIG, "coverage_pct": 30.3}, HOLD)


def test_sync_warning():
    # 토스 동기화가 밀리면 보유·현금이 실계좌와 다른 채로 리포트가 나간다.
    # 실패는 forward_cron.log에만 남아 안 보였다 (2026-09-08).
    from datetime import datetime, timedelta
    today = datetime.today()
    assert dr.sync_warning(today.strftime("%Y-%m-%d")) == ""
    assert dr.sync_warning((today - timedelta(days=3)).strftime("%Y-%m-%d")) == ""  # 화 아침 = 토요일치
    assert "4일 전" in dr.sync_warning((today - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert "기록 없음" in dr.sync_warning(None)   # 파일 없음 = 성공한 적 없음
    assert "토스 동기화" in dr.build_report(SIG, HOLD, sync_line="⚠️ 토스 동기화 기록 없음")
    assert "토스 동기화" not in dr.build_report(SIG, HOLD)


def test_score_and_rank_shown():
    # 23:00에도 리포트가 오는데 순위·점수가 없으면 5개 중 뭘 살지 못 고른다.
    r = dr.build_report(SIG, HOLD)
    assert "A1 0.31" in r and "A2 0.20" in r
    assert "B1 0.69" in r          # 순위는 전략 안에서만 — A/B 점수는 스케일이 다르다
    assert "✅ =" not in r         # 아침 스냅샷이 없으면 범례도 없다


def test_morning_confirmation_mark():
    # 23:00 인트라데이 후보 중 아침 종가 스캔에도 있던 것에만 ✅ (백테스트와 같은 기준)
    buys = [x for x in dr.build_report(SIG, HOLD, confirmed={"DOW"}).splitlines()
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


def test_slots_left_ignores_core():
    # Core(지수 DCA)는 슬리브가 아니다. HSY 하나가 A 정원을 먹는다.
    assert dr.slots_left(HOLD) == {"A": dr.A_MAX_SLOTS - 1, "B": dr.B_MAX_SLOTS}
    assert dr.slots_left({})["A"] == dr.A_MAX_SLOTS


def test_over_quota_candidates_marked():
    # 정원이 차면 점수가 아무리 높아도 안 산다 — A/B를 점수로 경쟁시키지 않기 위한 원칙.
    full = {f"X{i}": {"strategy": "A"} for i in range(dr.A_MAX_SLOTS)}
    r = dr.build_report(SIG, full)
    assert "⛔ 정원참 DOW" in r and "⛔ 정원참 GD" in r    # A 자리 없음
    assert "🟢 매수  NBIS" in r                           # B는 비어 있으니 산다
    assert "남은 정원 A 0" in r


def test_stale_warning():
    from datetime import datetime, timedelta
    import daily_report as dr
    today = datetime.today()
    assert dr.stale_warning(today.strftime("%Y-%m-%d")) == ""
    assert dr.stale_warning((today - timedelta(days=3)).strftime("%Y-%m-%d")) == ""   # 화요일 아침 = 토요일치
    assert "낡았다" in dr.stale_warning((today - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert "60일 전" in dr.stale_warning((today - timedelta(days=60)).strftime("%Y-%m-%d"))
    assert "불명" in dr.stale_warning("어제")
