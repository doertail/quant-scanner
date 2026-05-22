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
