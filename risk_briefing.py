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
import os
import sys
from pathlib import Path

import yfinance as yf

from notify import send_discord
from risk_dashboard import (
    grade_regime, grade_trend, grade_breadth, grade_credit,
    grade_vix, grade_yield_spread, grade_overall, diff_grades,
    normalize_yield,
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
        ten = normalize_yield(float(tnx.iloc[-1]))
        three = normalize_yield(float(irx.iloc[-1]))
        return ten - three
    except Exception as exc:
        print(f"  [warn] 수익률곡선 데이터 실패: {exc}")
        return None


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
    details["breadth"] = f"{breadth:.1f}%" if breadth is not None else "데이터 없음"

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


def run_briefing(signals_path: Path = DEFAULT_SIGNALS,
                 dry_run: bool = False) -> None:
    """Grade the risk dashboard from signals.json and send/print the briefing.

    Raises FileNotFoundError if `signals_path` is missing, or ValueError if the
    file has no regime block. The CLI entry point (`main`) turns these into exit
    codes; in-process callers such as scanner_v4 should wrap this in try/except.
    """
    signals = load_signals(signals_path)
    regime = signals.get("regime", {})
    if not regime:
        raise ValueError("signals.json에 regime 블록이 없습니다")
    print(f"signals 로드: date={signals.get('date')} "
          f"regime={regime.get('market_regime')}")

    trend = fetch_trend()
    yield_spread = fetch_yield_spread()
    grades, details = build_dashboard(regime, trend, yield_spread)
    overall = grade_overall(grades)
    prev = load_state()
    changes = diff_grades(prev.get("grades", {}), grades)
    message = format_briefing(
        str(signals.get("date", "?")), overall, grades, details,
        changes, has_prev=bool(prev),
    )

    if dry_run:
        print(message)
        print("\n(미리보기 — Discord 미발송, state 미변경)")
        return

    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        print("[warn] DISCORD_WEBHOOK_URL 미설정 — 발송 건너뜀")
    else:
        send_discord(message)
        print("Discord 발송 완료")
    save_state(grades, overall)


def main() -> None:
    parser = argparse.ArgumentParser(description="시장 위험 브리핑")
    parser.add_argument("--signals", default=str(DEFAULT_SIGNALS),
                        help="signals.json 경로")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discord 발송 대신 stdout 출력 (state를 바꾸지 않음)")
    args = parser.parse_args()

    try:
        run_briefing(Path(args.signals), args.dry_run)
    except FileNotFoundError:
        print(f"[error] signals 파일 없음: {args.signals} "
              f"— scanner_v4.py를 먼저 실행하세요")
        sys.exit(1)
    except ValueError as exc:
        print(f"[error] {exc} — 6개 지표 중 4개의 근거가 사라져 브리핑이 "
              f"오해를 부릅니다. scanner_v4.py 재실행 필요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
