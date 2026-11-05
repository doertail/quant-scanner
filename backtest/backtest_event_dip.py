"""
backtest_event_dip.py — 대형주가 '단기 이슈'로 하루 급락했을 때 사면 QQQ보다 나은가?

사용자 방식 그대로: 대형주가 하루에 크게 빠지면(시장은 멀쩡한데 그 종목만) 다음 날 종가에
사서 N거래일 들고 간다. 같은 날 같은 돈으로 QQQ를 산 것과 비교한다 (초과수익).

이벤트: 종목 일간수익 ≤ DROP 이고 (종목 − QQQ) ≤ REL — 시장 전체 폭락이 아니라 그 회사 이슈.
같은 종목은 GAP일 안에 한 번만 센다 (연속 급락을 여러 번 세지 않게).

⚠️ 생존편향: 'TODAY' = 지금 거인이 된 회사들. 과거엔 몰랐다. 그래서 2010년쯤 거인이었지만
이후 부진한 회사('FALLEN')를 따로 돌려 비교한다 — 실제 기대치는 두 그룹 사이 어딘가.

사용: python backtest/backtest_event_dip.py
"""

import numpy as np
import pandas as pd
import yfinance as yf

TODAY = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL", "CRM", "ADBE",
         "NFLX", "AMD", "JPM", "WMT", "COST", "LLY", "V", "MA"]
FALLEN = ["INTC", "IBM", "CSCO", "PYPL", "BA", "DIS", "NKE", "GE", "T", "VZ", "XOM", "PFE",
          "MMM", "WBA", "C", "WFC", "QCOM", "KO", "BABA", "MRK"]
DROP, REL, GAP = -0.05, -0.04, 20
HORIZONS = (20, 60, 120, 250)
START = "2010-01-01"


def find_events(ret: pd.Series, qret: pd.Series, drop=DROP, rel=REL, gap=GAP) -> list:
    """이벤트 위치 목록 (순수). ret/qret은 같은 인덱스의 일간수익."""
    hit = (ret <= drop) & ((ret - qret) <= rel)
    out, last = [], -10**9
    for i in np.flatnonzero(hit.to_numpy()):
        if i - last >= gap:
            out.append(i)
            last = i
    return out


def forward_excess(px: pd.Series, qpx: pd.Series, i: int, h: int) -> float | None:
    """이벤트 다음 날 종가에 사서 h거래일 보유 — 종목 수익 − QQQ 수익 (순수)."""
    b, e = i + 1, i + 1 + h
    if e >= len(px) or np.isnan(px.iloc[b]) or np.isnan(px.iloc[e]):
        return None
    return (px.iloc[e] / px.iloc[b]) - (qpx.iloc[e] / qpx.iloc[b])


def run(group: list, close: pd.DataFrame) -> pd.DataFrame:
    q = close["QQQ"]
    qret = q.pct_change()
    rows = []
    for t in group:
        px = close[t]
        for i in find_events(px.pct_change(), qret):
            r = {"ticker": t, "date": close.index[i].date(), "drop": px.pct_change().iloc[i]}
            for h in HORIZONS:
                r[h] = forward_excess(px, q, i, h)
            rows.append(r)
    return pd.DataFrame(rows)


def baseline(group: list, close: pd.DataFrame, h: int) -> float:
    """대조군: 같은 종목을 아무 날(20일 간격)에나 샀을 때 h일 초과수익 중앙값."""
    q, xs = close["QQQ"], []
    for t in group:
        px = close[t]
        xs += [x for i in range(0, len(px), GAP) if (x := forward_excess(px, q, i, h)) is not None]
    return float(np.median(xs))


def summary(df: pd.DataFrame) -> str:
    L = [f"  이벤트 {len(df)}건 ({df['ticker'].nunique()}종목)"]
    for h in HORIZONS:
        x = df[h].dropna()
        L.append(f"  {h:>3}일 후 QQQ 대비: 중앙값 {x.median():+.1%} · 평균 {x.mean():+.1%} · "
                 f"이긴 비율 {(x > 0).mean():.0%} · 최악 {x.min():+.0%} (n={len(x)})")
    return "\n".join(L)


def _check():
    idx = pd.RangeIndex(8)
    ret = pd.Series([0, -0.06, -0.07, 0, 0, -0.06, 0, 0.0], index=idx)
    q = pd.Series([0, 0, -0.06, 0, 0, 0, 0, 0.0], index=idx)
    assert find_events(ret, q, gap=3) == [1, 5]          # 2는 시장 탓 + gap 안
    px = pd.Series([100, 90, 91, 95, 99, 100, 100, 100.0])
    qpx = pd.Series([100.0] * 8)
    assert abs(forward_excess(px, qpx, 1, 2) - (99 / 91 - 1)) < 1e-12
    assert forward_excess(px, qpx, 5, 5) is None


def main():
    _check()
    tickers = sorted(set(TODAY + FALLEN + ["QQQ"]))
    raw = yf.download(tickers, start=START, auto_adjust=True, progress=False)["Close"]
    print(f"기간 {raw.index[0].date()} ~ {raw.index[-1].date()} · 조건: 하루 ≤{DROP:.0%} & QQQ보다 ≤{REL:.0%}"
          f" · 다음 날 종가 매수\n")
    for name, g in (("지금 거인 (생존편향 有)", TODAY), ("한때 거인, 이후 부진", FALLEN)):
        print(f"■ {name}")
        print(summary(run(g, raw)))
        print("  대조군(아무 날 매수): " + " · ".join(f"{h}일 {baseline(g, raw, h):+.1%}" for h in HORIZONS))
        print()
    allg = run(TODAY + FALLEN, raw)
    print("■ 둘 합침")
    print(summary(allg))
    big = allg[allg["drop"] <= -0.10]
    print("\n■ 하루 −10% 이상 급락만")
    print(summary(big))


if __name__ == "__main__":
    main()
