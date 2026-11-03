"""공개 퀀트 자산배분 전략 vs QQQ 적립 — 매달 $600, 한국 해외주식 양도세 반영.

전략 (월말 종가 신호·체결, 매달 적립금도 목표 비중대로 투입):
  QQQ 적립     매달 QQQ만 산다 (기준)
  QQQ 10M SMA  QQQ > 10개월 이동평균이면 QQQ, 아니면 SHY (Faber 타이밍)
  GTAA5        SPY/EFA/AGG/DBC/VNQ 각 20%, 각자 10개월 SMA 아래면 그 몫은 SHY (Faber 2007)
  GEM(QQQ)     QQQ 12M수익 > SHY 12M수익이면 QQQ·EFA 중 12M수익 큰 쪽, 아니면 AGG (Antonacci)
  VAA-G4(QQQ)  공격 QQQ/EFA/EEM/AGG, 방어 LQD/IEF/SHY. 13612W 모멘텀
               = 12·r1 + 4·r3 + 2·r6 + r12. 공격 4개 모두 > 0이면 공격 1등, 아니면 방어 1등 100% (Keller 2017)
GEM·VAA는 원전의 SPY 자리에 QQQ를 넣었다(비교 대상이 나스닥100 적립이므로).
비용: 매매대금 0.1%. 세금: 연간 실현손익(평균단가, 종목 합산) − $1,850(≈₩250만) 초과분 22%,
연말에 평가액에서 차감. 기간 끝엔 '전량 매도했다면' 세후 금액도 계산.
배당은 수정주가에 포함(미국 원천징수 15%는 미반영 — 모든 전략에 비슷하게 유리).

Run: python backtest/backtest_tactical_vs_dca.py
"""

import numpy as np
import pandas as pd
import yfinance as yf

MONTHLY = 600.0
COST = 0.001
TAX, EXEMPT = 0.22, 1850.0
TICKERS = ["QQQ", "SPY", "EFA", "EEM", "AGG", "DBC", "VNQ", "LQD", "IEF", "SHY"]
HORIZONS = {"3년": 36, "5년": 60}


def load() -> pd.DataFrame:
    px = yf.download(TICKERS, start="2005-01-01", auto_adjust=True, progress=False)["Close"]
    return px.resample("ME").last().dropna()  # DBC 상장(2006-02) 이후로 정렬


def mom(px, i, m):
    return px.iloc[i] / px.iloc[i - m] - 1


def targets(name, px, i):
    """i번째 월말의 목표 비중 {ticker: w}."""
    sma10 = px.iloc[i - 9:i + 1].mean()
    p = px.iloc[i]
    if name == "QQQ 적립":
        return {"QQQ": 1.0}
    if name == "QQQ 10M SMA":
        return {"QQQ": 1.0} if p["QQQ"] > sma10["QQQ"] else {"SHY": 1.0}
    if name == "GTAA5":
        w = {}
        for t in ("SPY", "EFA", "AGG", "DBC", "VNQ"):
            k = t if p[t] > sma10[t] else "SHY"
            w[k] = w.get(k, 0) + 0.2
        return w
    if name == "GEM(QQQ)":
        r12 = mom(px, i, 12)
        if r12["QQQ"] <= r12["SHY"]:
            return {"AGG": 1.0}
        return {"QQQ" if r12["QQQ"] >= r12["EFA"] else "EFA": 1.0}
    if name == "VAA-G4(QQQ)":
        s = 12 * mom(px, i, 1) + 4 * mom(px, i, 3) + 2 * mom(px, i, 6) + mom(px, i, 12)
        off, dfn = ["QQQ", "EFA", "EEM", "AGG"], ["LQD", "IEF", "SHY"]
        pool = off if (s[off] > 0).all() else dfn
        return {s[pool].idxmax(): 1.0}
    raise ValueError(name)


STRATS = ["QQQ 적립", "QQQ 10M SMA", "GTAA5", "GEM(QQQ)", "VAA-G4(QQQ)"]


def simulate(name, px, i0, i1):
    """[i0, i1] 월말 구간. 반환 (보유 평가액, 전량매도 세후, 최저 평가/납입-1, 연평균 매도횟수)."""
    sh, basis = {}, {}  # 주식 수, 총 매입원가
    paid = realized = 0.0
    worst, sells = 0.0, 0
    for i in range(i0, i1 + 1):
        p = px.iloc[i]
        paid += MONTHLY
        value = sum(sh[t] * p[t] for t in sh) + MONTHLY
        w = targets(name, px, i)
        for t in list(sh):  # 목표 밖·초과분 매도
            want = value * w.get(t, 0)
            cur = sh[t] * p[t]
            if cur > want + 1e-9:
                q = (cur - want) / p[t]
                realized += (p[t] - basis[t] / sh[t]) * q
                basis[t] *= 1 - q / sh[t]
                sh[t] -= q
                sells += 1
                value -= (cur - want) * COST
        cash = value - sum(sh[t] * p[t] for t in sh)
        for t, wt in w.items():  # 부족분 매수 (남은 현금 비율대로)
            need = value * wt - sh.get(t, 0) * p[t]
            if need > 1e-9:
                spend = min(need, cash)
                sh[t] = sh.get(t, 0) + spend * (1 - COST) / p[t]
                basis[t] = basis.get(t, 0) + spend
                cash -= spend
        if px.index[i].month == 12 or i == i1:  # 연말 양도세
            tax = max(realized - EXEMPT, 0) * TAX
            if tax > 0:
                tot = sum(sh[t] * p[t] for t in sh)
                for t in sh:  # 세금만큼 비례 축소 (그 매도의 추가 실현손익은 무시)
                    f = tax / tot
                    basis[t] *= 1 - f
                    sh[t] *= 1 - f
            realized = 0.0
        val = sum(sh[t] * p[t] for t in sh)
        worst = min(worst, val / paid - 1)
    gain = val - sum(basis.values())
    after = val - max(gain - EXEMPT, 0) * TAX
    return val, after, worst, sells / ((i1 - i0 + 1) / 12)


def run(px):
    first = 12  # 12개월 모멘텀 워밍업
    print(f"월말 데이터 {px.index[first].date()} ~ {px.index[-1].date()}, 매달 ${MONTHLY:.0f}")
    for hname, h in HORIZONS.items():
        starts = range(first, len(px) - h + 1)
        res = {s: [simulate(s, px, i, i + h - 1) for i in starts] for s in STRATS}
        base_after = np.array([r[1] for r in res["QQQ 적립"]])
        print(f"\n=== {hname} ({len(base_after)}개 구간, 매달 시작) — 기준 QQQ 적립 ===")
        print(f"{'전략':<13}{'세후 중앙값':>11}{'기준 대비':>9}{'이긴비율':>8}{'최악':>8}{'최고':>8}"
              f"{'최저평가손익':>11}{'연 매도':>7}")
        for s in STRATS:
            after = np.array([r[1] for r in res[s]])
            d = after / base_after - 1
            print(f"{s:<13}{np.median(after):>11,.0f}{np.median(d):>+9.1%}{(d > 0).mean():>8.0%}"
                  f"{d.min():>+8.1%}{d.max():>+8.1%}{np.median([r[2] for r in res[s]]):>+11.1%}"
                  f"{np.mean([r[3] for r in res[s]]):>7.1f}")
    # 시작 연도별 5년 (기준 대비)
    print("\n5년 구간, 시작 연도별 세후 기준 대비 (1월 시작)")
    for i in range(first, len(px) - 60 + 1):
        if px.index[i].month != 1:
            continue
        b = simulate("QQQ 적립", px, i, i + 59)[1]
        row = "  ".join(f"{s.split('(')[0]} {simulate(s, px, i, i + 59)[1] / b - 1:+.0%}" for s in STRATS[1:])
        print(px.index[i].year, row)


if __name__ == "__main__":
    run(load())
