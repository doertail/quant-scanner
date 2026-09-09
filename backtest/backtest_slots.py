"""
backtest_slots.py — A/B 슬리브 정원(동시보유 상한) 비율 실측 (2005-2026)

config.py의 A_MAX_SLOTS/B_MAX_SLOTS는 거래 수 비율(A 2268 : B 695)을 보유기간으로
눈대중 할인한 값이었다. 근거가 눈대중이라 여기서 직접 잰다. 두 가지를 본다:

  1. **실측 동시보유** — 상한을 안 걸었을 때(넉넉히 두면) A와 B가 실제로 몇 자리를 쓰는가.
     이게 Sharpe 0.95를 만든 원본 믹스다.
  2. **상한 스윕** — (A_MAX_POS, B_MAX_POS) 격자에서 Sharpe/MDD가 어디서 최적인가.
     상한이 실측 동시보유보다 크면 안 물린다(=결과가 안 바뀐다). 그 경계도 같이 보인다.

⚠️ 절대 슬롯 수는 $100k · RISK_PER_TRADE 1% · MAX_CAP 15% 기준이다. 슬롯당 금액이
   $288인 소액 계좌에 숫자를 그대로 옮기면 안 된다. 옮길 수 있는 건 **A:B 비율**이다.

⚠️ 엔진은 하루 안에서 A를 먼저 채우고 남은 현금으로 B를 산다(backtest_v4 L644 → L678).
   상한을 풀면(50/50) A가 현금을 다 먹어 **B 거래가 0건**이 된다. 정원은 배분 취향이 아니라
   B가 자본을 받는 유일한 경로다.

⚠️ B(NDX100)는 생존편향 슬리브다(BACKTESTS.md §10). B에 유리한 결과는 할인해서 읽어야
   하는데, 편향은 B 슬롯을 늘리는 쪽으로 작용하므로 **B 상한이 작게 나오면 그건 신뢰**할 수
   있다(편향을 거스르는 방향의 결과다). 반대로 B가 크게 나오면 의심해야 한다.

사용:
    python backtest_slots.py                # 전체 격자
    python backtest_slots.py --probe        # 1칸만 (시간 측정용)
    python backtest_slots.py --a 4,6,8 --b 1,2,3
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import backtest_improve as improve
import backtest_v4 as bt

A_GRID = [2, 3, 4, 6, 8, 10, 15]
B_GRID = [1, 2, 3, 5, 10]


def concurrency(stats: dict, span_days: int) -> float:
    """평균 동시보유 자리 수 = Σ보유일 / 전체기간.

    거래별 보유일 합을 기간으로 나눈 값. n건 × 평균보유일로 근사한다(엔진이 평균만 준다).
    A와 B에 같은 공식을 쓰므로 **비율**은 근사와 무관하게 정확하다.
    """
    if not span_days:
        return 0.0
    return stats.get("n", 0) * stats.get("avg_hold_days", 0) / span_days


def _fmt(a, b, m, ca, cb):
    return (f"{a:>3} {b:>3} │{m['cagr']:>7.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}"
            f"{m['calmar']:>8.2f}│{ca:>7.1f}{cb:>7.1f}")


def main():
    ap = argparse.ArgumentParser(description="A/B 슬리브 정원 비율 실측")
    ap.add_argument("--probe", action="store_true", help="1칸만 돌려 시간 측정")
    ap.add_argument("--a", help="A 상한 목록 (쉼표)")
    ap.add_argument("--b", help="B 상한 목록 (쉼표)")
    args = ap.parse_args()

    a_grid = [int(x) for x in args.a.split(",")] if args.a else A_GRID
    b_grid = [int(x) for x in args.b.split(",")] if args.b else B_GRID
    if args.probe:
        a_grid, b_grid = [10], [10]

    t0 = time.time()
    ctx = improve.setup()
    dates = [d for d in ctx["all_dates"]
             if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]
    span = (dates[-1] - dates[0]).days
    # 국면은 슬롯 상한과 무관하다(VIX 밴드를 안 건드림) → 격자마다 재계산하지 않고 1회.
    # backtest_improve가 매번 재계산하는 건 VIX_PANIC 같은 걸 패치하기 때문이다.
    regime = bt.build_regime_series(ctx["qqq_ohlc"], ctx["breadth"], ctx["vix_cl"],
                                    ctx["hyg_cl"], ctx["all_dates"])
    print(f"준비 완료 {time.time()-t0:.0f}s · 거래일 {len(dates):,} · 기간 {span:,}일 "
          f"({dates[0].date()} ~ {dates[-1].date()})", flush=True)

    def run(a, b):
        improve.apply(dict(A_MAX_POS=a, B_MAX_POS=b))
        return bt.run_backtest(ctx["stock_a"], ctx["stock_b"], dates, regime, ctx["qqqm_df"],
                               etf_data={"SPY": ctx["spy_df"], "QQQ": ctx["qqq_df"]},
                               etf_indicators=ctx["etf_ind"],
                               mom_rank_df=ctx["mom_rank"], mom_rs_df=ctx["mom_rs"])

    # ── 1. 실측 동시보유 (출하 기본값 10/10 = Sharpe 0.95를 만든 설정) ────────
    # 상한을 풀면(50/50) B 거래가 **0건**이 된다 — 엔진이 하루 안에서 A를 먼저 채우고
    # 남은 현금으로 B를 사기 때문(backtest_v4 L644 A → L678 B). 즉 정원은 취향이 아니라
    # B가 자본을 받는 **유일한 경로**다. 그래서 "무제한" 측정은 의미가 없고 기본값을 잰다.
    t1 = time.time()
    r = run(10, 10)
    dt = time.time() - t1
    ca, cb = concurrency(r["A"], span), concurrency(r["B"], span)
    print(f"\n{'='*72}\n  1. 실측 동시보유 — 출하 기본값 A10/B10 (Sharpe 0.95 설정)\n{'='*72}")
    print(f"  A  {r['A']['n']:>5}건 × 평균 {r['A']['avg_hold_days']:>4}일 보유 → 동시 {ca:>5.1f}자리")
    print(f"  B  {r['B']['n']:>5}건 × 평균 {r['B']['avg_hold_days']:>4}일 보유 → 동시 {cb:>5.1f}자리")
    print(f"  → 실측 A:B = {ca/cb:.2f} : 1" if cb else "  → B 거래 없음")
    print(f"  (1회 {dt:.0f}s)", flush=True)

    if args.probe:
        print(f"\n격자 {len(A_GRID)}×{len(B_GRID)} = {len(A_GRID)*len(B_GRID)}칸 "
              f"→ 예상 {dt*len(A_GRID)*len(B_GRID)/60:.0f}분")
        return 0

    # ── 2. 상한 스윕 ──────────────────────────────────────────────────────────
    print(f"\n{'='*72}\n  2. 상한 스윕 — {len(a_grid)}×{len(b_grid)} = {len(a_grid)*len(b_grid)}칸\n{'='*72}")
    print(f"{'A':>3} {'B':>3} │{'CAGR':>8}{'MDD':>8}{'Sharpe':>8}{'Calmar':>8}│{'실A':>7}{'실B':>7}")
    print("-" * 72)
    rows = []
    for a in a_grid:
        for b in b_grid:
            res = run(a, b)
            m = res["main"]
            ca, cb = concurrency(res["A"], span), concurrency(res["B"], span)
            rows.append((a, b, m, ca, cb))
            print(_fmt(a, b, m, ca, cb), flush=True)
    improve.apply({})

    best_s = max(rows, key=lambda x: x[2]["sharpe"])
    best_c = max(rows, key=lambda x: x[2]["calmar"])
    print("-" * 72)
    print(f"  Sharpe 최적: A {best_s[0]} / B {best_s[1]} → {best_s[2]['sharpe']:.2f} "
          f"(CAGR {best_s[2]['cagr']:.2f}%, MDD {best_s[2]['mdd']:.1f}%) · 비율 {best_s[0]/best_s[1]:.1f}:1")
    print(f"  Calmar 최적: A {best_c[0]} / B {best_c[1]} → {best_c[2]['calmar']:.2f} · "
          f"비율 {best_c[0]/best_c[1]:.1f}:1")
    print("\n* 실A/실B = 그 설정에서의 실측 동시보유. 상한보다 작으면 상한이 안 물린 것(무효 셀).")
    print("* 절대 슬롯 수는 $100k 기준 — 소액 계좌엔 비율만 옮긴다.")
    print(f"* 총 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
