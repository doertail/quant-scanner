"""
backtest_voltarget.py — 변동성 타게팅/레버리지로 Sharpe 엣지를 수익으로 전환

전략은 시장(SPY+QQQ)보다 변동성·낙폭이 작다. '같은 위험'까지 레버리지하면
같은 위험에 더 높은 수익을 낼 수 있다(Sharpe 우위의 monetize). 이를 검증한다.

비교군 (전체 2005-2026, 차입비용 = 무위험 3.5% + 스프레드 1.5%):
  1) 전략 1x (무레버)
  2) 전략 정적레버 — 시장 변동성에 매칭(L = mkt_vol / strat_vol)
  3) 전략 변동성 타게팅 — 60일 실현변동성으로 일별 레버 조절(룩어헤드 없음), 상한 3x
  4) 전략 정적레버 — 시장 낙폭(MDD)에 매칭
  5) SPY+QQQ 바이앤홀드 (시장)

⚠️ 절대수익은 NDX100 생존편향 상속(레버리지가 거품도 확대). 차입이자/마진콜/갭은
미반영 → 백테스트가 레버리지 꼬리위험을 과소평가함. '같은 위험에 더 버나'의 방향성만.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import backtest_v4 as bt
import backtest_validation as bv

RF = bt.RISK_FREE_RATE      # 0.035
SPREAD = 0.015              # 차입 스프레드 가정
LMAX = 3.0                  # 레버리지 상한
FIN_DAILY = (RF + SPREAD) / 252.0
INIT = bt.INITIAL_CASH


def rets_of(equity):
    return pd.Series(equity).pct_change().fillna(0.0)


def lever_static(r: pd.Series, L: float) -> list:
    lev = L * r - max(0.0, L - 1.0) * FIN_DAILY
    return ((1 + lev).cumprod() * INIT).tolist()


def vol_target(r: pd.Series, target_ann: float, lookback=60) -> tuple:
    vol = r.rolling(lookback).std() * np.sqrt(252)
    L = (target_ann / vol).clip(upper=LMAX).shift(1).fillna(1.0).clip(lower=0.0)
    lev = L * r - (L - 1).clip(lower=0) * FIN_DAILY
    eq = ((1 + lev).cumprod() * INIT).tolist()
    return eq, L


def stats(equity):
    return bt.calc_stats(equity, INIT, [])


def main():
    ctx = bv.setup()
    full = [d for d in ctx["all_dates"]
            if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]
    res = bv.run(ctx, full)
    strat_eq = res["equity"]

    spy = ctx["spy_df"]["Close"].reindex(full, method="ffill")
    qqq = ctx["qqq_df"]["Close"].reindex(full, method="ffill")
    half = INIT / 2.0
    bnh_eq = (half / spy.iloc[0] * spy + half / qqq.iloc[0] * qqq).tolist()

    sr, br = rets_of(strat_eq), rets_of(bnh_eq)
    strat_vol = sr.std() * np.sqrt(252)
    bnh_vol = br.std() * np.sqrt(252)

    s1 = stats(strat_eq)
    bh = stats(bnh_eq)

    # 정적레버 — 변동성 매칭
    L_vol = bnh_vol / strat_vol if strat_vol > 0 else 1.0
    sv_eq = lever_static(sr, L_vol)
    sv = stats(sv_eq)

    # 변동성 타게팅 — 시장 변동성을 타겟
    vt_eq, Lser = vol_target(sr, bnh_vol)
    vt = stats(vt_eq)
    L_avg = float(Lser.mean())

    # 정적레버 — 낙폭(MDD) 매칭 (시장만큼 깨질 때까지 레버 ↑)
    L_mdd = 1.0
    for L in np.arange(1.0, LMAX + 0.001, 0.1):
        if stats(lever_static(sr, L))["mdd"] <= bh["mdd"]:
            L_mdd = round(float(L), 1)
            break
    else:
        L_mdd = LMAX
    sm_eq = lever_static(sr, L_mdd)
    sm = stats(sm_eq)

    W = 92
    print("\n" + "=" * W)
    print("  변동성 타게팅/레버리지 — '같은 위험에 더 버나' (전체 2005-2026)".center(W - 12))
    print("=" * W)
    print(f"{'구성':<34}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'변동성':>9}{'레버':>8}")
    print("-" * W)
    print(f"{'전략 1x (무레버)':<34}{s1['cagr']:>8.2f}%{s1['mdd']:>8.1f}%{s1['sharpe']:>8.2f}{strat_vol*100:>8.1f}%{'1.0x':>8}")
    print(f"{'전략 정적레버 (변동성 매칭)':<34}{sv['cagr']:>8.2f}%{sv['mdd']:>8.1f}%{sv['sharpe']:>8.2f}{strat_vol*L_vol*100:>8.1f}%{f'{L_vol:.1f}x':>8}")
    print(f"{'전략 변동성타게팅 (동적)':<34}{vt['cagr']:>8.2f}%{vt['mdd']:>8.1f}%{vt['sharpe']:>8.2f}{'~target':>9}{f'~{L_avg:.1f}x':>8}")
    print(f"{'전략 정적레버 (낙폭 매칭)':<34}{sm['cagr']:>8.2f}%{sm['mdd']:>8.1f}%{sm['sharpe']:>8.2f}{strat_vol*L_mdd*100:>8.1f}%{f'{L_mdd:.1f}x':>8}")
    print("-" * W)
    print(f"{'SPY+QQQ 바이앤홀드 (시장)':<34}{bh['cagr']:>8.2f}%{bh['mdd']:>8.1f}%{bh['sharpe']:>8.2f}{bnh_vol*100:>8.1f}%{'1.0x':>8}")
    print("-" * W)

    win = sm["cagr"] - bh["cagr"]
    print(f"\n핵심: 시장과 '같은 낙폭'({bh['mdd']:.0f}%)까지 레버({L_mdd}x) 시 "
          f"전략 CAGR {sm['cagr']:.1f}% vs 시장 {bh['cagr']:.1f}%  →  {win:+.1f}%p")
    print("⚠️ 절대수치는 생존편향 상속 + 차입이자/마진콜/갭 미반영. 방향성(같은 위험에 초과수익) 확인용.")
    print("⚠️ 실제 검증은 forward. 레버리지는 꼬리위험을 키우니 실거래는 보수적으로.")


if __name__ == "__main__":
    main()
