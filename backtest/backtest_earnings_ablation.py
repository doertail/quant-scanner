"""
backtest_earnings_ablation.py — 어닝스 필터 효과 측정

질문: 어닝스 ±2일 이내에 발생한 RSI 진입 신호는 진짜 거짓 신호인가?

방법:
  - 유니버스: S&P 500 대형 50종목
  - 기간: 2024-01-01 ~ 2026-01-01 (2년)
  - 시그널: 전략 A 조건 (RSI<35, Close<MA20, Close>MA200) 매일 스캔
  - 각 시그널 분류:
      group_near : 시그널 날짜 ±2일 이내에 어닝스 발표 있었음
      group_far  : 그 외
  - 측정: 다음 5일 / 20일 수익률, 승률, 최대 낙폭

결과 해석:
  - group_near 평균 수익 < group_far → 어닝스 필터 효과 있음
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 대형주 50개 (S&P 500 시총 상위 + 섹터 분산)
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK-B", "AVGO", "LLY",
    "JPM", "V", "UNH", "XOM", "WMT", "MA", "PG", "JNJ", "HD", "ORCL",
    "COST", "MRK", "ABBV", "BAC", "NFLX", "CVX", "KO", "AMD", "PEP", "TMO",
    "ADBE", "CSCO", "MCD", "ACN", "CRM", "ABT", "LIN", "WFC", "DHR", "TXN",
    "PM", "VZ", "DIS", "NEE", "INTU", "QCOM", "AMGN", "IBM", "MS", "GS",
]

START = "2024-01-01"
END   = "2026-01-01"
EARNINGS_WINDOW_DAYS = 2
HORIZONS = [5, 20]


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    rs    = up.ewm(com=period - 1, adjust=False).mean() / down.ewm(com=period - 1, adjust=False).mean()
    return 100 - (100 / (1 + rs))


def find_signals(df: pd.DataFrame) -> list[pd.Timestamp]:
    """전략 A 시그널 발생 날짜 리스트. RSI<35, Close<MA20, Close>MA200."""
    df = df.copy()
    df["RSI"]   = compute_rsi(df["Close"])
    df["MA20"]  = df["Close"].rolling(20).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    mask = (
        (df["RSI"] < 35) &
        (df["Close"] < df["MA20"]) &
        (df["Close"] > df["MA200"])
    )
    return df.index[mask].tolist()


def get_earnings_dates_set(ticker: str) -> set:
    """과거 어닝스 발표일을 date set으로."""
    try:
        ed = yf.Ticker(ticker).earnings_dates
        if ed is None or ed.empty:
            return set()
        return {d.date() for d in ed.index.tolist()}
    except Exception:
        return set()


def near_earnings(signal_date, earnings_set: set, window: int) -> bool:
    sig = signal_date.date() if hasattr(signal_date, "date") else signal_date
    for ed in earnings_set:
        if abs((ed - sig).days) <= window:
            return True
    return False


def forward_return(close: pd.Series, idx: int, horizon: int) -> float | None:
    if idx + horizon >= len(close):
        return None
    entry = float(close.iloc[idx])
    exit_ = float(close.iloc[idx + horizon])
    if entry <= 0:
        return None
    return (exit_ / entry - 1) * 100


def main():
    print(f"기간: {START} → {END} | 유니버스: {len(UNIVERSE)} | 어닝스 윈도우: ±{EARNINGS_WINDOW_DAYS}일")
    print()

    print("[1/3] 가격 데이터 다운로드...")
    raw = yf.download(
        UNIVERSE, start=START, end=END, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )

    print("[2/3] 어닝스 날짜 수집...")
    earnings_map = {t: get_earnings_dates_set(t) for t in UNIVERSE}
    total_earnings = sum(len(s) for s in earnings_map.values())
    print(f"      총 {total_earnings}개 어닝스 이벤트 수집")

    print("[3/3] 시그널 탐색 + 분류 + 수익률 계산...")
    results = []  # {ticker, date, group, ret_5d, ret_20d}
    for ticker in UNIVERSE:
        try:
            df = raw[ticker][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            continue
        if len(df) < 250:
            continue
        sig_dates = find_signals(df)
        eset = earnings_map.get(ticker, set())
        close = df["Close"]
        for sd in sig_dates:
            idx = df.index.get_loc(sd)
            group = "near" if near_earnings(sd, eset, EARNINGS_WINDOW_DAYS) else "far"
            row = {"ticker": ticker, "date": sd, "group": group}
            for h in HORIZONS:
                row[f"ret_{h}d"] = forward_return(close, idx, h)
            results.append(row)

    df_r = pd.DataFrame(results)
    if df_r.empty:
        print("시그널 없음 — 백테스트 실패")
        return

    print()
    print(f"총 시그널: {len(df_r)}")
    print(f"  - 어닝스 ±{EARNINGS_WINDOW_DAYS}일 (near): {(df_r['group'] == 'near').sum()}")
    print(f"  - 그 외          (far) : {(df_r['group'] == 'far').sum()}")

    # 통계
    print()
    print("=" * 70)
    print(f"  {'Group':<8} | {'N':>5} | {'Mean 5d':>9} | {'Win 5d':>7} | {'Mean 20d':>9} | {'Win 20d':>7}")
    print("-" * 70)
    for group in ["near", "far"]:
        g = df_r[df_r["group"] == group]
        if g.empty:
            continue
        for stats_only in [None]:
            r5  = g["ret_5d"].dropna()
            r20 = g["ret_20d"].dropna()
            print(
                f"  {group:<8} | "
                f"{len(g):>5} | "
                f"{r5.mean():>8.2f}% | "
                f"{(r5 > 0).mean() * 100:>6.1f}% | "
                f"{r20.mean():>8.2f}% | "
                f"{(r20 > 0).mean() * 100:>6.1f}%"
            )
    print("=" * 70)

    # 어닝스 필터 효과: near-far 차이
    near5  = df_r[df_r["group"] == "near"]["ret_5d"].dropna()
    far5   = df_r[df_r["group"] == "far"]["ret_5d"].dropna()
    near20 = df_r[df_r["group"] == "near"]["ret_20d"].dropna()
    far20  = df_r[df_r["group"] == "far"]["ret_20d"].dropna()

    if len(near5) > 0 and len(far5) > 0:
        diff5  = far5.mean() - near5.mean()
        diff20 = far20.mean() - near20.mean()
        print()
        print(f"필터 효과:")
        print(f"  5d 평균 수익 차이 (far - near):  {diff5:>+6.2f}%pt")
        print(f"  20d 평균 수익 차이 (far - near): {diff20:>+6.2f}%pt")
        print(f"  → far > near 이면 어닝스 필터가 유효 (저성과 시그널 제거)")

    # 워스트 시그널 분석
    print()
    print("[최악 10개 시그널 — 5일 손실 기준]")
    worst = df_r.sort_values("ret_5d").head(10)
    for _, r in worst.iterrows():
        print(f"  {r['ticker']:>6}  {r['date'].date()}  group={r['group']:>4}  ret_5d={r['ret_5d']:>+7.2f}%  ret_20d={r['ret_20d']:>+7.2f}%")

    # 결과 저장
    out_path = Path(__file__).resolve().parent / "results_earnings_ablation.csv"
    df_r.to_csv(out_path, index=False)
    print(f"\nCSV 저장: {out_path}")


if __name__ == "__main__":
    main()
