"""
backtest_regime_routing.py — 레짐 라우팅 검증 (방향 B)

거시 ablation 결론: 전략 A(평균회귀)는 BULL에서 약하고 BEAR/SIDEWAYS에서 강하다.
현재는 A를 BULL 포함 거의 전구간 허용(allow_a). 라우팅 = A를 BULL에서 빼서
(자본을 BULL 엔진인 B로 돌리고) A는 비-BULL에 집중시킨다.

비교 (전체 2005-2026):
  1) 베이스라인 — 현행 allow_a (BULL 포함)
  2) 라우팅 — allow_a &= (regime != BULL)  (A를 BULL에서 제외)
  3) 라우팅 + A슬롯↑ — 위 + A_MAX_POS 10→15 (비-BULL에서 A 더 배치)
  4) 시장 (SPY+QQQ B&H)

판정: Sharpe·MDD가 베이스라인 대비 개선되면 라우팅이 구조적 엣지.
주의: B는 NDX100 생존편향 노출 — 절대수치보다 베이스라인 대비 '개선폭'에 주목.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import backtest_v4 as bt
import backtest_validation as bv


def bnh(ctx, dates):
    spy = ctx["spy_df"]["Close"].reindex(dates, method="ffill")
    qqq = ctx["qqq_df"]["Close"].reindex(dates, method="ffill")
    half = bt.INITIAL_CASH / 2.0
    eq = (half / spy.iloc[0] * spy + half / qqq.iloc[0] * qqq).tolist()
    return bt.calc_stats(eq, bt.INITIAL_CASH, [])


def main():
    ctx = bv.setup()
    full = [d for d in ctx["all_dates"]
            if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]

    # 1) 베이스라인
    base = bv.run(ctx, full)["main"]

    # 2) 라우팅: A를 BULL에서 제외
    routed = ctx["regime"].copy()
    routed["allow_a"] = routed["allow_a"] & (routed["regime"] != "BULL")
    ctx_r = {**ctx, "regime": routed}
    route = bv.run(ctx_r, full)["main"]

    # 3) 라우팅 + A 슬롯 확대
    saved = bt.A_MAX_POS
    bt.A_MAX_POS = 15
    route_more = bv.run(ctx_r, full)["main"]
    bt.A_MAX_POS = saved

    mkt = bnh(ctx, full)

    rows = [
        ("1.베이스라인 (현행)", base),
        ("2.라우팅 (A를 BULL제외)", route),
        ("3.라우팅 + A슬롯15", route_more),
        ("시장 (SPY+QQQ)", mkt),
    ]
    W = 86
    print("\n" + "=" * W)
    print("  레짐 라우팅 검증 — A를 BULL에서 빼면? (전체 2005-2026)".center(W - 12))
    print("=" * W)
    print(f"{'구성':<26}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'최종$':>15}{'ΔSharpe':>9}")
    print("-" * W)
    for name, m in rows:
        ds = m["sharpe"] - base["sharpe"] if m is not mkt else m["sharpe"] - base["sharpe"]
        dtxt = f"{ds:+.2f}" if name != "1.베이스라인 (현행)" else "—"
        print(f"{name:<26}{m['cagr']:>8.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}${m['final']:>13,.0f}{dtxt:>9}")
    print("-" * W)
    best = max([route, route_more], key=lambda m: m["sharpe"])
    verdict = ("✅ 라우팅이 개선 (Sharpe↑)" if best["sharpe"] > base["sharpe"] + 0.01
               else "❌ 라우팅 효과 없음/악화 — 현행 유지")
    print(f"\n판정: {verdict}")
    print("* B는 생존편향 노출 — 베이스라인 대비 개선폭으로 판단. 실제 검증은 forward.")


if __name__ == "__main__":
    main()
