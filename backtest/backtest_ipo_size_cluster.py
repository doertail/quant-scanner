"""backtest_ipo_size_cluster.py — IPO 규모·클러스터링의 crowding-out 효과 검증.

IPO drift 백테스트(Part B)의 확장. 가설: IPO 규모가 크거나 대형 IPO가 한 시기에
몰리면(clustering) 자금을 대느라 다른 주식이 팔려 시장(SPY/QQQ)이 약해지는가?

변수 3개를 중앙값 2분할 버킷으로 비교:
  - deal_size_b: IPO 조달액(달러 10억). crowding-out 메커니즘과 직결.
  - mktcap_b:    상장일 시가총액(달러 10억).
  - cluster_intensity: 이벤트 +-90일 내 유니버스 IPO 조달액 합.

설계: docs/superpowers/specs/2026-05-22-ipo-size-cluster-design.md
실행: python backtest/backtest_ipo_size_cluster.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from ipo_drift_metrics import forward_return, summarize
from ipo_size_metrics import cluster_intensity, median_split, pearson

# (ticker, ipo_date, ai_related, deal_size_b, mktcap_b)
# 규모 수치는 공개 자료 기반 반올림 근사값 — 점추정 금지.
# 직상장(SPOT/COIN/PLTR/RBLX)은 조달액이 $0이므로 deal_size_b는 첫날
# 유통가치(시장이 흡수한 물량) 근사치다.
IPO_UNIVERSE_SIZED: list[tuple[str, str, bool, float, float]] = [
    ("SPOT", "2018-04-03", False,  9.2, 26.5),  # 직상장
    ("DBX",  "2018-03-23", False,  0.75,  9.2),
    ("DOCU", "2018-04-27", False,  0.63,  6.0),
    ("UBER", "2019-05-10", False,  8.1,  69.7),
    ("LYFT", "2019-03-29", False,  2.34, 22.4),
    ("PINS", "2019-04-18", False,  1.4,  12.7),
    ("ZM",   "2019-04-18", False,  0.75, 15.9),
    ("CRWD", "2019-06-12", False,  0.61, 11.4),
    ("DDOG", "2019-09-19", False,  0.65, 10.9),
    ("SNOW", "2020-09-16", True,   3.4,  70.4),
    ("ABNB", "2020-12-10", False,  3.5,  86.5),
    ("DASH", "2020-12-09", False,  3.4,  60.2),
    ("PLTR", "2020-09-30", True,   3.0,  21.0),  # 직상장
    ("U",    "2020-09-18", False,  1.3,  17.9),
    ("AI",   "2020-12-09", True,   0.65,  9.0),
    ("COIN", "2021-04-14", False, 30.0,  58.0),  # 직상장
    ("RIVN", "2021-11-10", False, 13.7,  66.5),
    ("HOOD", "2021-07-29", False,  2.1,  29.0),
    ("RBLX", "2021-03-10", False, 10.0,  38.3),  # 직상장
    ("GTLB", "2021-10-14", False,  0.65, 14.9),
    ("AFRM", "2021-01-13", False,  1.2,  23.6),
    ("ARM",  "2023-09-14", True,   4.87, 65.2),
    ("CART", "2023-09-19", False,  0.66, 11.2),
    ("KVYO", "2023-09-20", False,  0.58,  9.2),
    ("BIRK", "2023-10-11", False,  1.48,  7.5),
    ("RDDT", "2024-03-21", False,  0.75,  9.5),
    ("ALAB", "2024-03-20", True,   0.71,  9.5),
    ("CRWV", "2025-03-28", True,   1.5,  23.0),
]

HORIZONS = [5, 20, 60, 120, 180, 252]
BASELINE_START = "2018-01-01"
BASELINE_END = "2025-12-31"
DATA_START = "2017-06-01"
CLUSTER_WINDOW_DAYS = 90


def print_config() -> None:
    print("=== IPO Size & Clustering Backtest ===")
    print(
        f"config: 유니버스 {len(IPO_UNIVERSE_SIZED)}개 | "
        f"변수: deal_size / mktcap / cluster_intensity | 중앙값 2분할"
    )
    print(f"HORIZONS={HORIZONS} | 클러스터 창 ±{CLUSTER_WINDOW_DAYS}일")
    print(f"베이스라인 구간: {BASELINE_START} ~ {BASELINE_END}")
    print()


def _series_to_naive(s: pd.Series) -> pd.Series:
    """Return a copy with a tz-naive DatetimeIndex for safe alignment."""
    idx = s.index
    if idx.tz is not None:
        s = s.copy()
        s.index = idx.tz_localize(None)
    return s


def fetch_ipo_closes() -> dict[str, pd.Series]:
    """Download each IPO ticker; return {ticker: close Series}. Skips missing."""
    tickers = [t for t, _, _, _, _ in IPO_UNIVERSE_SIZED]
    raw = yf.download(
        tickers, start=DATA_START, group_by="ticker",
        auto_adjust=False, progress=False, threads=True,
    )
    out: dict[str, pd.Series] = {}
    for ticker, _, _, _, _ in IPO_UNIVERSE_SIZED:
        try:
            close = raw[ticker]["Close"].dropna()
        except (KeyError, TypeError):
            print(f"  [warn] {ticker}: 데이터 없음 — 제외")
            continue
        if close.empty:
            print(f"  [warn] {ticker}: 종가 없음 — 제외")
            continue
        out[ticker] = close
    return out


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


def compute_events(ipo_closes: dict[str, pd.Series],
                   market: dict[str, pd.Series]) -> list[dict]:
    """One row per IPO event: size fields, cluster intensity, and SPY/QQQ
    forward returns measured from the IPO day-0.
    """
    rows: list[dict] = []
    naive_market = {sym: _series_to_naive(market[sym]) for sym in ("SPY", "QQQ")}
    for idx, entry in enumerate(IPO_UNIVERSE_SIZED):
        ticker, _, ai, deal_size_b, mktcap_b = entry
        if ticker not in ipo_closes:
            continue
        day0 = _series_to_naive(ipo_closes[ticker]).index[0]
        row = {
            "ticker": ticker,
            "ipo_date": day0.date().isoformat(),
            "ai": ai,
            "deal_size_b": deal_size_b,
            "mktcap_b": mktcap_b,
            "cluster_intensity": cluster_intensity(
                IPO_UNIVERSE_SIZED, idx, CLUSTER_WINDOW_DAYS),
        }
        for sym in ("SPY", "QQQ"):
            s = naive_market[sym]
            vals = s.tolist()
            i = s.index.get_indexer([day0], method="nearest")[0]
            for h in HORIZONS:
                row[f"{sym}_{h}d"] = forward_return(vals, i, h)
        rows.append(row)
    return rows


def main() -> None:
    print_config()
    print("[1/3] IPO 종목 데이터 다운로드...")
    ipo_closes = fetch_ipo_closes()
    print(f"  -> {len(ipo_closes)}개 종목 로드")
    print("[2/3] 시장 데이터(SPY/QQQ) 다운로드...")
    market = fetch_market_closes()
    print(f"  -> SPY {len(market['SPY'])} bars, QQQ {len(market['QQQ'])} bars")
    print("[3/3] 베이스라인 + 이벤트 계산...")
    baseline = compute_baseline(market)
    events = compute_events(ipo_closes, market)
    print(f"  -> 이벤트 {len(events)}개 | "
          f"베이스라인 SPY 252d {baseline['SPY'][252]*100:.2f}%")


if __name__ == "__main__":
    main()
