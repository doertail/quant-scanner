"""
backtest_per_strategy.py — 전략 A/B/C 각각을 단독 포트폴리오로 격리 실행해 CAGR 측정.

backtest_validation.setup()으로 데이터 1회 준비 후, 다른 전략을 끄고
(A_MAX_POS/B_MAX_POS=0, C/D_POSITION_PCT=0) 한 전략만 $10만으로 굴린 결과.

주의: 단독 실행은 자본 대부분이 현금으로 남아(cash drag) CAGR이 낮게 나온다.
'그 전략만 단독 운용 시'의 정직한 포트폴리오 CAGR이다(건당 수익률과 다름).
B는 NDX100 생존편향에 노출.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import backtest_v4 as bt
import backtest_validation as bv

KNOBS = ('A_MAX_POS', 'B_MAX_POS', 'C_POSITION_PCT', 'D_POSITION_PCT')

CONFIGS = [
    ("A 단독 (평균회귀)", dict(A_MAX_POS=10, B_MAX_POS=0,  C_POSITION_PCT=0.0,  D_POSITION_PCT=0.0)),
    ("B 단독 (모멘텀)",   dict(A_MAX_POS=0,  B_MAX_POS=10, C_POSITION_PCT=0.0,  D_POSITION_PCT=0.0)),
    ("C 단독 (VIX패닉)",  dict(A_MAX_POS=0,  B_MAX_POS=0,  C_POSITION_PCT=20.0, D_POSITION_PCT=0.0)),
    ("전체 A+B+C+D",      dict(A_MAX_POS=10, B_MAX_POS=10, C_POSITION_PCT=20.0, D_POSITION_PCT=20.0)),
]


def main():
    ctx = bv.setup()
    full = [d for d in ctx['all_dates']
            if pd.Timestamp('2005-01-01') <= d <= pd.Timestamp('2026-12-31')]
    base = {k: getattr(bt, k) for k in KNOBS}

    rows = []
    for name, ov in CONFIGS:
        for k in KNOBS:
            setattr(bt, k, ov.get(k, base[k]))
        m = bv.run(ctx, full)["main"]
        rows.append((name, m))
    for k in KNOBS:
        setattr(bt, k, base[k])

    W = 78
    print("\n" + "=" * W)
    print("  전략별 단독 CAGR (전체 2005-2026, $100K 시작)".center(W - 10))
    print("=" * W)
    print(f"{'전략':<20}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'거래수':>8}{'최종$':>15}")
    print("-" * W)
    for name, m in rows:
        print(f"{name:<20}{m['cagr']:>8.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}{m['n']:>8}${m['final']:>13,.0f}")
    print("-" * W)
    print("\n* 단독은 cash drag로 CAGR이 낮음(자본 대부분 현금). B는 NDX100 생존편향 노출.")


if __name__ == '__main__':
    main()
