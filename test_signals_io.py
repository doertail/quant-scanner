"""signals.json 신선도 판정 테스트.

이 로직이 없어서 스캐너가 83일 죽어 있는 동안 daily_report는 7월 날짜 리포트를
계속 보냈고 forward_test는 얼어붙은 신호로 42일치 페이퍼 매매를 기록했다.
"""

from datetime import datetime

import signals_io


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def test_fresh_and_weekend_gap():
    # 화~토만 스캔 → 화요일 아침엔 토요일치(3일)가 정상이어야 한다
    today = _d("2026-09-08")  # 화
    assert signals_io.age_days("2026-09-08", today) == 0
    assert not signals_io.is_stale("2026-09-05", today)  # 토요일치 = 3일 전, 정상
    assert signals_io.describe("2026-09-05", today) == ""


def test_stale_boundary():
    today = _d("2026-09-08")
    assert not signals_io.is_stale("2026-09-05", today)  # 3일
    assert signals_io.is_stale("2026-09-04", today)      # 4일 = STALE_DAYS
    assert "4일 전" in signals_io.describe("2026-09-04", today)


def test_scanner_death_window():
    # 실제 사고: 7/9 마지막 정상 스캔, 9/5까지 아무도 몰랐다
    assert signals_io.age_days("2026-07-09", _d("2026-09-05")) == 58
    assert signals_io.is_stale("2026-07-09", _d("2026-09-05"))


def test_unparseable_date_is_stale():
    # 믿을 근거가 없으면 믿지 않는다
    assert signals_io.is_stale("어제")
    assert signals_io.is_stale(None)
    assert "불명" in signals_io.describe("어제")


def test_paper_exits_are_separate_from_real_exits():
    """페이퍼 북과 실계좌는 종목이 겹치지 않는다. 장부별로 청산이 나와야 한다.

    54일간 매도 0건이었던 원인: forward_test가 실계좌 exits를 봤고 거기엔
    페이퍼 북 종목이 애초에 없었다.
    """
    import scanner_v4 as sc

    rows = [
        {'ticker': 'AAPL', 'signal': 'TP1', 'strategy': 'A', 'trailing_stop': 308.52},
        {'ticker': 'DOW', 'signal': 'HOLD', 'strategy': 'A', 'trailing_stop': 26.24},
        {'ticker': 'AMD', 'signal': 'MA_CROSS', 'strategy': 'B', 'trailing_stop': 387.53},
    ]
    book = {'AAPL': {'shares': 3}, 'DOW': {'shares': 32}, 'AMD': {'shares': 1}}
    out = sc.exit_rows(rows, book)
    assert [r['ticker'] for r in out] == ['AAPL', 'AMD']   # HOLD는 빠진다
    assert out[0]['shares'] == 3                            # 수량은 그 장부에서 온다
    assert sc.exit_rows(rows, {})[0]['shares'] == 0          # 다른 장부면 0 — 못 판다


def test_forward_test_reads_paper_exits():
    """회귀 방지: forward_test가 실계좌 exits로 되돌아가면 다시 영영 못 판다."""
    src = open('forward_test.py', encoding='utf-8').read()
    assert "signals.get('paper_exits'" in src
    assert "signals.get('exits'" not in src
