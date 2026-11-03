import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_dca_panic_ladder import DAILY, BUY_COST, simulate

N = 10
op = cl = np.full(N, 100.0)
rf = np.zeros(N)


def test_all_dca_ignores_vix():
    vix = np.full(N, 50.0)
    final, _, buys = simulate(op, cl, vix, rf, 0, N - 1, 600, (30, 35, 40))
    assert buys == 0
    assert abs(final - N * DAILY * (1 - BUY_COST)) < 1e-9


def test_ladder_splits_pot_and_flush_spends_rest():
    # 5일 평온(모으기) → 31(1차) → 36(2차) → 15(종료, 3차 미달분 flush)
    vix = np.array([15, 15, 15, 15, 15, 31, 36, 15, 15, 15], float)
    final, _, buys = simulate(op, cl, vix, rf, 0, N - 1, 300, (30, 35, 40))
    assert buys == 3  # 1차 + 2차 + flush
    # 가격 일정: 공포 끝난 뒤 다시 모은 2일치(현금, 비용 없음)만 빼고 전액 매수됨
    saved = 2 * DAILY / 2
    assert abs(final - ((N * DAILY - saved) * (1 - BUY_COST) + saved)) < 1e-9


def test_without_flush_unhit_tranche_stays_cash():
    vix = np.array([15, 15, 15, 15, 15, 31, 15, 15, 15, 15], float)
    _, _, buys = simulate(op, cl, vix, rf, 0, N - 1, 300, (30, 35, 40), flush=False)
    assert buys == 1
