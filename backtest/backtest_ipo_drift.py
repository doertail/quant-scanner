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
