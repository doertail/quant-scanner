"""
백테스트 공용 통계.

이 파일이 생긴 이유: calc_stats가 5개 스크립트에 글자 단위로 똑같이 복붙돼
있었고, 그 안의 무위험수익률이 2005~2026 전 구간 3.5% 고정이라 Sharpe가
틀렸다 (ZIRP 시기 실제 0.1%, 2023~24 실제 5.3%). 고칠 곳이 5군데면 4군데는
안 고쳐진다. 단일 출처를 여기 둔다.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RF_FALLBACK = 0.035          # ^IRX를 못 받았을 때만 쓰는 옛 상수
_IRX_CACHE = Path(__file__).with_name('.irx_cache.csv')


# ─── 무위험수익률 ────────────────────────────────────────────────────────────

def _irx_series() -> pd.Series | None:
    """^IRX(13주 T-bill, 연 %) 일별 시계열. 캐시 우선, 실패하면 None."""
    if _IRX_CACHE.exists():
        try:
            cached = pd.read_csv(_IRX_CACHE, index_col=0, parse_dates=True).iloc[:, 0].dropna()
            fresh = cached.index.max() >= pd.Timestamp.today() - pd.Timedelta(days=30)
            if len(cached) > 100 and fresh:
                return cached
        except Exception:
            pass

    try:
        import yfinance as yf
        df = yf.download('^IRX', start='1990-01-01', progress=False, auto_adjust=False)
        s = df['Close']
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s = s.dropna()
        if s.empty:
            return None
        s.to_csv(_IRX_CACHE)
        return s
    except Exception:
        return None


def risk_free_daily(dates) -> pd.Series:
    """
    dates에 정렬된 일별 무위험수익률(소수). ^IRX가 없으면 RF_FALLBACK 평탄.

    # ponytail: ^IRX는 할인율이라 채권등가수익률보다 2~5bp 낮다. Sharpe 소수점
    # 셋째 자리 수준의 차이라 무시했다. 정밀도가 필요해지면 DGS3MO로 교체.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    s = _irx_series()
    if s is None:
        return pd.Series(RF_FALLBACK / TRADING_DAYS, index=idx)

    s = s[~s.index.duplicated()].sort_index()
    aligned = s.reindex(s.index.union(idx)).ffill().reindex(idx).bfill()
    aligned = aligned.fillna(RF_FALLBACK * 100)
    return (aligned / 100.0) / TRADING_DAYS


# ─── 거래비용 ────────────────────────────────────────────────────────────────

def total_cost(vix, base: float = 0.001, panic_mult: float = 3.0) -> float:
    """
    편도 거래비용(수수료 + 슬리피지). VIX≤20 base, VIX≥30 base*panic_mult, 사이 선형.

    평탄 5bp 슬리피지는 VIX>30인 날에 낙관적이다 — 전략 C/D는 정의상 그런
    날에만 산다.

    # ponytail: 배수 3.0은 실측 스프레드가 아니라 가정이다. 체결 기록이 쌓이면
    # 실제 체결가 - 종가로 교체할 것.
    """
    if vix is None or not np.isfinite(vix):
        return base
    if vix <= 20.0:
        return base
    if vix >= 30.0:
        return base * panic_mult
    return base * (1.0 + (panic_mult - 1.0) * (vix - 20.0) / 10.0)


# ─── 통계 ────────────────────────────────────────────────────────────────────

def max_drawdown(equity) -> tuple[float, int]:
    """(MDD %, 최장 언더워터 거래일수). 끝까지 회복 못 한 구간도 센다."""
    pv = pd.Series(equity, dtype=float)
    dd = (pv - pv.cummax()) / pv.cummax()
    best = cur = 0
    for under in (dd < 0).to_numpy():
        cur = cur + 1 if under else 0
        best = max(best, cur)
    return float(dd.min() * 100), best


_KEYS = ['final', 'ret', 'cagr', 'mdd', 'mdd_days',
         'sharpe', 'sortino', 'calmar', 'n', 'wins', 'wr']


def calc_stats(equity, initial: float, trades, dates=None) -> dict:
    """
    dates를 주면 ^IRX 실제 무위험수익률로 Sharpe/Sortino를 계산한다.
    안 주면 RF_FALLBACK 평탄값 — 옛 동작 그대로.

    Sortino의 MAR은 무위험수익률이다(초과수익 < 0 기준). 옛 코드는 원수익률
    < 0 기준이었어서 값이 조금 달라진다.

    mdd_days는 거래일 기준 (252 ≈ 1년).
    """
    if len(equity) < 2:
        return {k: 0 for k in _KEYS}

    final = float(equity[-1])
    ret = (final - initial) / initial * 100
    years = len(equity) / TRADING_DAYS
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    rets = pd.Series(equity, dtype=float).pct_change().dropna()
    if dates is not None and len(dates) == len(equity):
        rf = risk_free_daily(dates).to_numpy()[1:]      # pct_change가 첫 봉을 지운다
    else:
        # 조용히 틀린 Sharpe가 실측인 척하는 게 원래 버그였다. 시끄럽게 만든다.
        warnings.warn(
            f'calc_stats: dates 없음 → 무위험수익률 {RF_FALLBACK:.1%} 평탄값 사용. '
            'Sharpe/Sortino는 실측이 아니다.',
            stacklevel=2,
        )
        rf = RF_FALLBACK / TRADING_DAYS
    excess = pd.Series(rets.to_numpy() - rf)

    ann = np.sqrt(TRADING_DAYS)
    sd = excess.std()
    dn = excess[excess < 0].std()
    sharpe = float(excess.mean() / sd * ann) if sd > 0 else 0.0
    sortino = float(excess.mean() / dn * ann) if dn > 0 else 0.0

    mdd, mdd_days = max_drawdown(equity)
    calmar = cagr / abs(mdd) if mdd != 0 else 0.0
    wins = sum(1 for t in trades if t[1] > t[0])
    wr = wins / len(trades) * 100 if trades else 0.0

    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd, mdd_days=mdd_days,
                sharpe=sharpe, sortino=sortino, calmar=calmar,
                n=len(trades), wins=wins, wr=wr)


if __name__ == '__main__':
    # 거래비용
    assert total_cost(15) == 0.001
    assert total_cost(20) == 0.001
    assert total_cost(35) == 0.003
    assert abs(total_cost(25) - 0.002) < 1e-12          # 중간값 선형
    assert total_cost(float('nan')) == 0.001            # VIX 결측 → base

    # MDD: 100 → 50 → 80 → 100 → 120. 고점 미달인 50·80 두 봉이 언더워터,
    # 고점과 같아진 100은 아니다.
    mdd, days = max_drawdown([100, 50, 80, 100, 120])
    assert abs(mdd - (-50.0)) < 1e-9, mdd
    assert days == 2, days
    # 끝까지 회복 못 하면 마지막 구간도 센다
    assert max_drawdown([100, 90, 80, 70])[1] == 3

    # 무위험수익률을 못 받아도 죽지 않고 평탄값으로 떨어진다
    flat = calc_stats([100, 101, 102], 100, [])
    assert flat['mdd'] == 0 and flat['n'] == 0

    # 상수 RFR보다 실제 RFR이 붙으면 Sharpe가 달라져야 한다 (ZIRP 구간)
    zirp = pd.bdate_range('2012-01-02', periods=250)
    eq = list(100 * (1 + pd.Series(np.linspace(0.0002, 0.0003, 250))).cumprod())
    with_dates = calc_stats(eq, 100, [], dates=zirp)['sharpe']
    without = calc_stats(eq, 100, [])['sharpe']
    assert with_dates > without, (with_dates, without)   # 2012년 RFR ≈ 0 < 3.5%

    print('btlib self-check ok')
