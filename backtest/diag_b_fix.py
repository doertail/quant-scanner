"""diag_b_fix.py — 제안 diff 검증 (소스 수정 없이 몽키패치로만). 일회용."""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import pandas as pd

import backtest_v4 as bt
import universe

bt.get_nasdaq100_tickers = universe.get_nasdaq100_tickers   # ← 제안 diff와 동일 효과
bt.get_sp500_tickers = universe.get_sp500_tickers

import backtest_improve as improve

t0 = time.time()
ctx = improve.setup()
print(f"setup {time.time()-t0:.0f}s  stock_a={len(ctx['stock_a'])} stock_b={len(ctx['stock_b'])}",
      flush=True)

dates = [d for d in ctx["all_dates"]
         if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]
regime = bt.build_regime_series(ctx["qqq_ohlc"], ctx["breadth"], ctx["vix_cl"],
                                ctx["hyg_cl"], ctx["all_dates"])
r = regime.loc[pd.DatetimeIndex(dates)]
print(f"allow_b True {int(r['allow_b'].sum())}/{len(r)} · BULL {int((r['regime']=='BULL').sum())}",
      flush=True)

improve.apply({})
t1 = time.time()
res = bt.run_backtest(ctx["stock_a"], ctx["stock_b"], dates, regime, ctx["qqqm_df"],
                      etf_data={"SPY": ctx["spy_df"], "QQQ": ctx["qqq_df"]},
                      etf_indicators=ctx["etf_ind"],
                      mom_rank_df=ctx["mom_rank"], mom_rs_df=ctx["mom_rs"])
m = res["main"]
print(f"\n베이스라인 A10/B10  CAGR {m['cagr']:.2f}%  Sharpe {m['sharpe']:.2f}  MDD {m['mdd']:.1f}%")
print(f"거래  A {res['A']['n']}  B {res['B']['n']}  C {res['C']['n']}  D {res['D']['n']}")
print(f"승률  A {res['A']['wr']:.1f}%  B {res['B']['wr']:.1f}%")
print(f"({time.time()-t1:.0f}s, 총 {time.time()-t0:.0f}s)")
