"""
backtest_macro_regime_ablation.py — 3-layer 거시 국면 필터 효과 측정

질문: ADX + 시장폭 + VIX/RV 3-레이어 투표로 결정한 regime이 실제로
      전략 A 평균회귀 시그널의 outcomes을 차별화하는가?

방법:
  - 유니버스: 30개 S&P 500 대형주 (backtest_vix.py와 동일)
  - 기간: 2018-01-01 ~ 2026-01-01 (8년)
  - 매일 3-layer regime 계산: BULL / SIDEWAYS / BEAR
  - 매일 전략 A 시그널 스캔: RSI<35 + Close<MA20 + Close>MA200
  - 각 시그널을 entry-day의 regime으로 그룹화 → forward 5d/20d 수익률 비교

결과 해석:
  - BULL regime 시그널 평균수익 > SIDEWAYS/BEAR → regime 필터 유효
  - 모든 regime 결과 비슷 → regime 필터 무효 (정직하게 보고)
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# 30 large caps — same as backtest_vix.py
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK-B", "AVGO", "LLY",
    "JPM", "V", "UNH", "XOM", "WMT", "MA", "PG", "JNJ", "HD", "ORCL",
    "COST", "MRK", "ABBV", "BAC", "NFLX", "CVX", "KO", "AMD", "PEP", "TMO",
]

# Breadth proxy universe (50 large caps)
BREADTH_UNIVERSE = UNIVERSE + [
    "ADBE", "CSCO", "MCD", "ACN", "CRM", "ABT", "LIN", "WFC", "DHR", "TXN",
    "PM", "VZ", "DIS", "NEE", "INTU", "QCOM", "AMGN", "IBM", "MS", "GS",
]

START = "2018-01-01"
END   = "2026-01-01"
HORIZONS = [5, 20]

# Thresholds — same as macro_regime skill
ADX_PERIOD             = 14
ADX_TREND_THRESHOLD    = 25
ADX_SIDEWAYS_THRESHOLD = 20
BREADTH_BULL           = 60.0
BREADTH_BEAR           = 40.0
VIX_RV_LOW             = 0.8
VIX_RV_HIGH            = 1.2
RV_WINDOW              = 20


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    rs    = up.ewm(com=period - 1, adjust=False).mean() / down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + rs))


def compute_adx_series(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.DataFrame:
    """Return DataFrame with adx, plus_di, minus_di columns indexed by date."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    up_move, down_move = high - high.shift(1), low.shift(1) - low
    plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr      = tr.ewm(com=period - 1, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(com=period - 1, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(com=period - 1, adjust=False).mean() / atr
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx      = dx.ewm(com=period - 1, adjust=False).mean()
    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})


def vote_regime_row(adx, plus_di, minus_di, breadth_pct, vix_rv, qqq_above_ma200) -> str:
    # Layer 1
    if not pd.isna(adx) and adx >= ADX_TREND_THRESHOLD:
        l1 = "BULL" if plus_di > minus_di else "BEAR"
    elif not pd.isna(adx) and adx < ADX_SIDEWAYS_THRESHOLD:
        l1 = "SIDEWAYS"
    else:
        l1 = "BULL" if qqq_above_ma200 else "BEAR"

    # Layer 2
    if pd.isna(breadth_pct):
        l2 = "BULL" if qqq_above_ma200 else "BEAR"
    elif breadth_pct > BREADTH_BULL:
        l2 = "BULL"
    elif breadth_pct < BREADTH_BEAR:
        l2 = "BEAR"
    else:
        l2 = "SIDEWAYS"

    # Layer 3
    if pd.isna(vix_rv):
        l3 = "BULL" if qqq_above_ma200 else "BEAR"
    elif VIX_RV_LOW <= vix_rv <= VIX_RV_HIGH:
        l3 = "SIDEWAYS"
    else:
        l3 = "BULL" if qqq_above_ma200 else "BEAR"

    votes = [l1, l2, l3]
    if votes.count("SIDEWAYS") >= 2:
        return "SIDEWAYS"
    if votes.count("BULL") >= 2:
        return "BULL"
    return "BEAR"


def main():
    print(f"기간: {START} → {END} | 유니버스: {len(UNIVERSE)} | breadth 표본: {len(BREADTH_UNIVERSE)}")
    print()

    # 1. Macro data
    print("[1/4] 거시 데이터 다운로드 (QQQ, ^VIX, ^GSPC)...")
    qqq = yf.Ticker("QQQ").history(start=START, end=END, auto_adjust=False)
    vix = yf.Ticker("^VIX").history(start=START, end=END, auto_adjust=False)
    spx = yf.Ticker("^GSPC").history(start=START, end=END, auto_adjust=False)

    # 2. Breadth universe data
    print("[2/4] 시장폭 표본 다운로드...")
    breadth_raw = yf.download(
        BREADTH_UNIVERSE, start=START, end=END, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )

    # 3. Compute daily regime
    print("[3/4] 일별 regime 계산...")
    qqq_ma200 = qqq["Close"].rolling(200).mean()
    adx_df    = compute_adx_series(qqq)

    # Breadth daily
    breadth_series = pd.Series(index=qqq.index, dtype=float)
    breadth_above = {}
    breadth_ma200 = {}
    for t in BREADTH_UNIVERSE:
        try:
            c = breadth_raw[t]["Close"].dropna()
            if len(c) < 200:
                continue
            breadth_above[t] = c
            breadth_ma200[t] = c.rolling(200).mean()
        except Exception:
            continue

    for d in qqq.index:
        above, total = 0, 0
        d_naive = d.tz_localize(None) if d.tzinfo else d
        for t in breadth_above:
            try:
                cs = breadth_above[t]
                ms = breadth_ma200[t]
                cs_naive_idx = cs.index.tz_localize(None) if cs.index.tz else cs.index
                if d_naive not in cs_naive_idx:
                    continue
                pos = cs_naive_idx.get_loc(d_naive)
                close = float(cs.iloc[pos])
                ma    = float(ms.iloc[pos])
                if pd.isna(close) or pd.isna(ma):
                    continue
                above += int(close > ma)
                total += 1
            except Exception:
                continue
        breadth_series.loc[d] = (above / total * 100) if total else float("nan")

    # VIX/RV
    spx_rets = spx["Close"].pct_change()
    rv_series = spx_rets.rolling(RV_WINDOW).std() * np.sqrt(252) * 100
    vix_aligned = vix["Close"].reindex(qqq.index, method="ffill")
    rv_aligned  = rv_series.reindex(qqq.index, method="ffill")
    ratio = vix_aligned / rv_aligned

    # Vote per day
    regime_series = pd.Series(index=qqq.index, dtype=object)
    for d in qqq.index:
        adx = adx_df.loc[d, "adx"] if d in adx_df.index else float("nan")
        pdi = adx_df.loc[d, "plus_di"] if d in adx_df.index else float("nan")
        mdi = adx_df.loc[d, "minus_di"] if d in adx_df.index else float("nan")
        brd = breadth_series.loc[d]
        rt  = ratio.loc[d] if d in ratio.index else float("nan")
        q_above = qqq.loc[d, "Close"] > qqq_ma200.loc[d] if not pd.isna(qqq_ma200.loc[d]) else True
        regime_series.loc[d] = vote_regime_row(adx, pdi, mdi, brd, rt, q_above)

    # 4. Strategy A signals on UNIVERSE
    print("[4/4] 전략 A 시그널 + regime 분류 + 수익률...")
    raw = yf.download(
        UNIVERSE, start=START, end=END, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )

    results = []
    for ticker in UNIVERSE:
        try:
            df = raw[ticker][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            continue
        if len(df) < 250:
            continue
        rsi   = compute_rsi(df["Close"])
        ma20  = df["Close"].rolling(20).mean()
        ma200 = df["Close"].rolling(200).mean()
        mask = (rsi < 35) & (df["Close"] < ma20) & (df["Close"] > ma200)
        close = df["Close"]
        for d in df.index[mask]:
            d_naive = d.tz_localize(None) if d.tzinfo else d
            regime_idx = regime_series.index
            regime_idx_naive = regime_idx.tz_localize(None) if regime_idx.tz else regime_idx
            if d_naive not in regime_idx_naive:
                continue
            pos = regime_idx_naive.get_loc(d_naive)
            regime = regime_series.iloc[pos]
            idx_t = df.index.get_loc(d)
            row = {"ticker": ticker, "date": d, "regime": regime}
            for h in HORIZONS:
                if idx_t + h < len(close):
                    e = float(close.iloc[idx_t])
                    x = float(close.iloc[idx_t + h])
                    row[f"ret_{h}d"] = (x / e - 1) * 100 if e > 0 else None
                else:
                    row[f"ret_{h}d"] = None
            results.append(row)

    df_r = pd.DataFrame(results)
    if df_r.empty:
        print("시그널 없음")
        return

    print()
    print(f"총 시그널: {len(df_r)}")
    counts = df_r["regime"].value_counts()
    for r in ("BULL", "SIDEWAYS", "BEAR"):
        print(f"  - {r:<10}: {counts.get(r, 0)}")

    print()
    print("=" * 78)
    print(f"  {'Regime':<10} | {'N':>5} | {'Mean 5d':>9} | {'Win 5d':>7} | {'Mean 20d':>9} | {'Win 20d':>7}")
    print("-" * 78)
    for regime in ("BULL", "SIDEWAYS", "BEAR"):
        g = df_r[df_r["regime"] == regime]
        if g.empty:
            continue
        r5  = g["ret_5d"].dropna()
        r20 = g["ret_20d"].dropna()
        print(
            f"  {regime:<10} | "
            f"{len(g):>5} | "
            f"{r5.mean():>8.2f}% | "
            f"{(r5 > 0).mean() * 100:>6.1f}% | "
            f"{r20.mean():>8.2f}% | "
            f"{(r20 > 0).mean() * 100:>6.1f}%"
        )
    print("=" * 78)

    # BULL vs (SIDEWAYS+BEAR) difference
    bull   = df_r[df_r["regime"] == "BULL"]
    nbull  = df_r[df_r["regime"].isin(["SIDEWAYS", "BEAR"])]
    if not bull.empty and not nbull.empty:
        diff5  = bull["ret_5d"].mean()  - nbull["ret_5d"].mean()
        diff20 = bull["ret_20d"].mean() - nbull["ret_20d"].mean()
        print()
        print("BULL vs (SIDEWAYS+BEAR) 평균수익 차이:")
        print(f"  5d:  {diff5:>+6.2f}%pt")
        print(f"  20d: {diff20:>+6.2f}%pt")
        print()
        print("해석: BULL 시그널이 SIDEWAYS/BEAR 시그널보다 평균수익이 높으면 macro filter 유효")

    out = Path(__file__).resolve().parent / "results_macro_regime_ablation.csv"
    df_r.to_csv(out, index=False)
    print(f"\nCSV 저장: {out}")


if __name__ == "__main__":
    main()
