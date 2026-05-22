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


def _series_to_naive(s: pd.Series) -> pd.Series:
    """Return a copy with a tz-naive DatetimeIndex for safe alignment."""
    idx = s.index
    if idx.tz is not None:
        s = s.copy()
        s.index = idx.tz_localize(None)
    return s


def compute_part_a(ipo_closes: dict[str, pd.Series],
                   market: dict[str, pd.Series]) -> list[dict]:
    """One row per IPO ticker: day-0 close entry, forward abs + SPY-excess returns."""
    spy = _series_to_naive(market["SPY"])
    spy_list = spy.tolist()
    rows: list[dict] = []
    for ticker, _, ai in IPO_UNIVERSE:
        if ticker not in ipo_closes:
            continue
        closes = _series_to_naive(ipo_closes[ticker])
        stock_list = closes.tolist()
        day0 = closes.index[0]
        spy_idx = spy.index.get_indexer([day0], method="nearest")[0]
        row = {"ticker": ticker, "ipo_date": day0.date().isoformat(), "ai": ai}
        for h in HORIZONS:
            abs_ret = forward_return(stock_list, 0, h)
            spy_ret = forward_return(spy_list, spy_idx, h)
            row[f"abs_{h}d"] = abs_ret
            row[f"exc_{h}d"] = (abs_ret - spy_ret
                                if abs_ret is not None and spy_ret is not None
                                else None)
        rows.append(row)
    return rows


def print_part_a(rows: list[dict]) -> None:
    print()
    print("[Part A] IPO 종목 자체 — day-0 종가 진입")
    for label, subset in (("전체", rows),
                          ("AI", [r for r in rows if r["ai"]])):
        print(f"  그룹: {label} (티커 {len(subset)}개)")
        print(f"  {'Horizon':>8} | {'N':>3} | {'Mean abs':>9} | "
              f"{'Median abs':>10} | {'Win%':>6} | {'Mean exc vs SPY':>15}")
        for h in HORIZONS:
            a = summarize([r[f"abs_{h}d"] for r in subset])
            e = summarize([r[f"exc_{h}d"] for r in subset])
            if a["n"] == 0:
                print(f"  {h:>6}d  | {0:>3} | {'—':>9} | {'—':>10} | "
                      f"{'—':>6} | {'—':>15}")
                continue
            print(f"  {h:>6}d  | {a['n']:>3} | {a['mean']*100:>8.2f}% | "
                  f"{a['median']*100:>9.2f}% | {a['win_rate']*100:>5.1f}% | "
                  f"{(e['mean']*100 if e['mean'] is not None else 0):>14.2f}%")
        print()


def compute_baseline(market: dict[str, pd.Series]) -> dict[str, dict[int, float]]:
    """Unconditional mean forward return per symbol per horizon.

    Entry on every trading day in [BASELINE_START, BASELINE_END].
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


def compute_part_b(ipo_closes: dict[str, pd.Series],
                   market: dict[str, pd.Series]) -> list[dict]:
    """One row per IPO event: SPY/QQQ forward returns from the IPO day-0."""
    rows: list[dict] = []
    naive_market = {sym: _series_to_naive(market[sym]) for sym in ("SPY", "QQQ")}
    for ticker, _, ai in IPO_UNIVERSE:
        if ticker not in ipo_closes:
            continue
        day0 = _series_to_naive(ipo_closes[ticker]).index[0]
        row = {"ticker": ticker, "ipo_date": day0.date().isoformat(), "ai": ai}
        for sym in ("SPY", "QQQ"):
            s = naive_market[sym]
            vals = s.tolist()
            idx = s.index.get_indexer([day0], method="nearest")[0]
            for h in HORIZONS:
                row[f"{sym}_{h}d"] = forward_return(vals, idx, h)
        rows.append(row)
    return rows


def print_part_b(rows: list[dict], baseline: dict[str, dict[int, float]]) -> None:
    print("[Part B] 시장 추세 — IPO day-0 이후 SPY/QQQ")
    for label, subset in (("전체 IPO 이벤트", rows),
                          ("AI IPO 이벤트", [r for r in rows if r["ai"]])):
        print(f"  그룹: {label} (이벤트 {len(subset)}개)")
        print(f"  {'Horizon':>8} | {'N':>3} | {'SPY mean':>9} | {'SPY base':>9} | "
              f"{'SPY diff':>9} | {'QQQ mean':>9} | {'QQQ base':>9} | {'QQQ diff':>9}")
        for h in HORIZONS:
            cells = [f"  {h:>6}d "]
            n_shown = False
            for sym in ("SPY", "QQQ"):
                stat = summarize([r[f"{sym}_{h}d"] for r in subset])
                base = baseline[sym][h]
                if not n_shown:
                    cells.append(f"| {stat['n']:>3} ")
                    n_shown = True
                if stat["n"] == 0 or base is None:
                    cells.append(f"| {'—':>9} | {'—':>9} | {'—':>9} ")
                else:
                    diff = stat["mean"] - base
                    cells.append(f"| {stat['mean']*100:>8.2f}% | "
                                 f"{base*100:>8.2f}% | {diff*100:>+8.2f}% ")
            print("".join(cells))
        print()
    print("해석: SPY/QQQ diff가 음수면 'IPO 직후 시장이 베이스라인보다 약함' → 가설 지지")
    print()


def save_csv(part_a: list[dict], part_b: list[dict]) -> Path:
    """Merge per-event Part A and Part B rows and write results_ipo_drift.csv."""
    a_by_ticker = {r["ticker"]: r for r in part_a}
    merged = []
    for b in part_b:
        row = dict(b)
        a = a_by_ticker.get(b["ticker"], {})
        for h in HORIZONS:
            row[f"abs_{h}d"] = a.get(f"abs_{h}d")
            row[f"exc_{h}d"] = a.get(f"exc_{h}d")
        merged.append(row)
    out = Path(__file__).resolve().parent / "results_ipo_drift.csv"
    pd.DataFrame(merged).to_csv(out, index=False)
    return out


def main() -> None:
    print_config()
    print("[1/?] IPO 종목 데이터 다운로드...")
    ipo_closes = fetch_ipo_closes()
    print(f"  -> {len(ipo_closes)}개 종목 로드")
    print("[2/?] 시장 데이터(SPY/QQQ) 다운로드...")
    market = fetch_market_closes()
    print(f"  -> SPY {len(market['SPY'])} bars, QQQ {len(market['QQQ'])} bars")
    part_a = compute_part_a(ipo_closes, market)
    print_part_a(part_a)
    print("[3/3] 베이스라인 계산...")
    baseline = compute_baseline(market)
    part_b = compute_part_b(ipo_closes, market)
    print()
    print_part_b(part_b, baseline)
    out = save_csv(part_a, part_b)
    print(f"CSV 저장: {out}")


if __name__ == "__main__":
    main()
