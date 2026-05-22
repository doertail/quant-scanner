"""backtest_issuance_supply.py — 시장 전체 신규 발행(공급 충격) vs 시장 수익률.

가설: 시장 전체 신규 주식 발행이 많은 해일수록, 그 자본을 빨아들이느라 이후
시장(SPY/QQQ) 수익률이 약한가? 2026년 대규모 동시 상장 시나리오의 참고용.

⚠️ N=8 (연도별 데이터) — 통계 분석이 아니라 서술적 사례 분석이다. 발행액은
근사 공개 집계치이며, 발행은 내생적(시장이 뜨거울 때 발행)이라 인과 해석 불가.

설계: docs/superpowers/specs/2026-05-22-issuance-supply-design.md
실행: python backtest/backtest_issuance_supply.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from ipo_drift_metrics import forward_return, summarize
from ipo_size_metrics import median_split, pearson

# (year, year_start_date, ipo_proceeds_b, total_issuance_b)
# 발행액은 공개 자료 기반 반올림 근사치(달러 10억). total_issuance_b(IPO +
# follow-on)는 ipo_proceeds_b보다 출처 신뢰도가 낮다 — 정밀값이 아니라 거시
# 패턴(붐/붕괴)을 보는 용도.
ANNUAL_ISSUANCE: list[tuple[int, str, float, float]] = [
    (2018, "2018-01-02",  47.0, 190.0),
    (2019, "2019-01-02",  54.0, 220.0),
    (2020, "2020-01-02",  85.0, 350.0),
    (2021, "2021-01-04", 154.0, 435.0),
    (2022, "2022-01-03",   8.0, 110.0),
    (2023, "2023-01-03",  19.0, 140.0),
    (2024, "2024-01-02",  30.0, 165.0),
    (2025, "2025-01-02",  35.0, 180.0),
]

HORIZONS = [126, 252]
BASELINE_START = "2018-01-01"
BASELINE_END = "2025-12-31"
DATA_START = "2017-06-01"


def print_config() -> None:
    print("=== Issuance Supply-Shock Backtest ===")
    print(f"config: 연도 {len(ANNUAL_ISSUANCE)}개(2018~2025) | HORIZONS={HORIZONS}")
    print("        ⚠️ N=8 — 서술적 사례 분석, 통계 아님")
    print(f"베이스라인 구간: {BASELINE_START} ~ {BASELINE_END}")
    print()


def _series_to_naive(s: pd.Series) -> pd.Series:
    """Return a copy with a tz-naive DatetimeIndex for safe alignment."""
    idx = s.index
    if idx.tz is not None:
        s = s.copy()
        s.index = idx.tz_localize(None)
    return s


def fetch_market_closes() -> dict[str, pd.Series]:
    """Download SPY and QQQ close Series indexed by date."""
    out: dict[str, pd.Series] = {}
    for sym in ("SPY", "QQQ"):
        hist = yf.Ticker(sym).history(start=DATA_START, auto_adjust=False)
        out[sym] = hist["Close"].dropna()
    return out


def compute_baseline(market: dict[str, pd.Series]) -> dict[str, dict[int, float]]:
    """Unconditional mean forward return per symbol per horizon, over every
    trading day in [BASELINE_START, BASELINE_END].
    """
    base: dict[str, dict[int, float]] = {}
    lo, hi = pd.Timestamp(BASELINE_START), pd.Timestamp(BASELINE_END)
    for sym in ("SPY", "QQQ"):
        s = _series_to_naive(market[sym])
        vals = s.tolist()
        in_window = [i for i, d in enumerate(s.index) if lo <= d <= hi]
        base[sym] = {}
        for h in HORIZONS:
            rets = [forward_return(vals, i, h) for i in in_window]
            base[sym][h] = summarize(rets)["mean"]
    return base


def compute_years(market: dict[str, pd.Series]) -> list[dict]:
    """One row per year: issuance fields and SPY/QQQ forward returns measured
    from that year's start date.
    """
    rows: list[dict] = []
    naive = {sym: _series_to_naive(market[sym]) for sym in ("SPY", "QQQ")}
    for year, start, ipo_b, total_b in ANNUAL_ISSUANCE:
        row = {"year": year, "ipo_proceeds_b": ipo_b, "total_issuance_b": total_b}
        day0 = pd.Timestamp(start)
        for sym in ("SPY", "QQQ"):
            s = naive[sym]
            vals = s.tolist()
            i = s.index.get_indexer([day0], method="nearest")[0]
            for h in HORIZONS:
                row[f"{sym}_{h}d"] = forward_return(vals, i, h)
        rows.append(row)
    return rows


def main() -> None:
    print_config()
    print("[1/2] 시장 데이터(SPY/QQQ) 다운로드...")
    market = fetch_market_closes()
    print(f"  -> SPY {len(market['SPY'])} bars, QQQ {len(market['QQQ'])} bars")
    print("[2/2] 베이스라인 + 연도 계산...")
    baseline = compute_baseline(market)
    years = compute_years(market)
    print(f"  -> 연도 {len(years)}개 | "
          f"베이스라인 SPY 252d {baseline['SPY'][252]*100:.2f}%")


if __name__ == "__main__":
    main()
