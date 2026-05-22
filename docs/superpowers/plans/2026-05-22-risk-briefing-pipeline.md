# Risk Briefing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a post-scan pipeline that grades 6 market-risk indicators with deterministic rules, computes an overall risk grade plus a diff vs the previous run, and sends a concise Discord briefing.

**Architecture:** `risk_dashboard.py` holds pure dependency-free grading functions, unit-testable with the bare interpreter. `risk_briefing.py` is the orchestration script: it reads `signals.json` (produced by `scanner_v4.py`), fetches QQQ and the Treasury yield curve from yfinance, grades the dashboard, diffs against `briefing_state.json`, and sends a Discord briefing via the existing `notify.py`. `scanner_v4.py` is not modified.

**Tech Stack:** Python 3, yfinance, the project's `notify.py`. No pytest — pure-function tests are plain `assert` scripts.

**Reference spec:** `docs/superpowers/specs/2026-05-22-risk-briefing-design.md`

**Environment note:** A virtualenv with pandas/numpy/yfinance is at `/Users/jihun/Downloads/workspace/quant-scanner/venv/`. Run `risk_briefing.py` with `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python` — plain `python3` lacks yfinance. Task 1 (pure module + test) needs only the standard library. The branch is `risk-briefing`; stay on it.

**Scope note:** The spec mentioned reading `portfolio.json`; the briefing is market-level and its output never uses portfolio data, so `portfolio.json` is not read (YAGNI).

---

## File Structure

- Create: `risk_dashboard.py` (project root) — pure graders: `grade_regime`, `grade_trend`, `grade_breadth`, `grade_credit`, `grade_vix`, `grade_yield_spread`, `grade_overall`, `diff_grades`.
- Create: `test_risk_dashboard.py` (root) — plain-assert tests, run with `python3`.
- Create: `risk_briefing.py` (root) — orchestration script.
- Modify: `.gitignore` — add `briefing_state.json`.
- Modify: `CLAUDE.md` — add a row for `risk_briefing.py` and a run command.

---

## Task 1: Pure dashboard grading module

**Files:**
- Create: `risk_dashboard.py`
- Test: `test_risk_dashboard.py`

- [ ] **Step 1: Write the failing test**

Create `test_risk_dashboard.py`:

```python
"""Plain-assert tests for risk_dashboard. Run: python3 test_risk_dashboard.py"""
from risk_dashboard import (
    GREEN, YELLOW, RED, UNKNOWN,
    grade_regime, grade_trend, grade_breadth, grade_credit, grade_vix,
    grade_yield_spread, grade_overall, diff_grades,
)


def test_grade_regime():
    assert grade_regime("BULL") == GREEN
    assert grade_regime("SIDEWAYS") == YELLOW
    assert grade_regime("BEAR") == RED
    assert grade_regime(None) == UNKNOWN
    assert grade_regime("???") == UNKNOWN


def test_grade_trend():
    assert grade_trend(100.0, 90.0) == GREEN          # +11%, not extended
    assert grade_trend(110.0, 90.0) == YELLOW         # +22%, extended >15%
    assert grade_trend(85.0, 90.0) == RED             # below MA200
    assert grade_trend(90.0, 90.0) == RED             # equal counts as not above
    assert grade_trend(None, 90.0) == UNKNOWN
    assert grade_trend(100.0, None) == UNKNOWN
    assert grade_trend(100.0, 0.0) == UNKNOWN


def test_grade_breadth():
    assert grade_breadth(62.0) == GREEN
    assert grade_breadth(60.0) == GREEN
    assert grade_breadth(50.0) == YELLOW
    assert grade_breadth(40.0) == YELLOW
    assert grade_breadth(30.0) == RED
    assert grade_breadth(None) == UNKNOWN


def test_grade_credit():
    assert grade_credit(True) == GREEN
    assert grade_credit(False) == RED
    assert grade_credit(None) == UNKNOWN


def test_grade_vix():
    assert grade_vix("NORMAL") == GREEN
    assert grade_vix("SWEET") == YELLOW
    assert grade_vix("DANGER") == RED
    assert grade_vix("PANIC") == RED
    assert grade_vix(None) == UNKNOWN


def test_grade_yield_spread():
    assert grade_yield_spread(1.2, 0.5) == GREEN
    assert grade_yield_spread(0.3, 0.5) == YELLOW
    assert grade_yield_spread(0.0, 0.5) == YELLOW     # 0 is flat, not inverted
    assert grade_yield_spread(-0.4, 0.5) == RED       # inverted
    assert grade_yield_spread(None, 0.5) == UNKNOWN


def test_grade_overall():
    # 0 red, 0-1 yellow -> LOW
    assert grade_overall({"a": GREEN, "b": GREEN, "c": YELLOW}) == "LOW"
    assert grade_overall({"a": GREEN, "b": GREEN}) == "LOW"
    # 1 red OR >=2 yellow -> ELEVATED
    assert grade_overall({"a": RED, "b": GREEN}) == "ELEVATED"
    assert grade_overall({"a": YELLOW, "b": YELLOW, "c": GREEN}) == "ELEVATED"
    # >=2 red -> HIGH
    assert grade_overall({"a": RED, "b": RED, "c": GREEN}) == "HIGH"
    # UNKNOWN is ignored
    assert grade_overall({"a": GREEN, "b": UNKNOWN}) == "LOW"


def test_diff_grades():
    prev = {"regime": GREEN, "credit": GREEN, "vix": YELLOW}
    cur = {"regime": GREEN, "credit": RED, "vix": YELLOW}
    assert diff_grades(prev, cur) == [("credit", GREEN, RED)]
    # nothing changed
    assert diff_grades(cur, cur) == []
    # a component absent from prev is not a "change"
    assert diff_grades({}, {"regime": GREEN}) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All risk-dashboard tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 test_risk_dashboard.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'risk_dashboard'`

- [ ] **Step 3: Write minimal implementation**

Create `risk_dashboard.py`:

```python
"""Pure, dependency-free grading logic for the risk briefing pipeline.

Kept import-free so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations

GREEN, YELLOW, RED, UNKNOWN = "🟢", "🟡", "🔴", "⚪"


def grade_regime(market_regime: str | None) -> str:
    """BULL → green, SIDEWAYS → yellow, BEAR → red, anything else → unknown."""
    return {"BULL": GREEN, "SIDEWAYS": YELLOW, "BEAR": RED}.get(
        market_regime, UNKNOWN)


def grade_trend(qqq_close: float | None, ma200: float | None,
                ext_threshold: float = 0.15) -> str:
    """Red below MA200, yellow if extended > ext_threshold above it, else green."""
    if qqq_close is None or ma200 is None or ma200 <= 0:
        return UNKNOWN
    if qqq_close <= ma200:
        return RED
    if qqq_close / ma200 - 1 > ext_threshold:
        return YELLOW
    return GREEN


def grade_breadth(breadth_pct: float | None) -> str:
    """>=60 green, 40-60 yellow, <40 red."""
    if breadth_pct is None:
        return UNKNOWN
    if breadth_pct >= 60:
        return GREEN
    if breadth_pct >= 40:
        return YELLOW
    return RED


def grade_credit(hyg_ok: bool | None) -> str:
    """HYG above its MA50 → green, below → red."""
    if hyg_ok is None:
        return UNKNOWN
    return GREEN if hyg_ok else RED


def grade_vix(vix_zone: str | None) -> str:
    """NORMAL green, SWEET yellow, DANGER/PANIC red."""
    return {"NORMAL": GREEN, "SWEET": YELLOW,
            "DANGER": RED, "PANIC": RED}.get(vix_zone, UNKNOWN)


def grade_yield_spread(spread: float | None, flat_threshold: float) -> str:
    """Inverted (spread < 0) red, flat (0 to flat_threshold) yellow, else green."""
    if spread is None:
        return UNKNOWN
    if spread < 0:
        return RED
    if spread < flat_threshold:
        return YELLOW
    return GREEN


def grade_overall(grades: dict[str, str]) -> str:
    """Combine component grades into LOW / ELEVATED / HIGH. UNKNOWN is ignored."""
    reds = sum(1 for g in grades.values() if g == RED)
    yellows = sum(1 for g in grades.values() if g == YELLOW)
    if reds >= 2:
        return "HIGH"
    if reds == 1 or yellows >= 2:
        return "ELEVATED"
    return "LOW"


def diff_grades(prev: dict[str, str],
                cur: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return (name, old, new) for components whose grade changed since prev.

    Components absent from prev are not 'changes' and are skipped.
    """
    out: list[tuple[str, str, str]] = []
    for name, g in cur.items():
        if name in prev and prev[name] != g:
            out.append((name, prev[name], g))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 test_risk_dashboard.py`
Expected: PASS — 8 `PASS test_*` lines then `All risk-dashboard tests passed.`

- [ ] **Step 5: Commit**

```bash
git add risk_dashboard.py test_risk_dashboard.py
git commit -m "Add pure grading module for risk briefing pipeline"
```

---

## Task 2: gitignore entry and risk_briefing skeleton

**Files:**
- Modify: `.gitignore`
- Create: `risk_briefing.py`

- [ ] **Step 1: Add briefing_state.json to .gitignore**

Append a line to `.gitignore` (after the existing `signals.json` line):

```
briefing_state.json
```

- [ ] **Step 2: Write the skeleton**

Create `risk_briefing.py` with EXACTLY this content:

```python
"""risk_briefing.py — 6개 시장 위험 지표를 규칙으로 채점해 Discord 브리핑 발송.

scanner_v4.py 직후 실행. signals.json의 거시 레짐 블록 + yfinance(QQQ, 수익률곡선)을
읽어 위험 등급 대시보드를 만들고, 직전 실행(briefing_state.json)과 비교해 바뀐 것을
강조한 뒤 Discord로 보낸다.

⚠️ 예측기가 아니라 상태 모니터다. 임계값은 판단값(미최적화).

설계: docs/superpowers/specs/2026-05-22-risk-briefing-design.md
실행: python risk_briefing.py [--signals PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yfinance as yf

from notify import send_discord
from risk_dashboard import (
    grade_regime, grade_trend, grade_breadth, grade_credit, grade_vix,
    grade_yield_spread, grade_overall, diff_grades,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_SIGNALS = ROOT / "signals.json"
STATE_PATH = ROOT / "briefing_state.json"

EXT_THRESHOLD = 0.15        # QQQ가 MA200보다 15% 넘게 위면 '과열' 노랑
YIELD_FLAT_THRESHOLD = 0.5  # 10년-3개월 스프레드 0.5%p 미만이면 '평탄' 노랑

# (state 키, 표시 라벨) — 대시보드 출력 순서
COMPONENTS = [
    ("regime", "레짐"),
    ("trend", "추세"),
    ("breadth", "시장폭"),
    ("credit", "신용"),
    ("volatility", "변동성"),
    ("yield_curve", "수익률곡선"),
]

POSTURE = {
    "LOW": "보유 유지 — 갈라짐 신호 없음",
    "ELEVATED": "주의 — 신규 비중 확대 자제, 지표 추이 관찰",
    "HIGH": "위험 — 여러 지표 동시 악화, 단계적 축소 검토",
}


def load_signals(path: Path) -> dict:
    """Load the signals.json produced by scanner_v4.py."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="시장 위험 브리핑")
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS),
                        help="signals.json 경로")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discord 발송 대신 stdout 출력")
    args = parser.parse_args()

    signals = load_signals(Path(args.signals))
    regime = signals.get("regime", {})
    print(f"signals 로드: date={signals.get('date')} "
          f"regime={regime.get('market_regime')}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run to verify the skeleton executes**

First create a sample signals file:
```bash
cat > /tmp/sample_signals.json <<'JSON'
{"date": "2026-05-22", "regime": {"market_regime": "BULL", "breadth_pct": 62.0, "vix": 16.9, "vix_zone": "NORMAL", "hyg_ok": true, "bull": true}}
JSON
```
Run: `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals.json`
Expected: prints `signals 로드: date=2026-05-22 regime=BULL` and exits cleanly. No traceback.

- [ ] **Step 4: Commit**

```bash
git add .gitignore risk_briefing.py
git commit -m "Add risk_briefing skeleton and gitignore briefing_state.json"
```

---

## Task 3: yfinance data fetch — trend and yield curve

**Files:**
- Modify: `risk_briefing.py`

- [ ] **Step 1: Add the fetch functions**

Insert these three functions into `risk_briefing.py`, placed BEFORE `main()`:

```python
def fetch_trend() -> tuple[float | None, float | None]:
    """Return (QQQ latest close, QQQ 200-day MA), or (None, None) on failure."""
    try:
        hist = yf.Ticker("QQQ").history(period="400d", auto_adjust=False)
        close = hist["Close"].dropna()
        if len(close) < 200:
            return None, None
        return float(close.iloc[-1]), float(close.rolling(200).mean().iloc[-1])
    except Exception as exc:
        print(f"  [warn] QQQ 추세 데이터 실패: {exc}")
        return None, None


def _normalize_yield(value: float) -> float:
    """Treasury yields are 0-20%. yfinance sometimes scales x10 (e.g. 4.2 -> 42);
    any value above 25 is assumed x10-scaled and divided down.
    """
    return value / 10 if value > 25 else value


def fetch_yield_spread() -> float | None:
    """Return the 10y minus 3m Treasury spread in percentage points, or None.

    Uses ^TNX (10-year) and ^IRX (13-week). Both are normalized so the spread is
    in plain percentage points regardless of yfinance's scaling.
    """
    try:
        tnx = yf.Ticker("^TNX").history(period="5d", auto_adjust=False)["Close"].dropna()
        irx = yf.Ticker("^IRX").history(period="5d", auto_adjust=False)["Close"].dropna()
        if tnx.empty or irx.empty:
            return None
        ten = _normalize_yield(float(tnx.iloc[-1]))
        three = _normalize_yield(float(irx.iloc[-1]))
        return ten - three
    except Exception as exc:
        print(f"  [warn] 수익률곡선 데이터 실패: {exc}")
        return None
```

- [ ] **Step 2: Wire a smoke check into main()**

Append to the END of the existing `main()` body (keep all existing lines):

```python
    qqq_close, qqq_ma200 = fetch_trend()
    yield_spread = fetch_yield_spread()
    print(f"QQQ={qqq_close} MA200={qqq_ma200} | yield spread={yield_spread}")
```

- [ ] **Step 3: Run to verify**

Run: `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals.json`
Expected: after the `signals 로드:` line, a line `QQQ=<num> MA200=<num> | yield spread=<num>`. QQQ should be a few hundred, MA200 lower, yield spread a small number roughly in the −2 to +3 range (plain percentage points — confirms `_normalize_yield` produced a sane scale). No traceback.

- [ ] **Step 4: Commit**

```bash
git add risk_briefing.py
git commit -m "Add trend and yield-curve fetch to risk briefing"
```

---

## Task 4: Build dashboard and read/write state

**Files:**
- Modify: `risk_briefing.py`

- [ ] **Step 1: Add the dashboard and state functions**

Insert these three functions into `risk_briefing.py`, placed BEFORE `main()`:

```python
def build_dashboard(regime: dict, trend: tuple[float | None, float | None],
                    yield_spread: float | None) -> tuple[dict, dict]:
    """Return (grades, details): grades maps component key -> emoji, details
    maps component key -> a short human-readable string.
    """
    qqq, ma200 = trend
    grades: dict[str, str] = {}
    details: dict[str, str] = {}

    grades["regime"] = grade_regime(regime.get("market_regime"))
    details["regime"] = str(regime.get("market_regime") or "데이터 없음")

    grades["trend"] = grade_trend(qqq, ma200, EXT_THRESHOLD)
    if qqq is not None and ma200 is not None and ma200 > 0:
        details["trend"] = f"QQQ vs MA200 {(qqq / ma200 - 1) * 100:+.1f}%"
    else:
        details["trend"] = "데이터 없음"

    breadth = regime.get("breadth_pct")
    grades["breadth"] = grade_breadth(breadth)
    details["breadth"] = f"{breadth:.0f}%" if breadth is not None else "데이터 없음"

    hyg_ok = regime.get("hyg_ok")
    grades["credit"] = grade_credit(hyg_ok)
    if hyg_ok is None:
        details["credit"] = "데이터 없음"
    else:
        details["credit"] = "HYG > MA50" if hyg_ok else "HYG < MA50"

    vix_zone = regime.get("vix_zone")
    grades["volatility"] = grade_vix(vix_zone)
    vix = regime.get("vix")
    if vix is not None and vix_zone:
        details["volatility"] = f"VIX {vix} ({vix_zone})"
    else:
        details["volatility"] = str(vix_zone or "데이터 없음")

    grades["yield_curve"] = grade_yield_spread(yield_spread, YIELD_FLAT_THRESHOLD)
    if yield_spread is not None:
        details["yield_curve"] = f"10y−3m {yield_spread:+.2f}%p"
    else:
        details["yield_curve"] = "데이터 없음"

    return grades, details


def load_state() -> dict:
    """Load the previous briefing's grades, or {} if there is no prior run."""
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(grades: dict, overall: str) -> None:
    """Persist this run's grades and overall grade for the next run's diff."""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"grades": grades, "overall": overall}, f,
                  ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Wire into main()**

Replace the two lines added in Task 3 Step 2 (the `qqq_close, qqq_ma200 = fetch_trend()` line, the `yield_spread = ...` line, and the `print(f"QQQ=...")` line) with:

```python
    trend = fetch_trend()
    yield_spread = fetch_yield_spread()
    grades, details = build_dashboard(regime, trend, yield_spread)
    overall = grade_overall(grades)
    prev = load_state()
    changes = diff_grades(prev.get("grades", {}), grades)
    print(f"overall={overall} | grades={grades} | changes={changes}")
```

- [ ] **Step 3: Run to verify**

Run: `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals.json`
Expected: after the `signals 로드:` line, a line `overall=<LOW|ELEVATED|HIGH> | grades={...6 entries...} | changes=[...]`. With the all-green sample, `overall` should be `LOW` (trend may be 🟡 if QQQ is >15% over MA200, in which case still `LOW` — one yellow). On the first run `changes=[]`. No traceback. (`briefing_state.json` is NOT written yet — that happens in Task 5.)

- [ ] **Step 4: Commit**

```bash
git add risk_briefing.py
git commit -m "Add dashboard build and state persistence to risk briefing"
```

---

## Task 5: Format the briefing and send it

**Files:**
- Modify: `risk_briefing.py`

- [ ] **Step 1: Add the format function**

Insert this function into `risk_briefing.py`, placed BEFORE `main()`:

```python
def format_briefing(date: str, overall: str, grades: dict, details: dict,
                    changes: list[tuple[str, str, str]], has_prev: bool) -> str:
    """Render the Discord briefing message."""
    label = dict(COMPONENTS)
    lines = [
        f"📊 위험 브리핑 — {date}",
        "",
        f"종합 위험 등급: {overall}",
        f"자세: {POSTURE[overall]}",
        "",
        "대시보드",
    ]
    for key, name in COMPONENTS:
        lines.append(f"  {name:<6} {grades[key]} {details[key]}")
    lines.append("")
    lines.append("지난 브리핑 대비 변화")
    if not has_prev:
        lines.append("  - 이전 기록 없음 (첫 실행)")
    elif not changes:
        lines.append("  - 변화 없음")
    else:
        for key, old, new in changes:
            lines.append(f"  - {label.get(key, key)} {old}→{new}")
    lines += [
        "",
        "⚠️ 이건 예측이 아니라 상태 모니터다. 임계값은 판단값(미최적화),",
        "   밸류에이션은 MA200 이격도 프록시뿐. 단일 지표가 아니라 앙상블로 읽을 것.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 2: Replace the rest of main()**

Replace the line added in Task 4 Step 2 (`print(f"overall={overall} ...")`) with:

```python
    message = format_briefing(
        str(signals.get("date", "?")), overall, grades, details,
        changes, has_prev=bool(prev),
    )
    if args.dry_run:
        print(message)
    else:
        send_discord(message)
        print("Discord 발송 완료")
    save_state(grades, overall)
```

- [ ] **Step 3: Run to verify (dry-run)**

Run: `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals.json --dry-run`
Expected: prints the full briefing — `📊 위험 브리핑 — 2026-05-22` header, `종합 위험 등급:` line, `자세:` line, a `대시보드` block with 6 component lines each starting with an emoji, a `지난 브리핑 대비 변화` block (first run → `이전 기록 없음`), and the `⚠️` caveat. No traceback.

- [ ] **Step 4: Run again to verify the diff works**

Run the same command a second time:
`/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals.json --dry-run`
Expected: this time the `지난 브리핑 대비 변화` block shows `변화 없음` (state was written on the first run, grades unchanged). Confirms `briefing_state.json` round-trips.

- [ ] **Step 5: Confirm briefing_state.json is gitignored**

Run: `git status --porcelain`
Expected: `briefing_state.json` does NOT appear (it was added to `.gitignore` in Task 2). If it appears, report it and do not commit it.

- [ ] **Step 6: Commit**

```bash
git add risk_briefing.py
git commit -m "Add briefing formatting and Discord send to risk pipeline"
```

---

## Task 6: Documentation and end-to-end verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add risk_briefing to CLAUDE.md**

In `CLAUDE.md`, find the `### 메인 스크립트` table (it lists `scanner_v4.py`, `execution_layer.py`, `bot.py`). Add this row to the end of that table:

```
| `risk_briefing.py` | 위험 등급 브리핑 — signals.json + 수익률곡선으로 6개 지표 대시보드 채점 후 Discord 발송 |
```

Then in the `## 실행 방법` section, immediately after the `python execution_layer.py` line and its comment, add:

```bash

# 위험 브리핑 (스캔 직후, signals.json 평가)
python risk_briefing.py            # Discord 발송
python risk_briefing.py --dry-run  # 발송 없이 미리보기
```

- [ ] **Step 2: Commit the docs**

```bash
git add CLAUDE.md
git commit -m "Document risk_briefing.py in CLAUDE.md"
```

- [ ] **Step 3: Run the pure-module test suite**

Run: `python3 test_risk_dashboard.py`
Expected: 8 `PASS test_*` lines then `All risk-dashboard tests passed.`

- [ ] **Step 4: End-to-end dry-run with an ELEVATED-risk sample**

Create a second sample with degraded indicators:
```bash
cat > /tmp/sample_signals_bad.json <<'JSON'
{"date": "2026-05-22", "regime": {"market_regime": "SIDEWAYS", "breadth_pct": 38.0, "vix": 27.0, "vix_zone": "DANGER", "hyg_ok": false, "bull": true}}
JSON
```
Run: `/Users/jihun/Downloads/workspace/quant-scanner/venv/bin/python risk_briefing.py --signals /tmp/sample_signals_bad.json --dry-run`
Expected: the briefing renders with `종합 위험 등급: HIGH` (credit 🔴 + volatility 🔴 = 2 reds → HIGH), regime 🟡, breadth 🔴. The `지난 브리핑 대비 변화` block shows changes vs the previous run's all-green state (e.g. `신용 🟢→🔴`). No traceback.

- [ ] **Step 5: Confirm clean tree**

Run: `git status --porcelain`
Expected: empty — `briefing_state.json` is gitignored, every source change committed.

---

## Self-Review

- **Spec coverage:** pure graders for all 6 indicators + overall + diff (Task 1) ✓; `risk_briefing.py` reads signals.json (Task 2) ✓; fetches QQQ trend + yield curve with x10-scale normalization (Task 3) ✓; builds the 6-component dashboard with grades + details (Task 4) ✓; overall grade + posture (Task 1 `grade_overall`, Task 5 `POSTURE`) ✓; diff vs `briefing_state.json` with first-run handling (Tasks 4–5) ✓; concise Discord output via `notify.py`, `--dry-run` mode (Task 5) ✓; `briefing_state.json` gitignored (Task 2) ✓; monitor-not-predictor caveat in the message (Task 5 `format_briefing`) ✓; CLAUDE.md documented (Task 6) ✓; scanner_v4.py untouched, no Gemini, no scheduling, no portfolio.json — YAGNI respected ✓.
- **Placeholder scan:** no TBD/TODO; every code block is complete; the `_normalize_yield` helper removes the yield-scale uncertainty noted in the spec so no implementer judgement call remains.
- **Type consistency:** grade constants `GREEN/YELLOW/RED/UNKNOWN` and the 8 function signatures in `risk_dashboard.py` are used identically by `risk_briefing.py`. `build_dashboard` returns `(grades, details)` dicts keyed by the same component keys (`regime`, `trend`, `breadth`, `credit`, `volatility`, `yield_curve`) used in `COMPONENTS`, consumed identically by `format_briefing` and `save_state`/`load_state`. `fetch_trend` returns `(float|None, float|None)` consumed as `trend` by `build_dashboard`. `diff_grades` consumes `prev.get("grades", {})` matching the `{"grades": ..., "overall": ...}` shape written by `save_state`.
