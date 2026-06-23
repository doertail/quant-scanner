"""
backtest_dca_vs_dip.py — "그냥 적립" vs "쌀 때만 사기" vs "하이브리드" (QQQ)

같은 총투입금($1000/월)을 어떻게 '배치'하느냐만 다르게 해서 비교한다.
유휴 현금엔 연 2% 이자(현실적 — HYSA/단기채)를 줘서 딥 전략에 공정하게.

  DCA       : 매월 $1000 즉시 QQQ 매수
  DIP-N     : 매월 $1000을 현금에 적립, 고점대비 −N% 조정(딥)일 때 현금 전액 투입
  HYBRID    : 매월 $700 즉시 매수 + $300 적립, 딥(−10%)에 적립분 투입

총투입금이 동일하므로 최종 평가액이 곧 우열. MDD도 함께 본다.
주의: QQQ는 지수 ETF라 생존편향 없음. 세금·거래비용 미반영(셋 다 동일 조건이라 비교엔 무영향).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import yfinance as yf

START = "2005-01-01"
MONTHLY = 1000.0
CASH_YIELD_D = 0.02 / 252.0   # 유휴현금 연 2%


def simulate(prices: pd.Series, mode: str, dip_pct: float = 0.10):
    shares = 0.0
    reserve = 0.0
    peak = prices.iloc[0]
    contributed = 0.0
    equity = []
    months_seen = set()

    for date, px in prices.items():
        peak = max(peak, px)
        ym = (date.year, date.month)
        first_of_month = ym not in months_seen
        if first_of_month:
            months_seen.add(ym)
            contributed += MONTHLY
            if mode == "DCA":
                shares += MONTHLY / px
            elif mode == "DIP":
                reserve += MONTHLY
            elif mode == "HYBRID":
                shares += 0.7 * MONTHLY / px
                reserve += 0.3 * MONTHLY

        reserve *= (1 + CASH_YIELD_D)  # 현금 이자

        drawdown = px / peak - 1.0
        if mode in ("DIP", "HYBRID") and reserve > 0 and drawdown <= -dip_pct:
            shares += reserve / px
            reserve = 0.0

        equity.append(shares * px + reserve)

    eq = pd.Series(equity, index=prices.index)
    final = eq.iloc[-1]
    mdd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    return {"final": final, "contributed": contributed, "mult": final / contributed, "mdd": mdd}


def main():
    print(f"QQQ 다운로드 ({START}~)...")
    raw = yf.download("QQQ", start=START, progress=False, multi_level_index=False)
    px = raw["Close"].dropna()
    yrs = len(px) / 252.0
    print(f"기간 {px.index[0].date()}~{px.index[-1].date()} ({yrs:.1f}년), 월 ${MONTHLY:,.0f} 적립\n")

    configs = [
        ("DCA (매일/매월 적립)", "DCA", None),
        ("DIP −10% (쌀 때만)", "DIP", 0.10),
        ("DIP −20% (더 쌀 때만)", "DIP", 0.20),
        ("HYBRID (적립+딥추가)", "HYBRID", 0.10),
    ]
    rows = [(name, simulate(px, mode, dip or 0.10)) for name, mode, dip in configs]

    W = 80
    print("=" * W)
    print("  QQQ 적립 방식 비교 — 같은 총투입금, 배치 방식만 다름".center(W - 10))
    print("=" * W)
    print(f"{'방식':<24}{'총투입':>12}{'최종평가':>14}{'배수':>8}{'MDD':>9}")
    print("-" * W)
    dca_final = rows[0][1]["final"]
    for name, r in rows:
        vs = (r["final"] / dca_final - 1) * 100
        vstxt = "" if name.startswith("DCA") else f"  (DCA比 {vs:+.1f}%)"
        print(f"{name:<24}${r['contributed']:>10,.0f}${r['final']:>12,.0f}{r['mult']:>7.2f}x{r['mdd']:>8.1f}%{vstxt}")
    print("-" * W)
    best = max(rows, key=lambda x: x[1]["final"])
    print(f"\n최고 최종평가: {best[0]}")
    print("* 총투입 동일 → 최종평가가 곧 우열. 유휴현금 연2% 반영. QQQ 지수라 생존편향 없음.")


if __name__ == "__main__":
    main()
