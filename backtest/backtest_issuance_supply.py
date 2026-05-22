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
