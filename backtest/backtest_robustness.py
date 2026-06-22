"""
backtest_robustness.py — 풀시스템 전략의 기간별(케이스별) 로버스트니스 검증

backtest_v4 의 셋업/엔진을 재사용한다. 데이터·지표·국면·모멘텀랭크를 전체
기간(2005~현재)에 대해 한 번만 계산한 뒤, 하위 기간 윈도우마다 run_backtest 를
다시 돌려 SPY+QQQ 바이앤홀드와 비교한다.

목적: "특정 시대(예: 2020 이후)에만 잘 되는 과최적화 전략인가, 아니면 여러 국면에서
일관되게 작동하는가?" 를 본다. 전략과 B&H 모두 동일한 calc_stats 공식으로 평가.

주의: 유니버스는 *현재* S&P500/NDX100 구성종목 → 과거 구간엔 생존편향 있음
(상폐/편출 종목 누락). BACKTESTS.md 의 공통 caveat과 동일.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import yfinance as yf

import backtest_v4 as bt

# (라벨, 시작, 종료) — 서로 다른 거시 국면을 포함하도록 분할
WINDOWS = [
    ("2005-2010 GFC 포함",      "2005-01-01", "2010-12-31"),
    ("2011-2015 회복 불장",      "2011-01-01", "2015-12-31"),
    ("2016-2019 말기 불장",      "2016-01-01", "2019-12-31"),
    ("2020-2026 코로나~최근",    "2020-01-01", "2026-12-31"),
    ("전체 2005-2026",          "2005-01-01", "2026-12-31"),
]


def bnh_equity(spy_close: pd.Series, qqq_close: pd.Series, dates, initial: float):
    """SPY+QQQ 50:50 바이앤홀드 자산 시계열 (윈도우 시작일 매수)."""
    spy_w = spy_close.reindex(dates, method="ffill").dropna()
    qqq_w = qqq_close.reindex(dates, method="ffill").dropna()
    common = spy_w.index.intersection(qqq_w.index)
    spy_w, qqq_w = spy_w.loc[common], qqq_w.loc[common]
    if len(common) < 2:
        return []
    half = initial / 2.0
    sh_spy = half / spy_w.iloc[0]
    sh_qqq = half / qqq_w.iloc[0]
    return (sh_spy * spy_w + sh_qqq * qqq_w).tolist()


def main():
    bt.log.info("티커 수집...")
    sp500 = bt.get_sp500_tickers()
    ndx100 = bt.get_nasdaq100_tickers()
    base = list(set(sp500 + ndx100 + ["QQQ", "SPY", "HYG", "QQQM"]))

    bt.log.info(f"데이터 다운로드... ({bt.START} ~ {bt.END}, {len(base)}개 — 수 분 소요)")
    raw = yf.download(base, start=bt.START, end=bt.END,
                      group_by="ticker", threads=True, progress=False)
    vix_raw = yf.download("^VIX", start=bt.START, end=bt.END,
                          progress=False, multi_level_index=False)

    bt.log.info("지표/국면/모멘텀 사전계산 (전체 기간 1회)...")
    stock_a = bt.build_stock_data(raw, sp500)
    stock_b = bt.build_stock_data(raw, ndx100)
    qqq_ohlc = raw["QQQ"][["High", "Low", "Close"]].dropna()
    hyg_cl = raw["HYG"]["Close"].dropna()
    spy_df = raw["SPY"][["Close"]].dropna()
    qqq_df = raw["QQQ"][["Close"]].dropna()
    qqqm_df = raw["QQQM"][["Close"]].dropna()
    vix_cl = vix_raw["Close"].dropna()

    etf_indicators = {}
    for t in ["SPY", "QQQ"]:
        d = raw[t][["Open", "High", "Low", "Close", "Volume"]].copy().dropna(subset=["Close"])
        etf_indicators[t] = bt.compute_indicators(d)

    all_dates = sorted(set(d for sd in (stock_a, stock_b)
                           for df in sd.values() for d in df.index))
    breadth = bt.precompute_breadth(stock_a)
    mom_rank_df, mom_rs_df = bt.precompute_momentum_ranks(stock_b, qqq_df)
    regime_df = bt.build_regime_series(qqq_ohlc, breadth, vix_cl, hyg_cl, all_dates)

    spy_close = spy_df["Close"]
    qqq_close = qqq_df["Close"]
    init = bt.INITIAL_CASH

    rows = []
    for label, s, e in WINDOWS:
        s_ts, e_ts = pd.Timestamp(s), pd.Timestamp(e)
        win_dates = [d for d in all_dates if s_ts <= d <= e_ts]
        if len(win_dates) < 60:
            bt.log.warning(f"{label}: 거래일 부족({len(win_dates)}) — 건너뜀")
            continue
        bt.log.info(f"▶ {label}  ({win_dates[0].date()}~{win_dates[-1].date()}, {len(win_dates)}일)")

        res = bt.run_backtest(stock_a, stock_b, win_dates, regime_df, qqqm_df,
                              etf_data={"SPY": spy_df, "QQQ": qqq_df},
                              etf_indicators=etf_indicators,
                              mom_rank_df=mom_rank_df, mom_rs_df=mom_rs_df)
        m = res["main"]
        bnh = bt.calc_stats(bnh_equity(spy_close, qqq_close, win_dates, init), init, [])
        rows.append((label, m, bnh))

    # ── 결과 표 ──────────────────────────────────────────────────────────
    W = 96
    print("\n" + "=" * W)
    print("  📊 기간별 로버스트니스 — 전략(A+B+C+D+DCA) vs SPY+QQQ 바이앤홀드".center(W - 10))
    print("=" * W)
    hdr = f"{'기간':<22}{'주체':<8}{'CAGR':>8}{'MDD':>9}{'Sharpe':>8}{'Calmar':>8}{'최종$':>13}{'거래':>6}"
    print(hdr)
    print("-" * W)
    for label, m, bnh in rows:
        print(f"{label:<22}{'전략':<8}{m['cagr']:>7.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}{m['calmar']:>8.2f}${m['final']:>11,.0f}{m['n']:>6}")
        print(f"{'':<22}{'B&H':<8}{bnh['cagr']:>7.2f}%{bnh['mdd']:>8.1f}%{bnh['sharpe']:>8.2f}{bnh['calmar']:>8.2f}${bnh['final']:>11,.0f}{'-':>6}")
        d_cagr = m['cagr'] - bnh['cagr']
        d_shrp = m['sharpe'] - bnh['sharpe']
        verdict = "✅전략우위" if (d_shrp > 0 and m['mdd'] > bnh['mdd']) else ("⚠️혼재" if d_shrp > 0 or d_cagr > 0 else "❌B&H우위")
        print(f"{'':<22}{'Δ':<8}{d_cagr:>+7.2f}%{m['mdd']-bnh['mdd']:>+8.1f}%{d_shrp:>+8.2f}{'':>8}{'':>13}   {verdict}")
        print("-" * W)
    print("\n* MDD는 음수 — Δ가 +면 전략의 낙폭이 더 작음(좋음). Sharpe Δ +면 위험조정수익 우위.")


if __name__ == "__main__":
    main()
