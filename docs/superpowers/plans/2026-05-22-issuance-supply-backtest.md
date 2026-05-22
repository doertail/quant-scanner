# Issuance Supply-Shock Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained backtest that tests whether years of heavy market-wide equity issuance precede weaker SPY/QQQ forward returns — a descriptive case study (N=8 annual data points), not a statistical test.

**Architecture:** One new file `backtest/backtest_issuance_supply.py`, matching the project's one-file-per-backtest convention. It hardcodes 8 years (2018–2025) of approximate US issuance figures, downloads SPY/QQQ from yfinance, measures forward returns from each year's start, and prints an annual table, a high/low-issuance split, a correlation line, and a 2026-scenario summary. It reuses the already-shared pure modules `ipo_drift_metrics.py` (`forward_return`, `summarize`) and `ipo_size_metrics.py` (`median_split`, `pearson`) — no new pure functions, so there is no new unit-test module.

**Tech Stack:** Python 3, yfinance, pandas (already in `requirements.txt`).

**Reference spec:** `docs/superpowers/specs/2026-05-22-issuance-supply-design.md`

**Environment note:** A virtualenv with pandas/numpy/yfinance is at `/Users/jihun/Downloads/workspace/quant-scanner/venv/`. Run the script with `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python` — plain `python3` lacks pandas. The branch is `ipo-drift-backtest`; stay on it.

---

## File Structure

- Create: `backtest/backtest_issuance_supply.py` — the entire backtest.
- Modify: `BACKTESTS.md` — append section 9.

No new pure module: all pure helpers (`forward_return`, `summarize`, `median_split`, `pearson`) already exist and are tested.

---

## Task 1: Script skeleton — annual issuance constant and config block

**Files:**
- Create: `backtest/backtest_issuance_supply.py`

- [ ] **Step 1: Write the skeleton**

Create `backtest/backtest_issuance_supply.py` with EXACTLY this content:

```python
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


def main() -> None:
    print_config()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify the skeleton executes**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py`
Expected: prints the `=== Issuance Supply-Shock Backtest ===` config block (연도 8개) and exits cleanly, no traceback.

- [ ] **Step 3: Commit**

```bash
git add backtest/backtest_issuance_supply.py
git commit -m "Add issuance supply-shock backtest skeleton with annual data"
```

---

## Task 2: Data fetch, baseline, and per-year computation

**Files:**
- Modify: `backtest/backtest_issuance_supply.py`

- [ ] **Step 1: Add the data functions**

Insert these four functions into `backtest/backtest_issuance_supply.py`, placed BEFORE `main()`:

```python
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
```

- [ ] **Step 2: Wire a smoke check into main()**

Replace the body of `main()` so it reads EXACTLY:

```python
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
```

- [ ] **Step 3: Run to verify**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py`
Expected: config block, `[1/2]` shows SPY/QQQ bar counts in the low thousands, `[2/2]` reports `연도 8개` and a baseline percentage. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_issuance_supply.py
git commit -m "Add fetch, baseline, and per-year computation for issuance backtest"
```

---

## Task 3: Annual issuance × forward-return table

**Files:**
- Modify: `backtest/backtest_issuance_supply.py`

- [ ] **Step 1: Add the table function**

Insert this function BEFORE `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    print()
    print_year_table(years)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py`
Expected: a `[연도별 발행액 × forward 시장 수익률]` table with 8 rows, sorted so 2021 (Total $B 435) is first and 2022 (110) near the bottom. Each row shows SPY/QQQ 126d and 252d returns. No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_issuance_supply.py
git commit -m "Add annual issuance vs forward-return table"
```

---

## Task 4: High/low-issuance split and correlation

**Files:**
- Modify: `backtest/backtest_issuance_supply.py`

- [ ] **Step 1: Add the split and correlation functions**

Insert these three functions BEFORE `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    print_split(years, baseline)
    print_correlations(years)
```

- [ ] **Step 3: Run and eyeball**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py`
Expected: after the year table, a `[상·하위 절반 비교]` block (HIGH/LOW rows per horizon, 4 years per bucket) and a `[상관계수]` block (rows ipo_proceeds / total_issuance, columns SPY/QQQ × 126d/252d). No traceback.

- [ ] **Step 4: Commit**

```bash
git add backtest/backtest_issuance_supply.py
git commit -m "Add high/low-issuance split and correlation"
```

---

## Task 5: 2026-scenario summary and CSV export

**Files:**
- Modify: `backtest/backtest_issuance_supply.py`

- [ ] **Step 1: Add the summary and export functions**

Insert these two functions BEFORE `main()`:

```python
def print_summary(years: list[dict]) -> None:
    """List the three highest-issuance years and the market that followed."""
    top = sorted(years, key=lambda e: e["total_issuance_b"],
                 reverse=True)[:3]
    print("[2026 시나리오 요약]")
    print("  역대 최고 발행 연도 3개 — 그 해 연초 진입 시 forward 시장:")
    for r in top:
        s252 = r["SPY_252d"]
        q252 = r["QQQ_252d"]
        s = f"{s252*100:+.2f}%" if s252 is not None else "—"
        q = f"{q252*100:+.2f}%" if q252 is not None else "—"
        print(f"    {r['year']}: 전체발행 ${r['total_issuance_b']:.0f}B → "
              f"SPY 252d {s} | QQQ 252d {q}")
    print("  Anthropic/OpenAI/SpaceX 동시 상장(2026)은 위 고발행 연도와 성격이")
    print("  가깝다 — 단 N=8 서술 사례이고 발행은 내생적(시장이 뜨거울 때 발행)")
    print("  이라, 예측이 아니라 정황 참고임에 유의.")
    print()


def save_csv(years: list[dict]) -> Path:
    """Write per-year rows (issuance fields + forward returns) to CSV."""
    out = Path(__file__).resolve().parent / "results_issuance_supply.csv"
    pd.DataFrame(years).to_csv(out, index=False)
    return out
```

- [ ] **Step 2: Wire into main()**

Append to the END of `main()`:

```python
    print_summary(years)
    out = save_csv(years)
    print(f"CSV 저장: {out}")
```

- [ ] **Step 3: Run and verify the CSV**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py && head -3 results_issuance_supply.csv`
Expected: a `[2026 시나리오 요약]` block listing 3 years, a final `CSV 저장:` line; the CSV header lists `year,ipo_proceeds_b,total_issuance_b,SPY_126d,SPY_252d,QQQ_126d,QQQ_252d` with data rows.

- [ ] **Step 4: Confirm the CSV is gitignored**

Run: `git status --porcelain`
Expected: `backtest/results_issuance_supply.csv` does NOT appear — it is covered by the existing `.gitignore` pattern `backtest/results_*.csv`. If it DOES appear as untracked, report it and do not commit the CSV.

- [ ] **Step 5: Commit**

```bash
git add backtest/backtest_issuance_supply.py
git commit -m "Add 2026-scenario summary and CSV export"
```

---

## Task 6: BACKTESTS.md section 9

**Files:**
- Modify: `BACKTESTS.md`

- [ ] **Step 1: Capture a real run**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py | tee /tmp/issuance_run.txt`
Read `/tmp/issuance_run.txt`. You MUST use its actual numbers — do not invent any figure.

- [ ] **Step 2: Append section 9**

In `BACKTESTS.md`, immediately BEFORE the `## Caveats Common to All Backtests` heading, insert a new section. Use this structure, filling EVERY `<fill ...>` marker with real numbers from the run:

```markdown
## 9. Issuance Supply Shock — Market-Wide New Equity Supply (`backtest_issuance_supply.py`)

**Question**: Sections 7–8 looked at individual IPOs. This zooms out: in years
when *total* US new equity issuance is heavy, does the broad market (SPY/QQQ)
deliver weaker forward returns — the "supply shock drains the market" idea behind
a 2026 with Anthropic, OpenAI, and SpaceX all listing?

**Setup**: Eight years (2018–2025), each hardcoded with an approximate US IPO
proceeds figure and an approximate total-issuance figure (IPO + follow-on).
Forward SPY/QQQ returns are measured from each year's start over 126 and 252
trading days, compared against the unconditional 2018–2025 baseline. With only
8 data points this is a **descriptive case study, not a statistical test**.

### Result

**Annual issuance vs forward return (highest total issuance first)**

| Year | IPO $B | Total $B | SPY 252d |
|---|---|---|---|
| <fill all 8 rows from the year table, in the printed order> |

**High vs low total-issuance half (4 years each) — SPY 252d**

| Bucket | SPY mean | SPY diff vs baseline |
|---|---|---|
| HIGH | <fill> | <fill> |
| LOW | <fill> | <fill> |

**Correlation (Pearson r, N=8 — not significant, reference only)**:
total issuance vs SPY 252d = <fill>; vs QQQ 252d = <fill>.

**Interpretation**: Write 3–5 honest sentences from the actual numbers. Say
plainly whether heavy-issuance years were followed by weaker markets. Then state
the endogeneity limit directly: issuance is highest when markets are euphoric, so
even a negative pattern cannot show that supply *caused* weakness — euphoria
driving both is an equally good explanation. Do not overclaim from 8 points.

⚠️ **N=8 — not statistics.** No regression, p-values, or confidence intervals.
Issuance figures are rounded public aggregates; total issuance (incl. follow-ons)
is the rougher of the two. **Endogeneity**: firms issue into hot markets, so
high-issuance years cluster near tops because issuance and overvaluation share a
cause — this backtest cannot separate "supply pressure" from "an overheated market
mean-reverting." Consecutive years' forward windows overlap. Anthropic, OpenAI,
and SpaceX are private, not in the data, and not backtested.
```

The year table must include all 8 rows. The "<fill>" markers in the split and
correlation lines come from the `[상·하위 절반 비교]` and `[상관계수]` blocks.

- [ ] **Step 3: Fill the placeholders**

Replace every `<fill ...>` marker with real figures from `/tmp/issuance_run.txt`,
and replace the Interpretation instruction with 3–5 honest sentences.

- [ ] **Step 4: Verify no placeholders remain**

Run: `grep -nE '<fill|TBD|TODO' BACKTESTS.md`
Expected: no output (exit code 1).

- [ ] **Step 5: Commit**

```bash
git add BACKTESTS.md
git commit -m "Document issuance supply-shock backtest as BACKTESTS.md section 9"
```

---

## Task 7: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run all three pure-metric test suites**

Run: `cd backtest && python3 test_ipo_drift_metrics.py && python3 test_ipo_size_metrics.py`
Expected: both print all `PASS` lines and their `All ... tests passed.` footer. (These cover the reused pure modules; this backtest adds no new pure functions.)

- [ ] **Step 2: Full backtest run**

Run: `cd backtest && /Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python backtest_issuance_supply.py`
Expected: config block, 2-step download/compute, `[연도별 발행액 …]` table,
`[상·하위 절반 비교]` block, `[상관계수]` block, `[2026 시나리오 요약]` block,
`CSV 저장:` line. No traceback.

- [ ] **Step 3: Confirm clean tree**

Run: `git status --porcelain`
Expected: empty — `results_issuance_supply.csv` is gitignored, every source change committed.

---

## Self-Review

- **Spec coverage:** new self-contained script reusing `ipo_drift_metrics` + `ipo_size_metrics`, no new pure module (Task 1 imports) ✓; `ANNUAL_ISSUANCE` 4-tuple with both ipo_proceeds and total_issuance (Task 1) ✓; forward SPY/QQQ returns at 126/252d from year start vs 2018–2025 baseline (Task 2) ✓; annual table sorted by issuance (Task 3) ✓; median high/low split, flagged as case comparison (Task 4 `print_split`) ✓; Pearson correlation labelled N=8/not-significant (Task 4 `print_correlations`) ✓; 2026-scenario summary naming the three companies as context-only (Task 5 `print_summary`) ✓; CSV export (Task 5) ✓; BACKTESTS.md section 9 with the N=8 and endogeneity caveats (Task 6) ✓; YAGNI — no quarterly data, no regression, no shock-ratio function (none added) ✓.
- **Placeholder scan:** Task 6 writes a template with explicit `<fill ...>` markers then fills them in Step 3, with Step 4 grep-gating that none survive. All code blocks are complete.
- **Type consistency:** year row dict keys (`year`, `ipo_proceeds_b`, `total_issuance_b`, `SPY_{h}d`, `QQQ_{h}d`) are consistent across `compute_years`, `print_year_table`, `print_split`, `print_correlations`, `print_summary`, `save_csv`. `baseline` is `dict[str, dict[int, float]]` keyed `baseline["SPY"][h]` in both `compute_baseline` and `print_split`. `_diff` defined in Task 4 and used only there. `median_split(events, key) -> (high, low)` and `pearson(xs, ys) -> float | None` reused from `ipo_size_metrics`; `forward_return`/`summarize` reused from `ipo_drift_metrics`.
