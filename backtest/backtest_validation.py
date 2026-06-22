"""
backtest_validation.py — 풀시스템 전략 종합 검증 (생존편향 분리 + 과최적화 점검)

backtest_v4 엔진을 재사용. 데이터/지표/국면/모멘텀을 1회 계산 후 여러 설정으로 재실행.

분석:
  1) 전략별 기여도 (A/B/C/D 트레이드 통계 + DCA)
  2) 전략 A on/off × 5개 기간 — 평균회귀(생존편향에 가장 취약)를 빼면
     초과수익이 얼마나 남는지 = 생존편향 거품의 하한 추정
  3) RSI 진입 임계값 민감도 (30/35/40) — 결과가 특정 파라미터에 칼날처럼
     의존하는지(과최적화) 점검

주의: 유니버스 = 현재 지수 구성종목 → 과거 구간 생존편향 존재(BACKTESTS.md caveat).
A on/off 비교가 그 영향을 부분적으로 정량화한다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import yfinance as yf

import backtest_v4 as bt

WINDOWS = [
    ("2005-2010 GFC",     "2005-01-01", "2010-12-31"),
    ("2011-2015 회복",     "2011-01-01", "2015-12-31"),
    ("2016-2019 말기불장",  "2016-01-01", "2019-12-31"),
    ("2020-2026 코로나~",   "2020-01-01", "2026-12-31"),
    ("전체 2005-2026",     "2005-01-01", "2026-12-31"),
]


def bnh(spy_c, qqq_c, dates, initial):
    s = spy_c.reindex(dates, method="ffill").dropna()
    q = qqq_c.reindex(dates, method="ffill").dropna()
    idx = s.index.intersection(q.index)
    s, q = s.loc[idx], q.loc[idx]
    if len(idx) < 2:
        return None
    half = initial / 2.0
    eq = (half / s.iloc[0] * s + half / q.iloc[0] * q).tolist()
    return bt.calc_stats(eq, initial, [])


def setup():
    bt.log.info("티커/데이터/지표 준비 (1회)...")
    sp500 = bt.get_sp500_tickers()
    ndx100 = bt.get_nasdaq100_tickers()
    base = list(set(sp500 + ndx100 + ["QQQ", "SPY", "HYG", "QQQM"]))
    raw = yf.download(base, start=bt.START, end=bt.END,
                      group_by="ticker", threads=True, progress=False)
    vix_raw = yf.download("^VIX", start=bt.START, end=bt.END,
                          progress=False, multi_level_index=False)
    stock_a = bt.build_stock_data(raw, sp500)
    stock_b = bt.build_stock_data(raw, ndx100)
    qqq_ohlc = raw["QQQ"][["High", "Low", "Close"]].dropna()
    hyg_cl = raw["HYG"]["Close"].dropna()
    spy_df = raw["SPY"][["Close"]].dropna()
    qqq_df = raw["QQQ"][["Close"]].dropna()
    qqqm_df = raw["QQQM"][["Close"]].dropna()
    vix_cl = vix_raw["Close"].dropna()
    etf_ind = {}
    for t in ["SPY", "QQQ"]:
        d = raw[t][["Open", "High", "Low", "Close", "Volume"]].copy().dropna(subset=["Close"])
        etf_ind[t] = bt.compute_indicators(d)
    all_dates = sorted(set(d for sd in (stock_a, stock_b)
                           for df in sd.values() for d in df.index))
    breadth = bt.precompute_breadth(stock_a)
    mom_rank, mom_rs = bt.precompute_momentum_ranks(stock_b, qqq_df)
    regime = bt.build_regime_series(qqq_ohlc, breadth, vix_cl, hyg_cl, all_dates)
    return dict(stock_a=stock_a, stock_b=stock_b, qqqm_df=qqqm_df, spy_df=spy_df,
                qqq_df=qqq_df, etf_ind=etf_ind, mom_rank=mom_rank, mom_rs=mom_rs,
                regime=regime, all_dates=all_dates)


def run(ctx, win_dates):
    return bt.run_backtest(
        ctx["stock_a"], ctx["stock_b"], win_dates, ctx["regime"], ctx["qqqm_df"],
        etf_data={"SPY": ctx["spy_df"], "QQQ": ctx["qqq_df"]},
        etf_indicators=ctx["etf_ind"], mom_rank_df=ctx["mom_rank"], mom_rs_df=ctx["mom_rs"],
    )


def win_dates(ctx, s, e):
    s_ts, e_ts = pd.Timestamp(s), pd.Timestamp(e)
    return [d for d in ctx["all_dates"] if s_ts <= d <= e_ts]


def main():
    ctx = setup()
    init = bt.INITIAL_CASH
    full = win_dates(ctx, "2005-01-01", "2026-12-31")
    W = 100

    # ── 분석 1: 전략별 기여도 (전체 기간) ─────────────────────────────────
    res_full = run(ctx, full)
    print("\n" + "=" * W)
    print("  [1] 전략별 트레이드 기여도 (전체 2005-2026)".center(W - 10))
    print("=" * W)
    print(f"{'전략':<26}{'트레이드':>8}{'승률':>8}{'평균수익/건':>12}{'평균보유일':>10}")
    print("-" * W)
    names = {"A": "방패A 평균회귀", "B": "창B 모멘텀", "C": "지수C VIX패닉", "D": "크립토D 모멘텀"}
    for k in ["A", "B", "C", "D"]:
        s = res_full[k]
        print(f"{names[k]:<26}{s.get('n',0):>8}{s.get('wr',0):>7.1f}%{s.get('avg_pnl_pct',0):>11.2f}%{s.get('avg_hold_days',0):>10}")
    dca = res_full["DCA"]
    print(f"{'QQQM DCA(매일적립)':<26}{'-':>8}{'-':>8}{dca['ret']:>11.1f}%{'-':>10}  (투입 ${dca['invested']:,.0f}→${dca['final']:,.0f})")

    # ── 분석 2: 전략 A on/off × 기간 (생존편향 분리) ──────────────────────
    print("\n" + "=" * W)
    print("  [2] 전략 A(평균회귀) ON vs OFF — 생존편향 거품 분리".center(W - 6))
    print("=" * W)
    print(f"{'기간':<18}{'설정':<12}{'CAGR':>8}{'MDD':>9}{'Sharpe':>8}{'최종$':>13}")
    print("-" * W)
    saved_max = bt.A_MAX_POS
    for label, s, e in WINDOWS:
        wd = win_dates(ctx, s, e)
        if len(wd) < 60:
            continue
        bt.A_MAX_POS = saved_max
        on = run(ctx, wd)["main"]
        bt.A_MAX_POS = 0  # A 진입 차단
        off = run(ctx, wd)["main"]
        bt.A_MAX_POS = saved_max
        bh = bnh(ctx["spy_df"]["Close"], ctx["qqq_df"]["Close"], wd, init)
        print(f"{label:<18}{'A포함(전체)':<12}{on['cagr']:>7.2f}%{on['mdd']:>8.1f}%{on['sharpe']:>8.2f}${on['final']:>11,.0f}")
        print(f"{'':<18}{'A제외(B+C+D)':<12}{off['cagr']:>7.2f}%{off['mdd']:>8.1f}%{off['sharpe']:>8.2f}${off['final']:>11,.0f}")
        print(f"{'':<18}{'SPY+QQQ B&H':<12}{bh['cagr']:>7.2f}%{bh['mdd']:>8.1f}%{bh['sharpe']:>8.2f}${bh['final']:>11,.0f}")
        a_contrib = on['cagr'] - off['cagr']
        print(f"{'':<18}{'→ A기여 CAGR':<12}{a_contrib:>+7.2f}%  (이 중 상당부분이 생존편향 거품일 수 있음)")
        print("-" * W)

    # ── 분석 3: RSI 임계값 민감도 (전체 기간) ─────────────────────────────
    print("\n" + "=" * W)
    print("  [3] 전략A RSI 진입 임계값 민감도 (전체 기간) — 과최적화 점검".center(W - 6))
    print("=" * W)
    print(f"{'A_RSI_BUY':<12}{'CAGR':>8}{'MDD':>9}{'Sharpe':>8}{'A트레이드':>10}{'최종$':>14}")
    print("-" * W)
    saved_rsi = bt.A_RSI_BUY
    for thr in [30, 35, 40]:
        bt.A_RSI_BUY = thr
        r = run(ctx, full)
        m = r["main"]
        print(f"{f'RSI < {thr}':<12}{m['cagr']:>7.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}{r['A'].get('n',0):>10}${m['final']:>12,.0f}")
    bt.A_RSI_BUY = saved_rsi
    print("-" * W)
    print("\n* 결과가 임계값에 따라 칼날처럼 변하면 과최적화 신호. 완만하면 견고.")


if __name__ == "__main__":
    main()
