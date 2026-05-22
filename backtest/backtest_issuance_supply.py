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


def print_year_table(years: list[dict]) -> None:
    """Print each year's issuance and SPY/QQQ forward returns, highest
    total issuance first.
    """
    print("[연도별 발행액 × forward 시장 수익률]  (total_issuance 내림차순)")
    print(f"  {'Year':>5} | {'IPO $B':>7} | {'Total $B':>8} | "
          f"{'SPY 126d':>9} | {'SPY 252d':>9} | {'QQQ 126d':>9} | {'QQQ 252d':>9}")
    for r in sorted(years, key=lambda e: e["total_issuance_b"], reverse=True):
        def fmt(key: str) -> str:
            v = r[key]
            return f"{v*100:>8.2f}%" if v is not None else f"{'—':>9}"
        print(f"  {r['year']:>5} | {r['ipo_proceeds_b']:>7.0f} | "
              f"{r['total_issuance_b']:>8.0f} | {fmt('SPY_126d')} | "
              f"{fmt('SPY_252d')} | {fmt('QQQ_126d')} | {fmt('QQQ_252d')}")
    print()


def _diff(mean: float | None, base: float | None) -> float | None:
    """Forward-return mean minus baseline mean, or None if either is missing."""
    if mean is None or base is None:
        return None
    return mean - base


def print_split(years: list[dict],
                baseline: dict[str, dict[int, float]]) -> None:
    """Median-split years by total issuance; compare HIGH vs LOW forward returns."""
    high, low = median_split(years, "total_issuance_b")
    print(f"[상·하위 절반 비교]  total_issuance 중앙값 분할 — "
          f"버킷당 LOW {len(low)} / HIGH {len(high)}개 (사례 비교, 통계 아님)")
    print(f"  {'Bucket':>6} | {'Horizon':>7} | {'SPY mean':>9} | {'SPY diff':>9} | "
          f"{'QQQ mean':>9} | {'QQQ diff':>9}")
    for h in HORIZONS:
        for label, bucket in (("HIGH", high), ("LOW", low)):
            spy = summarize([e[f"SPY_{h}d"] for e in bucket])
            qqq = summarize([e[f"QQQ_{h}d"] for e in bucket])
            sd = _diff(spy["mean"], baseline["SPY"][h])
            qd = _diff(qqq["mean"], baseline["QQQ"][h])
            sm = f"{spy['mean']*100:>8.2f}%" if spy["mean"] is not None else f"{'—':>9}"
            sds = f"{sd*100:>+8.2f}%" if sd is not None else f"{'—':>9}"
            qm = f"{qqq['mean']*100:>8.2f}%" if qqq["mean"] is not None else f"{'—':>9}"
            qds = f"{qd*100:>+8.2f}%" if qd is not None else f"{'—':>9}"
            print(f"  {label:>6} | {h:>6}d | {sm} | {sds} | {qm} | {qds}")
    print("  → HIGH 버킷 diff가 LOW보다 낮으면 공급충격 가설과 방향 일치")
    print()


def print_correlations(years: list[dict]) -> None:
    """Pearson r of each issuance variable vs SPY/QQQ forward returns."""
    print("[상관계수]  Pearson r — N=8, 통계적 의미 없음, 참고용")
    print(f"  {'변수':>16} | {'SPY 126d':>9} | {'SPY 252d':>9} | "
          f"{'QQQ 126d':>9} | {'QQQ 252d':>9}")
    for label, key in (("ipo_proceeds", "ipo_proceeds_b"),
                       ("total_issuance", "total_issuance_b")):
        cells = []
        for sym in ("SPY", "QQQ"):
            for h in HORIZONS:
                r = pearson([e[key] for e in years],
                            [e[f"{sym}_{h}d"] for e in years])
                cells.append(f"{r:>+8.2f}" if r is not None else f"{'—':>9}")
        print(f"  {label:>16} | " + " | ".join(cells))
    print("  (음수 r = 고발행 → 이후 시장 약함. 단 N=8·내생성 때문에 인과 아님)")
    print()


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

    print()
    print_year_table(years)
    print_split(years, baseline)
    print_correlations(years)


if __name__ == "__main__":
    main()
