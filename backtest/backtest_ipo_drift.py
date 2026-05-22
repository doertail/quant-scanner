"""backtest_ipo_drift.py — "대형 IPO 이후 주가 하락" 가설 이벤트 스터디.

Part A: IPO 종목 자체의 day-0 종가 진입 forward 수익률 (절대 + SPY 초과).
Part B: IPO day-0 이후 SPY/QQQ 시장 추세 vs 무조건부 베이스라인.
AI 태그 서브셋(SNOW/PLTR/AI/ARM/ALAB/CRWV)을 별도로 비교한다.

설계: docs/superpowers/specs/2026-05-22-ipo-drift-backtest-design.md
실행: python backtest/backtest_ipo_drift.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from ipo_drift_metrics import forward_return, summarize

# (ticker, ipo_date 근사값 YYYY-MM-DD, ai_related)
# ipo_date는 참고용 — 실제 day-0은 yfinance 첫 거래일을 사용한다.
IPO_UNIVERSE: list[tuple[str, str, bool]] = [
    ("SPOT", "2018-04-03", False),
    ("DBX",  "2018-03-23", False),
    ("DOCU", "2018-04-27", False),
    ("UBER", "2019-05-10", False),
    ("LYFT", "2019-03-29", False),
    ("PINS", "2019-04-18", False),
    ("ZM",   "2019-04-18", False),
    ("CRWD", "2019-06-12", False),
    ("DDOG", "2019-09-19", False),
    ("SNOW", "2020-09-16", True),
    ("ABNB", "2020-12-10", False),
    ("DASH", "2020-12-09", False),
    ("PLTR", "2020-09-30", True),
    ("U",    "2020-09-18", False),
    ("AI",   "2020-12-09", True),
    ("COIN", "2021-04-14", False),
    ("RIVN", "2021-11-10", False),
    ("HOOD", "2021-07-29", False),
    ("RBLX", "2021-03-10", False),
    ("GTLB", "2021-10-14", False),
    ("AFRM", "2021-01-13", False),
    ("ARM",  "2023-09-14", True),
    ("CART", "2023-09-19", False),
    ("KVYO", "2023-09-20", False),
    ("BIRK", "2023-10-11", False),
    ("RDDT", "2024-03-21", False),
    ("ALAB", "2024-03-20", True),
    ("CRWV", "2025-03-28", True),
]

HORIZONS = [5, 20, 60, 120, 180, 252]
BASELINE_START = "2018-01-01"
BASELINE_END = "2025-12-31"
DATA_START = "2017-06-01"  # SPY/QQQ buffer before earliest IPO


def print_config() -> None:
    ai_n = sum(1 for _, _, ai in IPO_UNIVERSE if ai)
    print("=== IPO Drift Backtest ===")
    print(
        f"config: 유니버스 {len(IPO_UNIVERSE)}개(AI {ai_n}개) | "
        f"HORIZONS={HORIZONS} | 데이터: yfinance 일봉"
    )
    print(f"베이스라인 구간: {BASELINE_START} ~ {BASELINE_END}")
    print()


def fetch_ipo_closes() -> dict[str, pd.Series]:
    """Download each IPO ticker; return {ticker: close Series} indexed by date.

    Skips tickers with no data. Warns if yfinance first bar differs from the
    hardcoded ipo_date by more than 5 calendar days.
    """
    tickers = [t for t, _, _ in IPO_UNIVERSE]
    raw = yf.download(
        tickers, start=DATA_START, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )
    out: dict[str, pd.Series] = {}
    for ticker, ipo_date, _ in IPO_UNIVERSE:
        try:
            close = raw[ticker]["Close"].dropna()
        except (KeyError, TypeError):
            print(f"  [warn] {ticker}: 데이터 없음 — 제외")
            continue
        if close.empty:
            print(f"  [warn] {ticker}: 종가 없음 — 제외")
            continue
        first = close.index[0]
        first_naive = first.tz_localize(None) if first.tzinfo else first
        gap = abs((first_naive - pd.Timestamp(ipo_date)).days)
        if gap > 5:
            print(f"  [warn] {ticker}: 첫 거래일 {first_naive.date()} vs "
                  f"하드코딩 {ipo_date} ({gap}일 차이)")
        out[ticker] = close
    return out


def fetch_market_closes() -> dict[str, pd.Series]:
    """Download SPY and QQQ close Series indexed by date."""
    out: dict[str, pd.Series] = {}
    for sym in ("SPY", "QQQ"):
        hist = yf.Ticker(sym).history(start=DATA_START, auto_adjust=False)
        out[sym] = hist["Close"].dropna()
    return out


def main() -> None:
    print_config()
    print("[1/?] IPO 종목 데이터 다운로드...")
    ipo_closes = fetch_ipo_closes()
    print(f"  -> {len(ipo_closes)}개 종목 로드")
    print("[2/?] 시장 데이터(SPY/QQQ) 다운로드...")
    market = fetch_market_closes()
    print(f"  -> SPY {len(market['SPY'])} bars, QQQ {len(market['QQQ'])} bars")


if __name__ == "__main__":
    main()
