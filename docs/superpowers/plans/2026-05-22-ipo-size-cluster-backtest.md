# IPO Size & Clustering Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained backtest that tests the crowding-out hypothesis — do larger IPOs (by deal size or market cap) or clustered IPO waves correlate with weaker SPY/QQQ forward returns?

**Architecture:** Two new files under `backtest/`. `ipo_size_metrics.py` holds pure dependency-free helpers (`cluster_intensity`, `median_split`, `pearson`) unit-testable with the bare interpreter. `backtest_ipo_size_cluster.py` is the runnable script: a 28-IPO universe hardcoded with deal-size and market-cap figures, yfinance download, per-event SPY/QQQ forward returns, three median-split bucket analyses, a correlation table, and a largest-bucket summary. It reuses the already-shared pure module `ipo_drift_metrics.py` (`forward_return`, `summarize`); all other logic is self-held, matching the project's one-file-per-backtest convention. `BACKTESTS.md` gets a new section 8.

**Tech Stack:** Python 3, yfinance, pandas (already in `requirements.txt`). No pytest — pure-function tests are plain `assert` scripts run with `python3`.

**Reference spec:** `docs/superpowers/specs/2026-05-22-ipo-size-cluster-design.md`

**Environment note:** A virtualenv with pandas/numpy/yfinance is at `/Users/jihun/Downloads/workspace/quant-scanner/venv/`. Run the backtest script with `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python` — plain `python3` lacks pandas. Tasks 1 (pure module + test) need only the standard library. The branch is `ipo-drift-backtest`; stay on it.

---

## File Structure

- Create: `backtest/ipo_size_metrics.py` — pure helpers: `cluster_intensity`, `median_split`, `pearson`. Only stdlib imports.
- Create: `backtest/test_ipo_size_metrics.py` — plain-`assert` tests, run with `python3`.
- Create: `backtest/backtest_ipo_size_cluster.py` — main script.
- Modify: `BACKTESTS.md` — append section 8.

---

## Task 1: Pure size/cluster metrics module

**Files:**
- Create: `backtest/ipo_size_metrics.py`
- Test: `backtest/test_ipo_size_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `backtest/test_ipo_size_metrics.py`:

```python
"""Plain-assert tests for ipo_size_metrics. Run: python3 backtest/test_ipo_size_metrics.py"""
from ipo_size_metrics import cluster_intensity, median_split, pearson

# universe tuple shape: (ticker, ipo_date, ai_related, deal_size_b, mktcap_b)
_UNIV = [
    ("A", "2020-01-01", False, 1.0, 10.0),
    ("B", "2020-02-01", False, 2.0, 20.0),
    ("C", "2020-03-15", False, 4.0, 40.0),
    ("D", "2021-06-01", False, 8.0, 80.0),
]


def test_cluster_intensity_includes_self_and_window():
    # A on 2020-01-01: within +-90d are A, B (31d), C (74d); D is far. 1+2+4=7
    assert cluster_intensity(_UNIV, 0, 90) == 7.0


def test_cluster_intensity_isolated_event():
    # D on 2021-06-01 has no other event within +-90d -> just itself
    assert cluster_intensity(_UNIV, 3, 90) == 8.0


def test_cluster_intensity_narrow_window():
    # A with a 40-day window: only B is 31d away, C is 74d away -> A + B = 3
    assert cluster_intensity(_UNIV, 0, 40) == 3.0


def test_median_split_even():
    events = [{"v": 3}, {"v": 1}, {"v": 4}, {"v": 2}]
    high, low = median_split(events, "v")
    assert sorted(e["v"] for e in low) == [1, 2]
    assert sorted(e["v"] for e in high) == [3, 4]


def test_median_split_odd_high_gets_extra():
    events = [{"v": 1}, {"v": 2}, {"v": 3}]
    high, low = median_split(events, "v")
    assert sorted(e["v"] for e in low) == [1]
    assert sorted(e["v"] for e in high) == [2, 3]


def test_pearson_perfect_positive():
    assert abs(pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    assert abs(pearson([1, 2, 3], [6, 4, 2]) - (-1.0)) < 1e-9


def test_pearson_drops_none_pairs():
    # None pairs dropped; remaining (1,2),(3,6) are perfectly correlated
    assert abs(pearson([1, None, 3], [2, 9, 6]) - 1.0) < 1e-9


def test_pearson_too_few_pairs():
    assert pearson([1], [2]) is None
    assert pearson([1, None], [None, 2]) is None


def test_pearson_zero_variance():
    assert pearson([5, 5, 5], [1, 2, 3]) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All size-metrics tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backtest && python3 test_ipo_size_metrics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'ipo_size_metrics'`

- [ ] **Step 3: Write minimal implementation**

Create `backtest/ipo_size_metrics.py`:

```python
"""Pure, dependency-free helpers for the IPO size & clustering backtest.

Kept import-free (stdlib only) so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations

from datetime import date


def cluster_intensity(universe: list[tuple], event_idx: int,
                      window_days: int) -> float:
    """Sum of deal_size_b for every universe IPO within +-window_days of the event.

    The event itself is included. `universe` entries are
    (ticker, ipo_date_iso, ai_related, deal_size_b, mktcap_b) tuples.
    """
    ref = date.fromisoformat(universe[event_idx][1])
    total = 0.0
    for entry in universe:
        d = date.fromisoformat(entry[1])
        if abs((d - ref).days) <= window_days:
            total += entry[3]
    return total


def median_split(events: list[dict], key: str) -> tuple[list[dict], list[dict]]:
    """Split events into (high, low) halves by the median of `key`.

    Sorted ascending, the lower half goes to `low`; for odd counts the extra
    element goes to `high`. Returns (high, low).
    """
    ordered = sorted(events, key=lambda e: e[key])
    mid = len(ordered) // 2
    return ordered[mid:], ordered[:mid]


def pearson(xs: list, ys: list) -> float | None:
    """Pearson correlation of xs vs ys. Pairs with a None on either side are
    dropped. Returns None if fewer than 2 valid pairs or zero variance.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    vx = sum((p[0] - mx) ** 2 for p in pairs)
    vy = sum((p[1] - my) ** 2 for p in pairs)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backtest && python3 test_ipo_size_metrics.py`
Expected: PASS — 10 `PASS test_*` lines then `All size-metrics tests passed.`

- [ ] **Step 5: Commit**

```bash
git add backtest/ipo_size_metrics.py backtest/test_ipo_size_metrics.py
git commit -m "Add pure size/cluster metrics module for IPO size backtest"
```

---

## Task 2: Script skeleton — sized universe and config block

**Files:**
- Create: `backtest/backtest_ipo_size_cluster.py`

- [ ] **Step 1: Write the skeleton**

Create `backtest/backtest_ipo_size_cluster.py` with EXACTLY this content:

```python
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


def main() -> None:
    print_config()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify the skeleton executes**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py`
Expected: prints the `=== IPO Size & Clustering Backtest ===` config block (유니버스 28개) and exits cleanly, no traceback.

- [ ] **Step 3: Commit**

```bash
git add backtest/backtest_ipo_size_cluster.py
git commit -m "Add IPO size/cluster backtest skeleton with sized universe"
```

---

## Task 3: Data fetch, baseline, and per-event computation

**Files:**
- Modify: `backtest/backtest_ipo_size_cluster.py`

- [ ] **Step 1: Add the data functions**

Insert these five functions into `backtest/backtest_ipo_size_cluster.py`, placed BEFORE `main()`:

```python
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
```

- [ ] **Step 2: Wire a smoke check into main()**

Replace the body of `main()` so it reads EXACTLY:

```python
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
```

- [ ] **Step 3: Run to verify download + computation works**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py`
Expected: config block, `[1/3]` loads ~28 tickers (a few `[warn]` lines acceptable), `[2/3]` shows SPY/QQQ bar counts in the low thousands, `[3/3]` reports `이벤트 28개` and a baseline percentage. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_size_cluster.py
git commit -m "Add fetch, baseline, and per-event computation for IPO size backtest"
```

---

## Task 4: Three median-split bucket tables

**Files:**
- Modify: `backtest/backtest_ipo_size_cluster.py`

- [ ] **Step 1: Add the split-printing functions**

Insert these two functions BEFORE `main()`:

```python
def _diff(mean: float | None, base: float | None) -> float | None:
    """Forward-return mean minus baseline mean, or None if either is missing."""
    if mean is None or base is None:
        return None
    return mean - base


def print_split(title: str, key: str, events: list[dict],
                baseline: dict[str, dict[int, float]]) -> None:
    """Median-split events by `key` and print HIGH/LOW SPY+QQQ forward returns."""
    high, low = median_split(events, key)
    med = summarize([e[key] for e in events])["median"]
    print(f"[분할] {title} — 중앙값 {med:.2f}  (HIGH {len(high)}개 / LOW {len(low)}개)")
    print(f"  {'Bucket':>6} | {'Horizon':>7} | {'N':>3} | "
          f"{'SPY mean':>9} | {'SPY diff':>9} | {'QQQ mean':>9} | {'QQQ diff':>9}")
    high_weaker = 0
    for h in HORIZONS:
        spy_diff_by_bucket: dict[str, float | None] = {}
        for label, bucket in (("HIGH", high), ("LOW", low)):
            spy = summarize([e[f"SPY_{h}d"] for e in bucket])
            qqq = summarize([e[f"QQQ_{h}d"] for e in bucket])
            sd = _diff(spy["mean"], baseline["SPY"][h])
            qd = _diff(qqq["mean"], baseline["QQQ"][h])
            spy_diff_by_bucket[label] = sd
            sm = f"{spy['mean']*100:>8.2f}%" if spy["mean"] is not None else f"{'—':>9}"
            sds = f"{sd*100:>+8.2f}%" if sd is not None else f"{'—':>9}"
            qm = f"{qqq['mean']*100:>8.2f}%" if qqq["mean"] is not None else f"{'—':>9}"
            qds = f"{qd*100:>+8.2f}%" if qd is not None else f"{'—':>9}"
            print(f"  {label:>6} | {h:>6}d | {spy['n']:>3} | "
                  f"{sm} | {sds} | {qm} | {qds}")
        hi_sd, lo_sd = spy_diff_by_bucket["HIGH"], spy_diff_by_bucket["LOW"]
        if hi_sd is not None and lo_sd is not None and hi_sd < lo_sd:
            high_weaker += 1
    print(f"  → HIGH 버킷 SPY가 LOW보다 약했던 horizon: "
          f"{high_weaker}/{len(HORIZONS)}  (6/6에 가까울수록 crowding-out 가설 지지)")
    print()
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    print()
    print_split("조달액(deal size $B)", "deal_size_b", events, baseline)
    print_split("시가총액(market cap $B)", "mktcap_b", events, baseline)
    print_split("클러스터 강도(±90일 조달액 합 $B)", "cluster_intensity",
                events, baseline)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py`
Expected: three `[분할]` blocks (조달액 / 시가총액 / 클러스터 강도). Each shows a median value, HIGH/LOW rows per horizon with SPY/QQQ mean and diff, and a `→ HIGH 버킷 SPY가 LOW보다 약했던 horizon: X/6` summary line. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_size_cluster.py
git commit -m "Add three median-split bucket tables for IPO size backtest"
```

---

## Task 5: Correlation table and largest-bucket summary

**Files:**
- Modify: `backtest/backtest_ipo_size_cluster.py`

- [ ] **Step 1: Add the correlation and summary functions**

Insert these two functions BEFORE `main()`:

```python
def print_correlations(events: list[dict]) -> None:
    """Pearson r of each size variable vs SPY and QQQ forward returns."""
    print("[상관계수] 규모 변수 × forward 수익률 (Pearson r, 서술 통계)")
    variables = (("deal_size", "deal_size_b"),
                 ("mktcap", "mktcap_b"),
                 ("cluster", "cluster_intensity"))
    for sym in ("SPY", "QQQ"):
        header = f"  {sym+' 변수':>16} | " + " | ".join(
            f"{str(h)+'d':>6}" for h in HORIZONS)
        print(header)
        for label, key in variables:
            cells = []
            for h in HORIZONS:
                r = pearson([e[key] for e in events],
                            [e[f"{sym}_{h}d"] for e in events])
                cells.append(f"{r:>+6.2f}" if r is not None else f"{'—':>6}")
            print(f"  {label:>16} | " + " | ".join(cells))
        print()
    print("  (음수 r = 규모가 클수록 시장 forward 수익률이 낮음 → crowding-out 지지)")
    print()


def print_summary(events: list[dict],
                  baseline: dict[str, dict[int, float]]) -> None:
    """Report the base rate of the largest-market-cap quartile."""
    by_mktcap = sorted(events, key=lambda e: e["mktcap_b"], reverse=True)
    top_n = max(1, len(events) // 4)
    top = by_mktcap[:top_n]
    names = ", ".join(f"{e['ticker']}(${e['mktcap_b']:.0f}B)" for e in top)
    print("[최대규모 요약]")
    print(f"  시총 상위 {top_n}개(역대 최대규모 버킷): {names}")
    for h in (60, 120, 252):
        spy = summarize([e[f"SPY_{h}d"] for e in top])
        qqq = summarize([e[f"QQQ_{h}d"] for e in top])
        sd = _diff(spy["mean"], baseline["SPY"][h])
        qd = _diff(qqq["mean"], baseline["QQQ"][h])
        spy_s = f"{spy['mean']*100:+.2f}%" if spy["mean"] is not None else "—"
        qqq_s = f"{qqq['mean']*100:+.2f}%" if qqq["mean"] is not None else "—"
        sd_s = f"{sd*100:+.2f}pp" if sd is not None else "—"
        qd_s = f"{qd*100:+.2f}pp" if qd is not None else "—"
        print(f"    {h:>3}d: SPY {spy_s} (baseline 대비 {sd_s}) | "
              f"QQQ {qqq_s} ({qd_s})")
    print("  참고: Anthropic/OpenAI/SpaceX는 비상장이라 백테스트 대상이 아니다.")
    print("  세 회사 모두 시총이 위 상위 버킷의 어떤 종목보다 크므로, 상위 버킷의")
    print("  기저율이 가장 가까운 참고치다 — 종목별 예측이 아니라 base rate임에 유의.")
    print()
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    print_correlations(events)
    print_summary(events, baseline)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py`
Expected: after the three split blocks, a `[상관계수]` block with SPY and QQQ sub-tables (rows deal_size / mktcap / cluster, columns the 6 horizons), then a `[최대규모 요약]` block listing the top-7 market-cap names and their 60d/120d/252d returns vs baseline. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_size_cluster.py
git commit -m "Add correlation table and largest-bucket summary"
```

---

## Task 6: CSV export

**Files:**
- Modify: `backtest/backtest_ipo_size_cluster.py`

- [ ] **Step 1: Add the export function**

Insert this function BEFORE `main()`:

```python
def save_csv(events: list[dict]) -> Path:
    """Write per-event rows (size fields + forward returns) to CSV."""
    out = Path(__file__).resolve().parent / "results_ipo_size_cluster.csv"
    pd.DataFrame(events).to_csv(out, index=False)
    return out
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    out = save_csv(events)
    print(f"CSV 저장: {out}")
```

- [ ] **Step 3: Run and verify the CSV**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py && head -3 results_ipo_size_cluster.csv`
Expected: final stdout line `CSV 저장: .../results_ipo_size_cluster.csv`; the CSV header lists `ticker,ipo_date,ai,deal_size_b,mktcap_b,cluster_intensity,SPY_5d,...,QQQ_...` and at least one data row.

- [ ] **Step 4: Confirm the CSV is gitignored**

Run: `git status --porcelain`
Expected: `backtest/results_ipo_size_cluster.csv` does NOT appear — it is covered by the existing `.gitignore` pattern `backtest/results_*.csv`. If it DOES appear as untracked, report it and do not commit it.

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_ipo_size_cluster.py
git commit -m "Add CSV export to IPO size/cluster backtest"
```

---

## Task 7: BACKTESTS.md section 8

**Files:**
- Modify: `BACKTESTS.md`

- [ ] **Step 1: Capture a real run**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py | tee /tmp/ipo_size_run.txt`
Read `/tmp/ipo_size_run.txt`. You MUST use its actual numbers — do not invent any figure.

- [ ] **Step 2: Append section 8**

In `BACKTESTS.md`, immediately BEFORE the `## Caveats Common to All Backtests` heading, insert a new section. Use this structure, filling EVERY table cell and the interpretation with real numbers from the run:

```markdown
## 8. IPO Size & Clustering — Crowding-Out Test (`backtest_ipo_size_cluster.py`)

**Question**: Section 7 showed the market does not weaken after a large IPO on
average. This goes further: do *larger* IPOs (by deal size or market cap), or
*clustered* IPO waves, correlate with weaker SPY/QQQ forward returns — the
"crowding-out" idea that funding IPOs drains other stocks?

**Setup**: The 28-IPO universe from section 7, each hardcoded with an approximate
deal size and IPO-day market cap. Cluster intensity = sum of universe deal sizes
within ±90 days of each event. Events are median-split into HIGH/LOW buckets
(14 each) on each of the three variables; SPY/QQQ forward returns at
5/20/60/120/180/252 trading days are compared against the unconditional
2018–2025 baseline.

### Result

**Median-split — HIGH vs LOW "SPY weaker" horizon count (out of 6)**

| Variable | HIGH-weaker horizons |
|---|---|
| Deal size | <fill from the → line of each split block> |
| Market cap | <fill> |
| Cluster intensity | <fill> |

**Correlation (Pearson r, SPY forward returns)**

| Variable | 5d | 20d | 60d | 120d | 180d | 252d |
|---|---|---|---|---|---|---|
| Deal size | <fill 6 values> |
| Market cap | <fill 6 values> |
| Cluster | <fill 6 values> |

**Largest market-cap quartile (top 7)** — 60d / 120d / 252d SPY return vs baseline:
<fill the three lines from the [최대규모 요약] block>

**Interpretation**: Write 3–5 honest sentences from the actual numbers. State
plainly whether the crowding-out hypothesis is supported — i.e. whether HIGH
buckets are consistently weaker and whether correlations are negative. If the
data contradicts it, say so directly; do not spin.

⚠️ **Approximate size figures** — deal size and market cap are rounded public
estimates; the four direct listings (SPOT, COIN, PLTR, RBLX) raised no primary
proceeds, so their deal size is a first-day float-value proxy. Median split gives
N=14 per bucket — wide confidence intervals. Overlapping forward windows mean
observations are not independent, so no p-values are reported. Anthropic, OpenAI,
and SpaceX are private, not in the universe, and not backtested.
```

Both the HIGH-weaker count and the correlation tables must be fully filled. The
"largest quartile" lines come from the `[최대규모 요약]` block.

- [ ] **Step 3: Fill the placeholders**

Replace every `<fill ...>` marker with real figures from `/tmp/ipo_size_run.txt`,
and replace the Interpretation instruction with 3–5 honest sentences.

- [ ] **Step 4: Verify no placeholders remain**

Run: `grep -nE '<fill|\.\.\.|TBD|TODO' BACKTESTS.md`
Expected: no output (exit code 1).

- [ ] **Step 5: Commit**

```bash
git add BACKTESTS.md
git commit -m "Document IPO size & clustering backtest as BACKTESTS.md section 8"
```

---

## Task 8: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run both pure-metric test suites**

Run: `cd backtest && python3 test_ipo_drift_metrics.py && python3 test_ipo_size_metrics.py`
Expected: both print all `PASS` lines and their `All ... tests passed.` footer.

- [ ] **Step 2: Full backtest run**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_ipo_size_cluster.py`
Expected: config block, 3-step download/compute, three `[분할]` blocks, `[상관계수]`
block, `[최대규모 요약]` block, `CSV 저장:` line. No traceback.

- [ ] **Step 3: Confirm clean tree**

Run: `git status --porcelain`
Expected: empty — `results_ipo_size_cluster.csv` is gitignored, every source change committed.

---

## Self-Review

- **Spec coverage:** new self-contained script reusing only `ipo_drift_metrics` (Task 2 imports) ✓; `IPO_UNIVERSE_SIZED` 5-tuple with deal size + market cap, direct-listing comment (Task 2) ✓; cluster intensity = ±90d universe deal-size sum incl. self (Task 1 `cluster_intensity`, Task 3 `compute_events`) ✓; median 2-split on all three variables (Task 4) ✓; Part B SPY/QQQ forward returns vs 2018–2025 baseline (Task 3) ✓; Pearson correlation, descriptive, no p-values (Task 5 `print_correlations`) ✓; largest-bucket summary with Anthropic/OpenAI/SpaceX note (Task 5 `print_summary`) ✓; CSV export (Task 6) ✓; BACKTESTS.md section 8 with all caveats (Task 7) ✓; YAGNI — no Part A size analysis, no market-wide IPO totals, no regression, no scanner integration ✓.
- **Placeholder scan:** Task 7 writes a template with explicit `<fill ...>` markers then fills them in Step 3, with Step 4 grep-gating that none survive. No other placeholders; all code blocks are complete.
- **Type consistency:** `cluster_intensity(universe, event_idx, window_days)`, `median_split(events, key) -> (high, low)`, `pearson(xs, ys) -> float | None` are used identically in Tasks 3–5. Event row dict keys (`ticker`, `ipo_date`, `ai`, `deal_size_b`, `mktcap_b`, `cluster_intensity`, `SPY_{h}d`, `QQQ_{h}d`) are consistent across `compute_events`, `print_split`, `print_correlations`, `print_summary`, `save_csv`. `_diff` defined in Task 4 and reused in Task 5. `forward_return`/`summarize` reused from the existing `ipo_drift_metrics` module.
