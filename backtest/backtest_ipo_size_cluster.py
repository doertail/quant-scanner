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
