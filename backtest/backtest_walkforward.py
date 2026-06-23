"""
backtest_walkforward.py — 과최적화 워크포워드 검증

파라미터 그리드(A_RSI_BUY × B_ATR_MULT)를 in-sample(2005-2015)에서 평가해
최적 조합을 고른 뒤, out-of-sample(2016-2026)에서 그 조합이 유지되는지 본다.

판정:
  · IS 최적 조합이 OOS에서도 상위면 → 견고(과최적화 아님)
  · IS 최적이 OOS에서 평범/하위면 → 과최적화 (과거에 맞춰진 것)

런타임 파라미터만 패치하므로 고정 regime(bv.run) 사용 가능.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import backtest_v4 as bt
import backtest_validation as bv

A_GRID = [30, 35, 40]
B_GRID = [3.0, 4.0, 5.0]
IS = ("2005-01-01", "2015-12-31")
OOS = ("2016-01-01", "2026-12-31")


def slice_dates(all_dates, s, e):
    s_ts, e_ts = pd.Timestamp(s), pd.Timestamp(e)
    return [d for d in all_dates if s_ts <= d <= e_ts]


def main():
    ctx = bv.setup()
    is_dates = slice_dates(ctx["all_dates"], *IS)
    oos_dates = slice_dates(ctx["all_dates"], *OOS)
    base_a, base_b = bt.A_RSI_BUY, bt.B_ATR_MULT

    grid = []
    for a in A_GRID:
        for b in B_GRID:
            bt.A_RSI_BUY, bt.B_ATR_MULT = a, b
            m_is = bv.run(ctx, is_dates)["main"]
            m_oos = bv.run(ctx, oos_dates)["main"]
            grid.append({"a": a, "b": b,
                         "is_cagr": m_is["cagr"], "is_shp": m_is["sharpe"],
                         "oos_cagr": m_oos["cagr"], "oos_shp": m_oos["sharpe"]})
    bt.A_RSI_BUY, bt.B_ATR_MULT = base_a, base_b

    is_best = max(grid, key=lambda r: r["is_shp"])
    oos_best = max(grid, key=lambda r: r["oos_shp"])
    oos_rank = sorted(grid, key=lambda r: r["oos_shp"], reverse=True)
    is_best_oos_rank = oos_rank.index(is_best) + 1

    W = 86
    print("\n" + "=" * W)
    print("  워크포워드 검증 — IS(2005-2015)에서 최적 → OOS(2016-2026) 유지되나".center(W - 14))
    print("=" * W)
    print(f"{'A_RSI':>6}{'B_ATR':>7}{'  | ':>4}{'IS CAGR':>9}{'IS Shp':>8}{'  | ':>4}{'OOS CAGR':>10}{'OOS Shp':>9}")
    print("-" * W)
    for r in grid:
        mark = ""
        if r is is_best:
            mark += " ◀IS최적"
        if r is oos_best:
            mark += " ★OOS최적"
        print(f"{r['a']:>6}{r['b']:>7.1f}{'  | ':>4}{r['is_cagr']:>8.2f}%{r['is_shp']:>8.2f}"
              f"{'  | ':>4}{r['oos_cagr']:>9.2f}%{r['oos_shp']:>9.2f}{mark}")
    print("-" * W)
    print(f"\nIS 최적 조합: A_RSI={is_best['a']}, B_ATR={is_best['b']}  (IS Sharpe {is_best['is_shp']:.2f})")
    print(f"  → 그 조합의 OOS Sharpe {is_best['oos_shp']:.2f}, OOS {len(grid)}개 중 {is_best_oos_rank}위")
    print(f"OOS 실제 최적: A_RSI={oos_best['a']}, B_ATR={oos_best['b']}  (OOS Sharpe {oos_best['oos_shp']:.2f})")
    verdict = ("견고 — IS 최적이 OOS에서도 상위" if is_best_oos_rank <= 3
               else "⚠️ 과최적화 의심 — IS 최적이 OOS에서 하위")
    print(f"\n판정: {verdict}")
    print("* B는 NDX100 생존편향 노출 — 절대수치보다 IS↔OOS 일관성에 주목.")


if __name__ == "__main__":
    main()
