# IPO Drift Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained event-study backtest that tests "stock declines after a large IPO" — Part A (the IPO stock itself) and Part B (SPY/QQQ market trend after IPO events, with an AI-IPO subset).

**Architecture:** Two new files under `backtest/`. `ipo_drift_metrics.py` holds pure dependency-free math (forward return, summary stats) so it is unit-testable with the bare interpreter. `backtest_ipo_drift.py` is the runnable script: hardcoded IPO universe, yfinance download, Part A / Part B computation, console tables, CSV export — matching the pattern of `backtest_macro_regime_ablation.py`. `BACKTESTS.md` gets a new section 7.

**Tech Stack:** Python 3, yfinance, pandas, numpy (already in `requirements.txt`). No pytest — pure-function tests are plain `assert` scripts run with `python3`.

**Reference spec:** `docs/superpowers/specs/2026-05-22-ipo-drift-backtest-design.md`

**Environment note:** The project has no virtualenv. The engineer must run in an environment with `pip install -r requirements.txt` satisfied before Tasks 3+. Tasks 1–2 (pure module + its test) need only the standard library.

---

## File Structure

- Create: `backtest/ipo_drift_metrics.py` — pure functions: `forward_return`, `summarize`. No third-party imports.
- Create: `backtest/test_ipo_drift_metrics.py` — plain-`assert` tests for the metrics module, run with `python3`.
- Create: `backtest/backtest_ipo_drift.py` — main script: universe constant, data fetch, Part A, Part B, output, CSV.
- Modify: `BACKTESTS.md` — append section 7.

---

## Task 1: Pure metrics module

**Files:**
- Create: `backtest/ipo_drift_metrics.py`
- Test: `backtest/test_ipo_drift_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `backtest/test_ipo_drift_metrics.py`:

```python
"""Plain-assert tests for ipo_drift_metrics. Run: python3 backtest/test_ipo_drift_metrics.py"""
from ipo_drift_metrics import forward_return, summarize


def test_forward_return_basic():
    closes = [100.0, 110.0, 121.0, 90.0]
    assert forward_return(closes, 0, 1) == 0.10
    assert abs(forward_return(closes, 0, 2) - 0.21) < 1e-9
    assert forward_return(closes, 0, 3) == -0.10


def test_forward_return_out_of_range():
    closes = [100.0, 110.0]
    assert forward_return(closes, 0, 5) is None
    assert forward_return(closes, 1, 1) is None


def test_forward_return_zero_entry():
    assert forward_return([0.0, 50.0], 0, 1) is None


def test_summarize_basic():
    s = summarize([0.10, -0.05, 0.20, -0.10])
    assert s["n"] == 4
    assert abs(s["mean"] - 0.0375) < 1e-9
    assert abs(s["median"] - 0.025) < 1e-9
    assert s["win_rate"] == 0.5


def test_summarize_odd_median():
    s = summarize([0.10, -0.05, 0.20])
    assert abs(s["median"] - 0.10) < 1e-9


def test_summarize_empty():
    s = summarize([])
    assert s == {"n": 0, "mean": None, "median": None, "win_rate": None}


def test_summarize_ignores_none():
    s = summarize([0.10, None, -0.10, None])
    assert s["n"] == 2
    assert s["win_rate"] == 0.5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All metrics tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backtest && python3 test_ipo_drift_metrics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'ipo_drift_metrics'`

- [ ] **Step 3: Write minimal implementation**

Create `backtest/ipo_drift_metrics.py`:

```python
"""Pure, dependency-free metrics for the IPO drift backtest.

Kept import-free so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations


def forward_return(closes: list[float], idx: int, horizon: int) -> float | None:
    """Return closes[idx+horizon]/closes[idx]-1, or None if out of range / bad entry."""
    end = idx + horizon
    if idx < 0 or end >= len(closes):
        return None
    entry = closes[idx]
    if entry is None or entry <= 0:
        return None
    exit_px = closes[end]
    if exit_px is None:
        return None
    return exit_px / entry - 1.0


def summarize(values: list[float | None]) -> dict:
    """Aggregate a list of returns. None values are dropped.

    Returns {n, mean, median, win_rate}; all-None/empty -> Nones with n=0.
    """
    clean = [v for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    mean = sum(clean) / n
    ordered = sorted(clean)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    win_rate = sum(1 for v in clean if v > 0) / n
    return {"n": n, "mean": mean, "median": median, "win_rate": win_rate}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backtest && python3 test_ipo_drift_metrics.py`
Expected: PASS — 7 `PASS test_*` lines then `All metrics tests passed.`

- [ ] **Step 5: Commit**

```bash
git add backtest/ipo_drift_metrics.py backtest/test_ipo_drift_metrics.py
git commit -m "Add pure metrics module for IPO drift backtest"
```

---

## Task 2: Script skeleton — universe constant and config block

**Files:**
- Create: `backtest/backtest_ipo_drift.py`

- [ ] **Step 1: Write the skeleton**

Create `backtest/backtest_ipo_drift.py`:

```python
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


def main() -> None:
    print_config()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify the skeleton executes**

Run: `cd backtest && python3 backtest_ipo_drift.py`
Expected: prints the `=== IPO Drift Backtest ===` config block and exits cleanly. (If `ModuleNotFoundError` for numpy/pandas/yfinance, the engineer must `pip install -r requirements.txt` first.)

- [ ] **Step 3: Commit**

```bash
git add backtest/backtest_ipo_drift.py
git commit -m "Add IPO drift backtest skeleton with hardcoded universe"
```

---

## Task 3: Data fetch layer

**Files:**
- Modify: `backtest/backtest_ipo_drift.py`

- [ ] **Step 1: Add the fetch functions**

Insert these functions before `main()`:

```python
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
```

- [ ] **Step 2: Wire a smoke check into main()**

Replace the body of `main()` with:

```python
def main() -> None:
    print_config()
    print("[1/?] IPO 종목 데이터 다운로드...")
    ipo_closes = fetch_ipo_closes()
    print(f"  -> {len(ipo_closes)}개 종목 로드")
    print("[2/?] 시장 데이터(SPY/QQQ) 다운로드...")
    market = fetch_market_closes()
    print(f"  -> SPY {len(market['SPY'])} bars, QQQ {len(market['QQQ'])} bars")
```

- [ ] **Step 3: Run to verify download works**

Run: `cd backtest && python3 backtest_ipo_drift.py`
Expected: config block, then `[1/?]` loads ~28 tickers (warns are acceptable), `[2/?]` shows SPY/QQQ bar counts in the thousands. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_drift.py
git commit -m "Add yfinance fetch layer for IPO drift backtest"
```

---

## Task 4: Part A — IPO stock forward returns

**Files:**
- Modify: `backtest/backtest_ipo_drift.py`

- [ ] **Step 1: Add the Part A computation**

Insert before `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

Append to `main()`:

```python
    part_a = compute_part_a(ipo_closes, market)
    print_part_a(part_a)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && python3 backtest_ipo_drift.py`
Expected: a `[Part A]` block with two groups (전체, AI). Each horizon row shows N (decreasing for longer horizons as recent IPOs lack forward data), mean/median/win%, and mean excess vs SPY. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_drift.py
git commit -m "Add Part A: IPO stock forward returns"
```

---

## Task 5: Part B — market trend vs baseline

**Files:**
- Modify: `backtest/backtest_ipo_drift.py`

- [ ] **Step 1: Add the Part B computation**

Insert before `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

Append to `main()`:

```python
    print("[3/3] 베이스라인 계산...")
    baseline = compute_baseline(market)
    part_b = compute_part_b(ipo_closes, market)
    print()
    print_part_b(part_b, baseline)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && python3 backtest_ipo_drift.py`
Expected: a `[Part B]` block with two groups. Each row shows SPY/QQQ mean, baseline, and signed diff. The interpretation line prints at the end. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_ipo_drift.py
git commit -m "Add Part B: market trend after IPO vs baseline"
```

---

## Task 6: CSV export

**Files:**
- Modify: `backtest/backtest_ipo_drift.py`

- [ ] **Step 1: Add the export function**

Insert before `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

Append to `main()`:

```python
    out = save_csv(part_a, part_b)
    print(f"CSV 저장: {out}")
```

- [ ] **Step 3: Run and verify the CSV**

Run: `cd backtest && python3 backtest_ipo_drift.py && head -3 results_ipo_drift.csv`
Expected: final line `CSV 저장: .../results_ipo_drift.csv`; the CSV header lists `ticker,ipo_date,ai,SPY_5d,...,abs_5d,...,exc_5d,...` and at least one data row.

- [ ] **Step 4: Confirm results CSV is gitignored**

Run: `grep -nE 'results_|\.csv' ../.gitignore`
Expected: a pattern already covers `backtest/results_*.csv` (the other backtests write the same kind of file). If `git status --porcelain` shows `backtest/results_ipo_drift.csv` as untracked, add `backtest/results_*.csv` to `../.gitignore` and commit that change.

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_ipo_drift.py
git commit -m "Add CSV export to IPO drift backtest"
```

---

## Task 7: BACKTESTS.md section 7

**Files:**
- Modify: `BACKTESTS.md`

- [ ] **Step 1: Capture a real run**

Run: `cd backtest && python3 backtest_ipo_drift.py | tee /tmp/ipo_drift_run.txt`
Read `/tmp/ipo_drift_run.txt` and use its actual numbers in the next step — do not invent figures.

- [ ] **Step 2: Append section 7**

In `BACKTESTS.md`, immediately before the `## Caveats Common to All Backtests` heading, insert:

```markdown
## 7. IPO Drift — Price Action After Large IPOs (`backtest_ipo_drift.py`)

**Question**: After a large IPO, does the stock decline (Part A), and does the
broad market (SPY/QQQ) weaken (Part B)? Is the effect stronger for mega-cap AI IPOs?

**Setup**: ~25 large IPOs from 2018–2025 hardcoded with their listing dates, 6 of
them AI-related (SNOW, PLTR, C3.ai, ARM, Astera Labs, CoreWeave). Forward returns
measured at 5/20/60/120/180/252 trading days. Part A enters the IPO stock at its
day-0 close (absolute + SPY-excess). Part B measures SPY/QQQ forward returns from
each IPO day-0 against an unconditional baseline (mean forward return over every
trading day 2018–2025).

### Result

<!-- Fill the two tables below from /tmp/ipo_drift_run.txt -->

**Part A — IPO stock itself**

| Group | Horizon | N | Mean abs | Win% | Mean excess vs SPY |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |

**Part B — market trend (SPY shown; QQQ in script output)**

| Group | Horizon | N | SPY mean | SPY baseline | SPY diff |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |

**Interpretation**: <!-- Write 2–4 honest sentences from the actual numbers.
State plainly whether the data supports or contradicts the hypothesis, for both
Part A and Part B, and call out the AI subset. -->

⚠️ **AI subset N=6** — confidence intervals are very wide; treat the AI rows as
directional only, not conclusive. Forward windows of clustered IPOs (e.g. Sep–Dec
2020) overlap, so observations are not independent — no p-values are reported.
Survivorship bias: delisted large IPOs (WeWork, DIDI) are absent from the universe.
```

- [ ] **Step 3: Fill the placeholders**

Replace the two table bodies and the Interpretation comment with the real figures
from `/tmp/ipo_drift_run.txt`. No `...`, no `<!-- -->` comments may remain.

- [ ] **Step 4: Verify no placeholders remain**

Run: `grep -nE '\.\.\.|<!--|Fill the' BACKTESTS.md`
Expected: no output (exit code 1).

- [ ] **Step 5: Commit**

```bash
git add BACKTESTS.md
git commit -m "Document IPO drift backtest as BACKTESTS.md section 7"
```

---

## Task 8: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Re-run the metrics tests**

Run: `cd backtest && python3 test_ipo_drift_metrics.py`
Expected: all `PASS` lines + `All metrics tests passed.`

- [ ] **Step 2: Full backtest run**

Run: `cd backtest && python3 backtest_ipo_drift.py`
Expected: config block, IPO + market download, `[Part A]` (전체/AI), `[Part B]`
(전체/AI with baseline + diff), interpretation line, `CSV 저장:` line. No traceback.

- [ ] **Step 3: Confirm clean tree**

Run: `git status --porcelain`
Expected: empty — `results_ipo_drift.csv` must be gitignored, every source change committed.

---

## Self-Review

- **Spec coverage:** Part A (Task 4) ✓; Part B + baseline + AI subset (Task 5) ✓; universe of ~25 with 6 AI tags (Task 2) ✓; six horizons incl. 180d lockup (Task 2 `HORIZONS`) ✓; absolute + SPY-excess returns (Task 4) ✓; baseline = all trading days 2018–2025 (Task 5 `compute_baseline`) ✓; config block + tables + CSV output (Tasks 2/4/5/6) ✓; BACKTESTS.md section 7 with caveats (Task 7) ✓; no scanner integration / no upcoming-IPO watch / no significance tests — none added, YAGNI respected ✓.
- **Placeholder scan:** Task 7 deliberately writes a template then fills it from a real run in Step 3, with Step 4 grep-gating that no `...`/`<!--` survive. No other placeholders.
- **Type consistency:** `forward_return(closes, idx, horizon)` and `summarize(values) -> {n,mean,median,win_rate}` are used identically across Tasks 4–6. Row dict keys (`abs_{h}d`, `exc_{h}d`, `SPY_{h}d`, `QQQ_{h}d`, `ticker`, `ipo_date`, `ai`) are consistent between `compute_part_a`, `compute_part_b`, `save_csv`. `_series_to_naive` defined in Task 4 and reused in Task 5.
