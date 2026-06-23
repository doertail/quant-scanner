"""
backtest_pead.py — PEAD(어닝스 발표 후 드리프트) 검증 (방향 C, 신규 알파 후보)

가설: 어닝스 서프라이즈가 큰(긍정) 종목은 발표 후 수 주간 초과수익을 낸다(드리프트).
서프라이즈가 음수면 하방 드리프트. 학술적으로 가장 견고한 이상현상 중 하나.

데이터: yfinance earnings_dates의 실제 Surprise(%) (종목당 ~6년 분기). 룩어헤드 방지를
위해 발표 다음 거래일부터 진입·측정. 초과수익 = 종목수익 − SPY수익.

판정: 서프라이즈 상위 그룹의 forward 초과수익이 (a) 양수이고 (b) 하위 그룹보다
일관되게 높으면(단조성) → 거래 가능한 PEAD 엣지. 아니면 → 노이즈.

⚠️ yfinance 어닝스 깊이 제한(~6년), 유니버스 생존편향, 표본 작음, 비용 미반영.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import yfinance as yf

# 유동성 큰 대형주 ~40 (어닝스 API 호출 수 제한 위해)
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V",
    "UNH", "XOM", "MA", "PG", "JNJ", "HD", "COST", "MRK", "ABBV", "CRM",
    "WMT", "BAC", "KO", "PEP", "ORCL", "AMD", "ADBE", "NFLX", "DIS", "CSCO",
    "INTC", "QCOM", "TXN", "PFE", "CVX", "WFC", "GE", "CAT", "BA", "NKE",
]
HORIZONS = [5, 20, 40, 60]
START = "2019-01-01"


def fwd_excess(close, spy, i, h):
    if i + h >= len(close) or i < 0:
        return None
    r = close.iloc[i + h] / close.iloc[i] - 1.0
    rs = spy.iloc[i + h] / spy.iloc[i] - 1.0
    return (r - rs) * 100


def main():
    print(f"데이터 다운로드... ({len(UNIVERSE)}종목 + SPY, {START}~)")
    raw = yf.download(UNIVERSE + ["SPY"], start=START, group_by="ticker",
                      threads=True, progress=False)
    spy = raw["SPY"]["Close"].dropna()

    records = []  # {surprise, h5, h20, h40, h60}
    for t in UNIVERSE:
        try:
            close = raw[t]["Close"].dropna()
            if len(close) < 100:
                continue
            ed = yf.Ticker(t).earnings_dates
        except Exception:
            continue
        if ed is None or ed.empty or "Surprise(%)" not in ed.columns:
            continue
        spy_t = spy.reindex(close.index, method="ffill")
        for ts, row in ed.iterrows():
            sup = row.get("Surprise(%)")
            if pd.isna(sup):
                continue
            d = ts.date()
            # 발표 다음 거래일(룩어헤드 방지): date > d 인 첫 인덱스
            pos = close.index.searchsorted(pd.Timestamp(d) + pd.Timedelta(days=1))
            if pos >= len(close):
                continue
            rec = {"surprise": float(sup)}
            ok = False
            for h in HORIZONS:
                v = fwd_excess(close, spy_t, pos, h)
                rec[f"h{h}"] = v
                ok = ok or (v is not None)
            if ok:
                records.append(rec)

    df = pd.DataFrame(records)
    print(f"수집 이벤트: {len(df)}개\n")
    if len(df) < 30:
        print("표본 부족 — 판정 불가"); return

    # 서프라이즈 3분위
    df["tercile"] = pd.qcut(df["surprise"].rank(method="first"), 3,
                            labels=["하위(저서프라이즈)", "중위", "상위(고서프라이즈)"])

    W = 84
    print("=" * W)
    print("  PEAD — 어닝스 서프라이즈 3분위별 발표후 초과수익 (vs SPY, %)".center(W - 12))
    print("=" * W)
    hdr = f"{'서프라이즈 그룹':<22}{'n':>6}" + "".join(f"{f'+{h}d':>11}" for h in HORIZONS)
    print(hdr); print("-" * W)
    means = {}
    for grp in ["하위(저서프라이즈)", "중위", "상위(고서프라이즈)"]:
        sub = df[df["tercile"] == grp]
        line = f"{grp:<22}{len(sub):>6}"
        means[grp] = {}
        for h in HORIZONS:
            m = sub[f"h{h}"].dropna().mean()
            means[grp][h] = m
            line += f"{m:>10.2f}%"
        print(line)
    print("-" * W)
    # 롱숏 스프레드 (상위 − 하위)
    spread = f"{'롱숏 (상위−하위)':<22}{'':>6}"
    for h in HORIZONS:
        spread += f"{means['상위(고서프라이즈)'][h] - means['하위(저서프라이즈)'][h]:>10.2f}%"
    print(spread)
    print("=" * W)

    # 판정: 20일 기준 단조성 + 상위 양수 + 롱숏 양수
    h = 20
    mono = means["상위(고서프라이즈)"][h] > means["중위"][h] > means["하위(저서프라이즈)"][h]
    top_pos = means["상위(고서프라이즈)"][h] > 0
    ls_pos = (means["상위(고서프라이즈)"][h] - means["하위(저서프라이즈)"][h]) > 0
    if mono and top_pos and ls_pos:
        verdict = "✅ PEAD 신호 있음 — 단조성+상위 양수+롱숏 양수 (추가 검증 가치)"
    elif ls_pos and top_pos:
        verdict = "🔶 약한 신호 — 롱숏은 양수지만 단조성 불완전"
    else:
        verdict = "❌ PEAD 신호 약함/없음 — 노이즈"
    print(f"\n판정(20일 기준): {verdict}")
    print("* yfinance 어닝스 깊이 제한·표본 작음·비용 미반영. 신호 있어도 forward 재검증 필수.")


if __name__ == "__main__":
    main()
