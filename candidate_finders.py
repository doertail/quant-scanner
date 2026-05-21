"""
candidate_finders.py — Phase 1 진입 후보 탐색

전략별 진입 신호 후보를 스캔:
  - find_candidates           : 전략 A (평균회귀) — RSI/MA 룰 기반 일반화 헬퍼
  - find_momentum_candidates  : 전략 B (NDX 모멘텀) — 6M 랭킹 + 3M QQQ 상대강도
  - find_crypto_candidates    : 전략 D (크립토 모멘텀) — ETH 레짐 + 6M 랭킹 + 3M ETH 상대강도
"""

import numpy as np
import pandas as pd

from config import (
    B_ATR_MULT,
    B_MOM_LONG,
    B_MOM_SHORT,
    B_RANK_TOP,
    D_ATR_MULT,
    D_MOM_LONG,
    D_MOM_SHORT,
    D_RANK_TOP,
    VIX_PANIC,
)
from indicators import vol_ratio_of


def find_candidates(
    stock_universe: dict[str, pd.DataFrame],
    current_holdings: dict,
    rules: dict,
    sort_key: str,
    sort_reverse: bool,
) -> list[dict]:
    """주어진 규칙에 따라 신규 진입 후보를 스캔 (전략 A 일반화)."""
    candidates = []
    for ticker, df in stock_universe.items():
        if ticker in current_holdings:
            continue

        row = df.iloc[-1]
        close = float(row['Close'])
        rsi = float(row['RSI'])
        ma20 = float(row['MA20'])
        ma200 = float(row['MA200'])

        rsi_cond = (rsi > rules['rsi_val']) if rules['rsi_cond'] == 'gt' else (rsi < rules['rsi_val'])
        ma_cond = (close > ma20) if rules['ma_cond'] == 'gt' else (close < ma20)

        if close > ma200 and rsi_cond and ma_cond:
            candidates.append(dict(
                ticker=ticker,
                close=close,
                rsi=round(rsi, 1),
                ma20=round(ma20, 2),
                ma50=round(float(row['MA50']), 2),
                ma200=round(ma200, 2),
                stop=round(close - float(row['ATR']) * rules['atr_mult'], 2),
                vol_ratio=vol_ratio_of(row),
            ))

    candidates.sort(key=lambda x: x[sort_key], reverse=sort_reverse)
    return candidates


def find_momentum_candidates(
    stock_universe: dict[str, pd.DataFrame],
    current_holdings: dict,
    qqq_close: pd.Series,
) -> list[dict]:
    """
    전략 B: 6개월 수익률 상위 25% + 3개월 QQQ 아웃퍼폼 + Close > MA20/MA200.
    백테스트: RSI>65 방식 대비 연환산 CAGR +20.7% → +33.7% 개선.
    """
    ret_6m: dict[str, float] = {}
    ret_3m: dict[str, float] = {}
    for ticker, df in stock_universe.items():
        if len(df) > B_MOM_LONG:
            ret_6m[ticker] = float(df['Close'].iloc[-1] / df['Close'].iloc[-(B_MOM_LONG + 1)] - 1)
        if len(df) > B_MOM_SHORT:
            ret_3m[ticker] = float(df['Close'].iloc[-1] / df['Close'].iloc[-(B_MOM_SHORT + 1)] - 1)

    if not ret_6m:
        return []

    sorted_tickers = sorted(ret_6m.keys(), key=lambda t: ret_6m[t])
    n = len(sorted_tickers)
    rank_map = {t: i / (n - 1) for i, t in enumerate(sorted_tickers)} if n > 1 else {sorted_tickers[0]: 0.5}

    qqq_ret_3m = (
        float(qqq_close.iloc[-1] / qqq_close.iloc[-(B_MOM_SHORT + 1)] - 1)
        if len(qqq_close) > B_MOM_SHORT else 0.0
    )

    candidates = []
    for ticker, df in stock_universe.items():
        if ticker in current_holdings:
            continue
        rank = rank_map.get(ticker)
        if rank is None or rank < (1.0 - B_RANK_TOP):
            continue
        rs = ret_3m.get(ticker, float('nan')) - qqq_ret_3m
        if np.isnan(rs) or rs <= 0:
            continue
        row   = df.iloc[-1]
        close = float(row['Close'])
        ma20  = float(row['MA20'])
        ma200 = float(row['MA200'])
        if not (close > ma200 and close > ma20):
            continue
        candidates.append(dict(
            ticker    = ticker,
            close     = close,
            rsi       = round(float(row['RSI']), 1),
            ma20      = round(ma20, 2),
            ma50      = round(float(row['MA50']), 2),
            ma200     = round(ma200, 2),
            stop      = round(close - float(row['ATR']) * B_ATR_MULT, 2),
            vol_ratio = vol_ratio_of(row),
            rank_pct  = round(rank * 100, 1),
            rs_vs_qqq = round(rs * 100, 2),
        ))

    candidates.sort(key=lambda x: x['rank_pct'], reverse=True)
    return candidates


def find_crypto_candidates(
    eth_close: pd.Series,
    vix_price: float,
    crypto_universe: dict[str, pd.DataFrame],
    current_holdings: dict,
) -> list[dict]:
    """
    전략 D: ETH>MA50 레짐 + 6M 수익률 상위 50% + ETH 3M 아웃퍼폼 + Close>MA20 + VIX≤30.
    백테스트: CAGR +17.62%, Sharpe 0.813 (2017–2024).
    """
    if len(eth_close) < 50:
        return []
    eth_ma50_val = float(eth_close.rolling(50).mean().iloc[-1])
    eth_cur_val  = float(eth_close.iloc[-1])
    if eth_cur_val <= eth_ma50_val:
        return []
    if not pd.isna(vix_price) and vix_price > VIX_PANIC:
        return []

    ret_6m: dict[str, float] = {}
    ret_3m: dict[str, float] = {}
    for ticker, df in crypto_universe.items():
        if len(df) > D_MOM_LONG:
            ret_6m[ticker] = float(df['Close'].iloc[-1] / df['Close'].iloc[-(D_MOM_LONG + 1)] - 1)
        if len(df) > D_MOM_SHORT:
            ret_3m[ticker] = float(df['Close'].iloc[-1] / df['Close'].iloc[-(D_MOM_SHORT + 1)] - 1)

    if not ret_6m:
        return []

    eth_ret_3m = (
        float(eth_close.iloc[-1] / eth_close.iloc[-(D_MOM_SHORT + 1)] - 1)
        if len(eth_close) > D_MOM_SHORT else 0.0
    )

    sorted_tickers = sorted(ret_6m.keys(), key=lambda t: ret_6m[t])
    n = len(sorted_tickers)
    rank_map = {t: i / (n - 1) for i, t in enumerate(sorted_tickers)} if n > 1 else {sorted_tickers[0]: 0.5}

    candidates = []
    for ticker, df in crypto_universe.items():
        if ticker in current_holdings:
            continue
        rank = rank_map.get(ticker)
        if rank is None or rank < (1.0 - D_RANK_TOP):
            continue
        rs = ret_3m.get(ticker, float('nan')) - eth_ret_3m
        if np.isnan(rs) or rs <= 0:
            continue
        row   = df.iloc[-1]
        close = float(row['Close'])
        ma20  = float(row['MA20'])
        if close <= ma20:
            continue
        candidates.append(dict(
            ticker    = ticker,
            close     = close,
            rsi       = round(float(row['RSI']), 1),
            ma20      = round(ma20, 2),
            ma50      = round(float(row['MA50']), 2),
            ma200     = round(float(row['MA200']), 2),
            stop      = round(close - float(row['ATR']) * D_ATR_MULT, 2),
            vol_ratio = vol_ratio_of(row),
            rank_pct  = round(rank * 100, 1),
            rs_vs_eth = round(rs * 100, 2),
        ))

    candidates.sort(key=lambda x: x['rank_pct'], reverse=True)
    return candidates
