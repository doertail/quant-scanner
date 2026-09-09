"""diag_b.py — B 슬리브 0건 원인 계측 (일회용, 읽기 전용)"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import backtest_improve as improve
import backtest_v4 as bt

t0 = time.time()
ctx = improve.setup()
print(f"setup {time.time()-t0:.0f}s", flush=True)

all_dates = ctx["all_dates"]
dates = [d for d in all_dates if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]
print(f"stock_a={len(ctx['stock_a'])} stock_b={len(ctx['stock_b'])} "
      f"all_dates={len(all_dates)} dates(2005+)={len(dates)} "
      f"{dates[0].date()}~{dates[-1].date()}", flush=True)

regime = bt.build_regime_series(ctx["qqq_ohlc"], ctx["breadth"], ctx["vix_cl"],
                                ctx["hyg_cl"], all_dates)
r = regime.loc[pd.DatetimeIndex(dates)]
print("\n=== 1. REGIME 분포 (2005+) ===")
print(r["regime"].value_counts(dropna=False).to_string())
print("\n=== vix_zone 분포 ===")
print(r["vix_zone"].value_counts(dropna=False).to_string())
print(f"\nallow_a True: {int(r['allow_a'].sum())} / {len(r)}")
print(f"allow_b True: {int(r['allow_b'].sum())} / {len(r)}")
print(f"BULL 일수:    {int((r['regime']=='BULL').sum())}")
print(f"vix NaN 일수: {int(r['vix'].isna().sum())}")
print(f"breadth NaN:  {int(ctx['breadth'].reindex(pd.DatetimeIndex(dates), method='ffill').isna().sum())}")
print(f"hyg NaN:      {int(ctx['hyg_cl'].reindex(pd.DatetimeIndex(dates), method='ffill').isna().sum())}")

# allow_b 분해
vix_s = r["vix"]
zone = r["vix_zone"]
hyg_s = ctx["hyg_cl"].reindex(pd.DatetimeIndex(dates), method="ffill")
hyg_ma = ctx["hyg_cl"].rolling(bt.HYG_MA_PERIOD).mean().reindex(pd.DatetimeIndex(dates), method="ffill")
print(f"\nDANGER 차단:      {int((zone=='DANGER').sum())}")
print(f"PANIC 차단:       {int((zone=='PANIC').sum())}")
print(f"SWEET&HYG약 차단: {int(((zone=='SWEET') & ~(hyg_s>hyg_ma)).sum())}")
print(f"non-BULL 차단:    {int((r['regime']!='BULL').sum())}")

if int(r["allow_b"].sum()) == 0:
    print("\n>>> allow_b가 0일. 여기가 원인. 게이트 분석 생략.", flush=True)
    sys.exit(0)

# === 2. B 종목 게이트 퍼널 (allow_b True 인 날만) ===
allow_b_dates = pd.DatetimeIndex([d for d in dates if bool(regime.at[d, "allow_b"])])
sb = ctx["stock_b"]
close_df = pd.DataFrame({t: df["Close"] for t, df in sb.items()})
ma20_df = pd.DataFrame({t: df["MA20"] for t, df in sb.items()})
ma200_df = pd.DataFrame({t: df["MA200"] for t, df in sb.items()})
rank = ctx["mom_rank"]
rs = ctx["mom_rs"]

idx = allow_b_dates
missing_rank_idx = len(idx.difference(rank.index))
print(f"\n=== 2. B 게이트 퍼널 (allow_b True {len(idx)}일) ===")
print(f"mom_rank 인덱스에 없는 allow_b 날: {missing_rank_idx}")

idx = idx.intersection(rank.index).intersection(close_df.index)
c = close_df.reindex(idx)
m20 = ma20_df.reindex(idx)
m200 = ma200_df.reindex(idx)
rk = rank.reindex(index=idx, columns=close_df.columns)
rsv = rs.reindex(index=idx, columns=close_df.columns)

g0 = c.notna()
g1 = g0 & (c > m200)
g2 = g1 & (c > m20)
g3 = g2 & rk.notna() & rsv.notna()
g4 = g3 & (rk >= (1.0 - bt.B_RANK_TOP))
g5 = g4 & (rsv > 0)

for name, g in [("가격 존재", g0), ("+ Close>MA200", g1), ("+ Close>MA20", g2),
                ("+ rank/rs NaN아님", g3), (f"+ rank>={1-bt.B_RANK_TOP:.2f}", g4),
                ("+ rs>0 (최종)", g5)]:
    per_day = g.sum(axis=1)
    print(f"  {name:<22} 종목-일 합계 {int(per_day.sum()):>9,}  "
          f"후보>=1인 날 {int((per_day>0).sum()):>6,}/{len(idx):,}  "
          f"일평균 {per_day.mean():.1f}")

print(f"\nrank 전체 NaN 비율: {rk.isna().mean().mean():.3f}")
print(f"rs   전체 NaN 비율: {rsv.isna().mean().mean():.3f}")
print(f"rank max/일 평균:   {rk.max(axis=1).mean():.3f}")

# === 3. 두 하네스 동등성 + 베이스라인 재현 ===
print("\n=== 3. 베이스라인 재현 (A10/B10) ===", flush=True)
improve.apply({})
t1 = time.time()
res_slots = bt.run_backtest(ctx["stock_a"], ctx["stock_b"], dates, regime, ctx["qqqm_df"],
                            etf_data={"SPY": ctx["spy_df"], "QQQ": ctx["qqq_df"]},
                            etf_indicators=ctx["etf_ind"],
                            mom_rank_df=ctx["mom_rank"], mom_rs_df=ctx["mom_rs"])
m = res_slots["main"]
print(f"slots 경로(regime 사전계산): CAGR {m['cagr']:.2f}% Sharpe {m['sharpe']:.2f} "
      f"MDD {m['mdd']:.1f}% | A {res_slots['A']['n']} B {res_slots['B']['n']} "
      f"C {res_slots['C']['n']} D {res_slots['D']['n']}  ({time.time()-t1:.0f}s)", flush=True)

t1 = time.time()
m2 = improve.run_cfg(ctx, dates)
print(f"improve 경로(regime 재계산):  CAGR {m2['cagr']:.2f}% Sharpe {m2['sharpe']:.2f} "
      f"MDD {m2['mdd']:.1f}%  ({time.time()-t1:.0f}s)", flush=True)
print(f"\n총 {time.time()-t0:.0f}s")
