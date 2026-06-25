"""
backtest_megacap_dip.py — 메가캡 딥 매수 서브전략 검증 (QQQ 오버레이)

가설: 평소 QQQ 보유하다, MSFT급 메가캡이 RSI<30(과매도)되면 그 슬롯을 해당
메가캡으로 회전 → 반등(RSI>50) 또는 120일 후 QQQ로 복귀. "그냥 QQQ 보유"보다
나은가? (현금 드래그 없이 '회전 타이밍'의 가치만 격리해서 검증)

핵심 질문: 메가캡 오버솔드 회전이 순수 QQQ를 이기는가? 못 이기면 → 그냥 QQQ.

⚠️ 생존편향 결정적: 유니버스 = 오늘의 메가캡(전부 거인이 됨). 과거 딥이 '항상
반등'한 건 걔네가 살아남았기 때문. 실제 forward 엣지는 이보다 훨씬 작다.
거래비용 0.1%/회전 반영. IS/OOS로 시대 견고성도 확인.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트

import numpy as np
import pandas as pd
import yfinance as yf
from indicators import compute_indicators

MEGA = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL", "CRM", "ADBE", "NFLX", "AMD"]
N_SLOTS = 10
RSI_IN, RSI_OUT, MAX_HOLD = 30, 50, 120
COST = 0.001
INIT = 100_000.0


def simulate(qqq, prices, rsi, dates):
    """슬롯 오버레이 시뮬. 모든 시리즈는 dates에 정렬됨(위치 i로 접근)."""
    slots = [{"asset": "QQQ", "shares": INIT / N_SLOTS / qqq.iloc[0], "entry_i": 0}
             for _ in range(N_SLOTS)]
    eq = []
    for i in range(len(dates)):
        qp = qqq.iloc[i]
        # 청산: 메가캡 슬롯 RSI>50 또는 120일 → QQQ 복귀
        for s in slots:
            if s["asset"] == "QQQ":
                continue
            t = s["asset"]
            r, tp = rsi[t].iloc[i], prices[t].iloc[i]
            if np.isnan(tp):
                continue
            if (not np.isnan(r) and r > RSI_OUT) or (i - s["entry_i"] >= MAX_HOLD):
                val = s["shares"] * tp * (1 - COST)
                s.update(asset="QQQ", shares=val / qp * (1 - COST), entry_i=i)
        # 진입: 메가캡 RSI<30 & 미보유 & QQQ 슬롯 여유 → 회전
        held = {s["asset"] for s in slots if s["asset"] != "QQQ"}
        for t in MEGA:
            if t in held:
                continue
            r, tp = rsi[t].iloc[i], prices[t].iloc[i]
            if np.isnan(r) or np.isnan(tp) or r >= RSI_IN:
                continue
            free = next((s for s in slots if s["asset"] == "QQQ"), None)
            if not free:
                break
            val = free["shares"] * qp * (1 - COST)
            free.update(asset=t, shares=val / tp * (1 - COST), entry_i=i)
            held.add(t)
        # 평가
        tot = 0.0
        for s in slots:
            p = qp if s["asset"] == "QQQ" else prices[s["asset"]].iloc[i]
            tot += s["shares"] * (0 if np.isnan(p) else p)
        eq.append(tot)
    return pd.Series(eq, index=dates)


def stats(eq):
    final = eq.iloc[-1]
    yrs = len(eq) / 252
    cagr = (final / INIT) ** (1 / yrs) - 1
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() * 252 - 0.035) / (rets.std() * np.sqrt(252)) if rets.std() else 0
    mdd = ((eq - eq.cummax()) / eq.cummax()).min()
    return final, cagr * 100, mdd * 100, sharpe


def run_window(dates):
    qqq_w = QQQ.reindex(dates)
    prices_w = {t: PRICES[t].reindex(dates) for t in MEGA}
    rsi_w = {t: RSI[t].reindex(dates) for t in MEGA}
    eq = simulate(qqq_w, prices_w, rsi_w, dates)
    bh = INIT / qqq_w.iloc[0] * qqq_w
    return stats(eq), stats(bh)


def main():
    global QQQ, PRICES, RSI
    print(f"다운로드 ({len(MEGA)} 메가캡 + QQQ)...")
    raw = yf.download(MEGA + ["QQQ"], start="2014-01-01", group_by="ticker",
                      threads=True, progress=False)
    QQQ = raw["QQQ"]["Close"].dropna()
    PRICES, RSI = {}, {}
    for t in MEGA:
        d = compute_indicators(raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna())
        PRICES[t] = d["Close"].reindex(QQQ.index)
        RSI[t] = d["RSI"].reindex(QQQ.index)

    full = QQQ.index
    mid = full[len(full) // 2]
    windows = [("전체", full), ("IS 전반", full[full <= mid]), ("OOS 후반", full[full > mid])]

    W = 84
    print("\n" + "=" * W)
    print("  메가캡 딥 매수 오버레이 vs 순수 QQQ".center(W - 8))
    print("=" * W)
    print(f"{'기간':<12}{'주체':<14}{'CAGR':>9}{'MDD':>9}{'Sharpe':>9}{'최종$':>14}")
    print("-" * W)
    for label, dts in windows:
        (sf, sc, sm, ss), (bf, bc, bm, bs) = run_window(dts)
        print(f"{label:<12}{'딥오버레이':<14}{sc:>8.2f}%{sm:>8.1f}%{ss:>9.2f}${sf:>12,.0f}")
        print(f"{'':<12}{'순수 QQQ':<14}{bc:>8.2f}%{bm:>8.1f}%{bs:>9.2f}${bf:>12,.0f}")
        print(f"{'':<12}{'Δ CAGR':<14}{sc-bc:>+8.2f}%{'':<27}{'✅이김' if sc>bc else '❌짐'}")
        print("-" * W)
    print("\n⚠️ 생존편향(오늘의 메가캡만) → 실제 forward 엣지는 이보다 작음. 도입 전 forward 검증 필수.")


if __name__ == "__main__":
    main()
