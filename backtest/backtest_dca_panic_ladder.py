"""매달 같은 예산을 '조금씩 적립 + 공포 때 모은 돈 분할 영끌'로 쓰면 전부 적립보다 나은가.

매 거래일 예산 $600×12/252 중 일부는 QQQ 적립, 나머지는 공포 대기 자금(pot)으로.
공포 = 전일 종가 VIX > 30 (VIX < 20으로 내려와야 다음 공포로 재무장).
공포가 시작되면 그때 pot을 사다리 단계 수로 나눠, 전일 VIX가 각 단계를 넘을 때마다
다음 날 시가에 한 몫씩 산다. 공포 중 새로 생기는 대기분은 바로 산다.
flush=True면 공포가 끝날 때(전일 VIX < 20) 안 쓴 pot을 전부 산다.
pot은 현금(0%) 또는 ^IRX 단기국채 수익률로 불어난다. 모든 매수에 0.1% 비용.
QQQM 상장 전 기간은 같은 지수의 QQQ로 대신한다. 매도 없음.

Run: python backtest/backtest_dca_panic_ladder.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from btlib import risk_free_daily

START = "1999-03-10"
MONTHLY = 600.0
DAILY = MONTHLY * 12 / 252
BUY_COST = 0.001
ARM_BELOW, PANIC_ABOVE = 20.0, 30.0
HORIZONS = {"3년": 756, "5년": 1260}
STEP = 21  # 매달 시작점을 하나씩 옮긴다
DCA_SPLITS = (600, 500, 420, 300, 200)
LADDERS = {"30 한방": (30,), "30/35/40": (30, 35, 40), "30/40/50": (30, 40, 50)}


def simulate(op, cl, vix_prev, rf, i0, i1, dca_month, ladder, flush=True):
    """[i0, i1] 구간 시뮬레이션. vix_prev[i] = i 전일 종가 VIX (신호 → i일 시가 체결).
    반환 (최종 평가액, 기간 중 최저 '평가액/누적납입-1', 공포 매수 횟수)."""
    dca = DAILY * dca_month / MONTHLY
    save = DAILY - dca
    shares = pot = paid = 0.0
    worst = 0.0
    active, done, tranche, buys = False, 0, 0.0, 0
    for i in range(i0, i1 + 1):
        px, v = op[i], vix_prev[i]
        pot *= 1 + rf[i]
        paid += DAILY
        buy = dca
        if not active and v > PANIC_ABOVE:
            active, done, tranche = True, 0, pot / len(ladder)
        if active:
            buy += save  # 공포 중 새 대기분은 바로
            while done < len(ladder) and v > ladder[done]:
                take = min(tranche, pot)
                pot -= take
                buy += take
                done += 1
                buys += take > 0
            if v < ARM_BELOW:
                active = False
                if flush and pot > 0:
                    buy += pot
                    pot = 0.0
                    buys += 1
        else:
            pot += save
        shares += buy * (1 - BUY_COST) / px
        worst = min(worst, (shares * cl[i] + pot) / paid - 1)
    return shares * cl[i1] + pot, worst, buys


def load():
    q = yf.download("QQQ", start=START, auto_adjust=True, progress=False)
    v = yf.download("^VIX", start=START, auto_adjust=True, progress=False)["Close"]
    op, cl = q["Open"], q["Close"]
    if isinstance(op, pd.DataFrame):
        op, cl, v = op.iloc[:, 0], cl.iloc[:, 0], v.iloc[:, 0]
    d = pd.concat({"op": op, "cl": cl, "vix": v}, axis=1).dropna()
    d["vix_prev"] = d["vix"].shift(1)
    d = d.dropna()
    d["rf"] = risk_free_daily(d.index).values
    return d


def run(d):
    op, cl, vp, rf = (d[c].to_numpy() for c in ("op", "cl", "vix_prev", "rf"))
    zero = np.zeros_like(rf)
    print(f"QQQ/VIX {d.index[0].date()} ~ {d.index[-1].date()}  매달 ${MONTHLY:.0f}, 시작점 {STEP}거래일 간격")
    for hname, h in HORIZONS.items():
        starts = range(0, len(d) - h, STEP)
        base = np.array([simulate(op, cl, vp, rf, s, s + h - 1, MONTHLY, (30,))[0] for s in starts])
        print(f"\n=== {hname} ({len(base)}개 구간) — 기준: 매달 $600 전부 적립 ===")
        print(f"{'적립/모으기':<11}{'사다리':<10}{'flush':<6}{'pot':<6}"
              f"{'중앙 차이':>9}{'이긴비율':>9}{'최악':>8}{'최고':>8}{'최저평가손익':>12}")
        for dca in DCA_SPLITS[1:]:
            for lname, lad in LADDERS.items():
                for flush in (True, False):
                    for rname, rfs in (("국채", rf), ("현금", zero)):
                        if (not flush or rname == "현금") and (dca != 420 or lname != "30/35/40"):
                            continue  # 변형 효과는 지금 비율($420)·추천 사다리에서만 본다
                        res = [simulate(op, cl, vp, rfs, s, s + h - 1, dca, lad, flush) for s in starts]
                        fin = np.array([r[0] for r in res])
                        diff = fin / base - 1
                        print(f"${dca}/${MONTHLY - dca:<6.0f}{lname:<10}{'O' if flush else 'X':<6}{rname:<6}"
                              f"{np.median(diff):>+9.2%}{(diff > 0).mean():>9.0%}"
                              f"{diff.min():>+8.1%}{diff.max():>+8.1%}"
                              f"{np.median([r[1] for r in res]):>+12.1%}")
        bw = [simulate(op, cl, vp, rf, s, s + h - 1, MONTHLY, (30,))[1] for s in starts]
        print(f"(기준 전부 적립의 최저평가손익 중앙값 {np.median(bw):+.1%})")


if __name__ == "__main__":
    run(load())
