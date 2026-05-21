"""
backtest_b_sideways_crypto.py
─────────────────────────────────────────────────────────────────────────────
검증 항목 3가지:

  [1] 전략 B 횡보장 허용 효과
      B_current  : SIDEWAYS → 완전 차단 (현행)
      B_sideways : SIDEWAYS + VIX ≤ 25 → 허용, rs_vs_qqq > 10% 강화 조건

  [2] 전략 D — 코인 연동 개별주 평균회귀
      대상: COIN, MSTR, BLOK
      진입: RSI < 30 + Close > MA200 + Close < MA20 + VIX ≤ 30
      청산: ATR × 3.0 트레일링 스톱 / TP1(RSI ≥ 55 → 50%) / TP2(MA20 도달)

  [3] 전략 E — ETH 직접 (ETH-USD) + ETH ETF (ETHA)
      진입: RSI < 30 + VIX ≤ 30  (MA200 조건 완화 — 역사 짧음)
      청산: ATR × 3.0 트레일링 스톱 / TP1(RSI ≥ 55 → 50%)

  공통 비교: QQQM Buy & Hold (기준선)
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime
from io import StringIO

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
START        = '2010-01-01'
END          = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH = 100_000.0
COMMISSION   = 0.0005
SLIPPAGE     = 0.0005
TOTAL_COST   = COMMISSION + SLIPPAGE
RISK_FREE_RATE = 0.035

RISK_PER_TRADE    = 0.01
MAX_CAP_PER_STOCK = 0.20   # 코인주는 단일종목 비중 높임
B_MAX_POS         = 10

# 전략 B
B_MOM_LONG  = 126
B_MOM_SHORT = 63
B_RANK_TOP  = 0.25
B_ATR_MULT  = 3.0

# B 횡보장 허용 파라미터
B_SIDEWAYS_VIX_MAX = 25.0    # SIDEWAYS일 때 허용 최대 VIX
B_SIDEWAYS_RS_MIN  = 0.10    # SIDEWAYS 허용 시 rs_vs_qqq 강화 (10%)

# 전략 D — 코인 연동 개별주
D_TICKERS    = ['COIN', 'MSTR', 'BLOK']
D_RSI_BUY    = 30
D_RSI_TP1    = 55
D_ATR_MULT   = 3.0
D_VIX_MAX    = 30.0
D_RISK_PCT   = 0.01
D_MAX_POS    = 3

# 전략 E — ETH (직접 + ETF)
E_TICKERS    = ['ETH-USD', 'ETHA']
E_RSI_BUY    = 30
E_RSI_TP1    = 55
E_ATR_MULT   = 3.0
E_VIX_MAX    = 30.0
E_MAX_POS    = 2

# 시장 국면
RSI_PERIOD             = 14
ATR_PERIOD             = 14
ADX_PERIOD             = 14
QQQ_MA_PERIOD          = 200
ADX_TREND_THRESHOLD    = 25
ADX_SIDEWAYS_THRESHOLD = 20
BREADTH_BULL           = 60.0
BREADTH_BEAR           = 40.0
VIX_RV_HIGH            = 1.2
VIX_RV_LOW             = 0.8
VIX_SWEET_LOW          = 20.0
VIX_DANGER_LOW         = 25.0
VIX_PANIC              = 30.0
HYG_MA_PERIOD          = 50


# ─── 티커 수집 ────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(
        'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        headers=headers, timeout=15,
    )
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    return []


def get_nasdaq100_tickers() -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(
        'https://en.wikipedia.org/wiki/Nasdaq-100',
        headers=headers, timeout=15,
    )
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    return []


# ─── 지표 계산 ───────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    delta     = df['Close'].diff()
    up        = delta.clip(lower=0)
    down      = -delta.clip(upper=0)
    df['RSI'] = 100 - (100 / (
        1 + up.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
          / down.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    ))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    prev = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low']  - prev).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()
    return df


def compute_adx_series(df: pd.DataFrame) -> pd.DataFrame:
    high, low, close = df['High'], df['Low'], df['Close']
    prev  = close.shift(1)
    tr    = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    up_m  = high - high.shift(1)
    dn_m  = low.shift(1) - low
    p_dm  = up_m.where((up_m > dn_m) & (up_m > 0), 0.0)
    m_dm  = dn_m.where((dn_m > up_m) & (dn_m > 0), 0.0)
    atr14 = tr.ewm(com=ADX_PERIOD - 1, adjust=False).mean()
    p_di  = 100 * p_dm.ewm(com=ADX_PERIOD - 1, adjust=False).mean() / atr14
    m_di  = 100 * m_dm.ewm(com=ADX_PERIOD - 1, adjust=False).mean() / atr14
    dx    = 100 * (p_di - m_di).abs() / (p_di + m_di).replace(0, np.nan)
    adx   = dx.ewm(com=ADX_PERIOD - 1, adjust=False).mean()
    return pd.DataFrame({'ADX': adx, 'DI_plus': p_di, 'DI_minus': m_di})


def build_stock_data(raw: pd.DataFrame, tickers: list[str], min_bars: int = 210) -> dict[str, pd.DataFrame]:
    result = {}
    for t in tickers:
        try:
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].copy().dropna(subset=['Close'])
            if len(df) < min_bars:
                continue
            df = compute_indicators(df)
            if df[['RSI', 'MA20', 'MA50', 'ATR']].iloc[-1].isna().any():
                continue
            result[t] = df
        except Exception:
            pass
    return result


def build_single_data(ticker: str, start: str, end: str, min_bars: int = 60) -> pd.DataFrame | None:
    """단일 티커 개별 다운로드 (코인/ETF 등 배치 누락 방지)"""
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, multi_level_index=False)
        if df is None or len(df) < min_bars:
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
        return compute_indicators(df)
    except Exception:
        return None


def precompute_breadth(sp500_data: dict[str, pd.DataFrame]) -> pd.Series:
    close_df = pd.DataFrame({t: df['Close']  for t, df in sp500_data.items()})
    ma200_df = pd.DataFrame({t: df['MA200']  for t, df in sp500_data.items()})
    above    = (close_df > ma200_df).sum(axis=1)
    total    = close_df.notna().sum(axis=1)
    return (above / total.replace(0, np.nan) * 100).rename('breadth')


def precompute_momentum_ranks(
    stock_data_b: dict[str, pd.DataFrame],
    qqq_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    close_df   = pd.DataFrame({t: df['Close'] for t, df in stock_data_b.items()})
    ret_6m     = close_df.pct_change(B_MOM_LONG)
    rank_df    = ret_6m.rank(axis=1, pct=True)
    ret_3m     = close_df.pct_change(B_MOM_SHORT)
    qqq_ret_3m = qqq_df['Close'].pct_change(B_MOM_SHORT)
    rs_df      = ret_3m.subtract(qqq_ret_3m, axis=0)
    return rank_df, rs_df


# ─── 국면 시리즈 ──────────────────────────────────────────────────────────────

def build_regime_series(
    qqq_ohlc: pd.DataFrame,
    breadth_series: pd.Series,
    vix_close: pd.Series,
    hyg_close: pd.Series,
    all_dates,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(all_dates)

    def fill(s: pd.Series) -> pd.Series:
        return s.reindex(dates, method='ffill')

    qqq_cl    = qqq_ohlc['Close'].dropna()
    qqq_ma200 = qqq_cl.rolling(QQQ_MA_PERIOD).mean()
    adx_data  = compute_adx_series(qqq_ohlc.dropna())
    log_ret   = np.log(qqq_cl / qqq_cl.shift(1))
    rv_30     = log_ret.rolling(30).std() * np.sqrt(252) * 100

    cl_s    = fill(qqq_cl)
    ma200_s = fill(qqq_ma200)
    adx_s   = fill(adx_data['ADX'])
    pdi_s   = fill(adx_data['DI_plus'])
    mdi_s   = fill(adx_data['DI_minus'])
    rv_s    = fill(rv_30)
    vix_s   = fill(vix_close)
    ratio_s = vix_s / rv_s.replace(0, np.nan)
    hyg_s   = fill(hyg_close)
    hyg_ma  = fill(hyg_close.rolling(HYG_MA_PERIOD).mean())
    brd_s   = fill(breadth_series)
    bull_qqq = cl_s > ma200_s

    l1 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    l1 = l1.mask(adx_s < ADX_SIDEWAYS_THRESHOLD, 'SIDEWAYS')
    l1 = l1.mask(adx_s >= ADX_TREND_THRESHOLD,
                 np.where(pdi_s > mdi_s, 'BULL', 'BEAR'))

    l2 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    l2 = l2.mask(brd_s > BREADTH_BULL, 'BULL')
    l2 = l2.mask(brd_s < BREADTH_BEAR, 'BEAR')
    l2 = l2.mask((brd_s >= BREADTH_BEAR) & (brd_s <= BREADTH_BULL) & brd_s.notna(), 'SIDEWAYS')

    l3 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    l3 = l3.mask(
        (ratio_s >= VIX_RV_LOW) & (ratio_s <= VIX_RV_HIGH) & ratio_s.notna(),
        'SIDEWAYS',
    )

    vote_df = pd.DataFrame({'l1': l1, 'l2': l2, 'l3': l3})

    def _vote(row):
        v = list(row)
        if v.count('SIDEWAYS') >= 2: return 'SIDEWAYS'
        if v.count('BULL') >= 2:     return 'BULL'
        return 'BEAR'

    regime_s = vote_df.apply(_vote, axis=1)

    vix_zone = pd.Series('NORMAL', index=dates, dtype=object)
    vix_zone = vix_zone.mask(vix_s > VIX_SWEET_LOW,  'SWEET')
    vix_zone = vix_zone.mask(vix_s > VIX_DANGER_LOW, 'DANGER')
    vix_zone = vix_zone.mask(vix_s > VIX_PANIC,      'PANIC')

    danger      = vix_zone == 'DANGER'
    panic       = vix_zone == 'PANIC'
    hyg_ok      = hyg_s > hyg_ma
    sweet_block = (vix_zone == 'SWEET') & ~hyg_ok

    allow_a = ~danger & ~sweet_block
    # B 현행: BULL만 허용
    allow_b_current  = ~danger & ~panic & ~sweet_block & (regime_s == 'BULL')
    # B 신규: SIDEWAYS + VIX ≤ 25 추가 허용
    allow_b_sideways = (
        allow_b_current
        | (~danger & ~panic & ~sweet_block
           & (regime_s == 'SIDEWAYS')
           & (vix_s <= B_SIDEWAYS_VIX_MAX))
    )

    return pd.DataFrame({
        'regime':           regime_s,
        'vix_zone':         vix_zone,
        'vix':              vix_s,
        'allow_a':          allow_a,
        'allow_b_current':  allow_b_current,
        'allow_b_sideways': allow_b_sideways,
    })


# ─── 통계 ────────────────────────────────────────────────────────────────────

def calc_stats(equity: list, initial: float, trades: list) -> dict:
    if len(equity) < 2:
        return {k: 0 for k in
                ['final', 'ret', 'cagr', 'mdd', 'sharpe', 'sortino', 'calmar', 'n', 'wins', 'wr']}
    final  = equity[-1]
    ret    = (final - initial) / initial * 100
    years  = len(equity) / 252.0
    cagr   = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0
    rets   = pd.Series(equity).pct_change().dropna()
    avg_r  = rets.mean() * 252
    std_r  = rets.std()  * np.sqrt(252)
    sharpe = (avg_r - RISK_FREE_RATE) / std_r if std_r > 0 else 0
    dn_r   = rets[rets < 0].std() * np.sqrt(252)
    sortino = (avg_r - RISK_FREE_RATE) / dn_r if dn_r > 0 else 0
    pv     = pd.Series(equity)
    mdd    = ((pv - pv.cummax()) / pv.cummax()).min() * 100
    calmar = cagr / abs(mdd) if mdd != 0 else 0
    wins   = sum(1 for t in trades if t[1] > t[0])
    wr     = wins / len(trades) * 100 if trades else 0.0
    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd,
                sharpe=sharpe, sortino=sortino, calmar=calmar,
                n=len(trades), wins=wins, wr=wr)


def trade_stats(trades: list) -> dict:
    """trades: [(entry_price, exit_price, entry_date, exit_date), ...]"""
    if not trades:
        return {'n': 0, 'wr': 0.0, 'avg_pnl_pct': 0.0,
                'avg_win_pct': 0.0, 'avg_loss_pct': 0.0, 'avg_hold_days': 0}
    wins      = [t for t in trades if t[1] > t[0]]
    pnl_pcts  = [(t[1] - t[0]) / t[0] * 100 for t in trades]
    win_pcts  = [(t[1] - t[0]) / t[0] * 100 for t in trades if t[1] > t[0]]
    loss_pcts = [(t[1] - t[0]) / t[0] * 100 for t in trades if t[1] <= t[0]]
    hold_days = [(t[3] - t[2]).days for t in trades]
    return {
        'n':             len(trades),
        'wr':            len(wins) / len(trades) * 100,
        'avg_pnl_pct':   sum(pnl_pcts)  / len(pnl_pcts)  if pnl_pcts  else 0.0,
        'avg_win_pct':   sum(win_pcts)  / len(win_pcts)  if win_pcts  else 0.0,
        'avg_loss_pct':  sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0,
        'avg_hold_days': int(sum(hold_days) / len(hold_days)) if hold_days else 0,
    }


def _get_price(data: dict, ticker: str, date) -> float | None:
    df = data.get(ticker)
    if df is None:
        return None
    try:
        return float(df.loc[:date].iloc[-1]['Close'])
    except Exception:
        return None


# ─── [1] 전략 B 횡보장 허용 비교 ─────────────────────────────────────────────

def run_b_comparison(
    stock_b: dict[str, pd.DataFrame],
    all_dates: list,
    regime_df: pd.DataFrame,
    mom_rank_df: pd.DataFrame,
    mom_rs_df: pd.DataFrame,
    use_sideways: bool,
    rs_min: float,
) -> dict:
    """
    use_sideways=False : 현행 (BULL만)
    use_sideways=True  : 신규 (SIDEWAYS + VIX ≤ 25 + rs > rs_min)
    """
    cash       = INITIAL_CASH
    positions: dict = {}
    eq_hist    = []
    trades     = []
    allow_col  = 'allow_b_sideways' if use_sideways else 'allow_b_current'

    for date in all_dates:
        if date not in regime_df.index:
            eq_hist.append(eq_hist[-1] if eq_hist else INITIAL_CASH)
            continue

        reg     = regime_df.loc[date]
        allow_b = bool(reg[allow_col])
        regime  = str(reg['regime'])
        vix     = float(reg['vix']) if pd.notna(reg['vix']) else float('nan')

        total_eq = cash + sum(
            p['shares'] * (_get_price(stock_b, t, date) or p['entry_price'])
            for t, p in positions.items()
        )

        # 청산
        for t in list(positions.keys()):
            df = stock_b.get(t)
            if df is None:
                continue
            try:
                row = df.loc[:date].iloc[-1]
            except Exception:
                continue
            close = float(row['Close'])
            atr   = float(row['ATR'])
            ma50  = float(row['MA50'])
            pos   = positions[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * B_ATR_MULT)
            if close <= pos['trailing_stop'] or close < ma50:
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions[t]

        # 진입
        if allow_b and len(positions) < B_MAX_POS:
            cands = []
            for t, df in stock_b.items():
                if t in positions:
                    continue
                try:
                    r = df.loc[:date].iloc[-1]
                except Exception:
                    continue
                cl, ma20, ma200 = float(r['Close']), float(r['MA20']), float(r['MA200'])
                if not (cl > ma200 and cl > ma20):
                    continue
                try:
                    rank = float(mom_rank_df.at[date, t]) if (date in mom_rank_df.index and t in mom_rank_df.columns) else float('nan')
                    rs   = float(mom_rs_df.at[date, t])   if (date in mom_rs_df.index   and t in mom_rs_df.columns)   else float('nan')
                except Exception:
                    continue
                if np.isnan(rank) or np.isnan(rs):
                    continue

                # 횡보장이면 강화 조건 적용
                is_sideways_day = (regime == 'SIDEWAYS')
                rs_threshold = rs_min if (use_sideways and is_sideways_day) else 0.0

                if rank >= (1.0 - B_RANK_TOP) and rs > rs_threshold:
                    cands.append((rank, t, r))

            cands.sort(reverse=True)
            for _, t, r in cands[:B_MAX_POS - len(positions)]:
                stop_dist = float(r['ATR']) * B_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * RISK_PER_TRADE
                shares   = min(risk_amt / stop_dist,
                               (total_eq * MAX_CAP_PER_STOCK) / float(r['Close']))
                cost     = shares * float(r['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions[t] = {
                        'shares':        shares,
                        'entry_price':   float(r['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(r['Close']) - stop_dist,
                    }
                    cash -= cost

        eq = cash + sum(
            p['shares'] * (_get_price(stock_b, t, date) or p['entry_price'])
            for t, p in positions.items()
        )
        eq_hist.append(eq)

    stats = calc_stats(eq_hist, INITIAL_CASH, [(t[0], t[1]) for t in trades])
    ts    = trade_stats(trades)
    stats.update(ts)
    return stats


# ─── [2] 전략 D — 코인 연동 개별주 ──────────────────────────────────────────

def run_strategy_d(
    crypto_data: dict[str, pd.DataFrame],
    all_dates: list,
    regime_df: pd.DataFrame,
) -> dict:
    cash       = INITIAL_CASH
    positions: dict = {}
    eq_hist    = []
    trades     = []

    for date in all_dates:
        vix = float('nan')
        if date in regime_df.index:
            r = regime_df.loc[date]
            vix = float(r['vix']) if pd.notna(r['vix']) else float('nan')

        total_eq = cash + sum(
            p['shares'] * (_get_price(crypto_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )

        # 청산
        for t in list(positions.keys()):
            df = crypto_data.get(t)
            if df is None:
                continue
            try:
                row = df.loc[:date].iloc[-1]
            except Exception:
                continue
            close = float(row['Close'])
            atr   = float(row['ATR'])
            rsi   = float(row['RSI'])
            ma20  = float(row['MA20'])
            pos   = positions[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * D_ATR_MULT)

            # TP1: RSI ≥ 55 → 50% 익절
            if rsi >= D_RSI_TP1 and not pos.get('half_sold'):
                half  = pos['shares'] * 0.5
                cash += half * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                pos['shares']   -= half
                pos['half_sold'] = True

            # STOP 또는 TP2 (MA20 도달)
            if close <= pos['trailing_stop'] or (pos.get('half_sold') and close >= ma20):
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions[t]

        # 진입
        if (not np.isnan(vix) and vix <= D_VIX_MAX) and len(positions) < D_MAX_POS:
            cands = []
            for t, df in crypto_data.items():
                if t in positions:
                    continue
                try:
                    row = df.loc[:date].iloc[-1]
                except Exception:
                    continue
                close  = float(row['Close'])
                rsi    = float(row['RSI'])
                ma20   = float(row['MA20'])
                ma200  = float(row['MA200'])
                if not (close > ma200 and close < ma20 and rsi < D_RSI_BUY):
                    continue
                cands.append((rsi, t, row))

            cands.sort()
            for _, t, row in cands[:D_MAX_POS - len(positions)]:
                stop_dist = float(row['ATR']) * D_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * D_RISK_PCT
                shares   = min(risk_amt / stop_dist,
                               (total_eq * MAX_CAP_PER_STOCK) / float(row['Close']))
                cost     = shares * float(row['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions[t] = {
                        'shares':        shares,
                        'entry_price':   float(row['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(row['Close']) - stop_dist,
                        'half_sold':     False,
                    }
                    cash -= cost

        eq = cash + sum(
            p['shares'] * (_get_price(crypto_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )
        eq_hist.append(eq)

    stats = calc_stats(eq_hist, INITIAL_CASH, [(t[0], t[1]) for t in trades])
    ts    = trade_stats(trades)
    stats.update(ts)
    return stats


# ─── [3] 전략 E — ETH ────────────────────────────────────────────────────────

def run_strategy_e(
    eth_data: dict[str, pd.DataFrame],
    all_dates: list,
    regime_df: pd.DataFrame,
) -> dict:
    """
    ETH-USD / ETHA 중 데이터 있는 것만 사용.
    MA200 조건 없음 (역사 짧음). RSI < 30 + VIX ≤ 30만 진입.
    """
    cash       = INITIAL_CASH
    positions: dict = {}
    eq_hist    = []
    trades     = []

    for date in all_dates:
        vix = float('nan')
        if date in regime_df.index:
            r = regime_df.loc[date]
            vix = float(r['vix']) if pd.notna(r['vix']) else float('nan')

        total_eq = cash + sum(
            p['shares'] * (_get_price(eth_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )

        # 청산
        for t in list(positions.keys()):
            df = eth_data.get(t)
            if df is None:
                continue
            try:
                row = df.loc[:date].iloc[-1]
            except Exception:
                continue
            close = float(row['Close'])
            atr   = float(row['ATR'])
            rsi   = float(row['RSI'])
            pos   = positions[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * E_ATR_MULT)

            if rsi >= E_RSI_TP1 and not pos.get('half_sold'):
                half  = pos['shares'] * 0.5
                cash += half * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                pos['shares']   -= half
                pos['half_sold'] = True

            if close <= pos['trailing_stop']:
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions[t]

        # 진입
        if (not np.isnan(vix) and vix <= E_VIX_MAX) and len(positions) < E_MAX_POS:
            cands = []
            for t, df in eth_data.items():
                if t in positions:
                    continue
                try:
                    row = df.loc[:date].iloc[-1]
                except Exception:
                    continue
                close = float(row['Close'])
                rsi   = float(row['RSI'])
                if rsi < E_RSI_BUY:
                    cands.append((rsi, t, row))

            cands.sort()
            for _, t, row in cands[:E_MAX_POS - len(positions)]:
                stop_dist = float(row['ATR']) * E_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * D_RISK_PCT
                shares   = min(risk_amt / stop_dist,
                               (total_eq * MAX_CAP_PER_STOCK) / float(row['Close']))
                cost     = shares * float(row['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions[t] = {
                        'shares':        shares,
                        'entry_price':   float(row['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(row['Close']) - stop_dist,
                        'half_sold':     False,
                    }
                    cash -= cost

        eq = cash + sum(
            p['shares'] * (_get_price(eth_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )
        eq_hist.append(eq)

    stats = calc_stats(eq_hist, INITIAL_CASH, [(t[0], t[1]) for t in trades])
    ts    = trade_stats(trades)
    stats.update(ts)
    return stats


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    log.info("티커 수집 중...")
    sp500  = get_sp500_tickers()
    ndx100 = get_nasdaq100_tickers()
    log.info(f"S&P500 {len(sp500)}개  NDX100 {len(ndx100)}개")

    base_tickers = list(set(sp500 + ndx100 + ['QQQ', 'HYG', 'QQQM'] + D_TICKERS))
    log.info(f"데이터 다운로드 중... ({START} ~ {END}, {len(base_tickers)}개)")
    raw = yf.download(
        base_tickers,
        start=START, end=END,
        group_by='ticker', threads=True, progress=False,
    )
    log.info("^VIX 다운로드 중...")
    vix_raw = yf.download('^VIX', start=START, end=END, progress=False, multi_level_index=False)

    # ETH 개별 다운로드 (배치 누락 대비)
    log.info("ETH 데이터 다운로드 중...")
    eth_data: dict[str, pd.DataFrame] = {}
    for t in E_TICKERS:
        df = build_single_data(t, START, END, min_bars=60)
        if df is not None:
            eth_data[t] = df
            log.info(f"  {t}: {len(df)}일  ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            log.warning(f"  {t}: 데이터 없음 (생략)")

    log.info("지표 계산 중...")
    stock_b = build_stock_data(raw, ndx100)
    stock_a = build_stock_data(raw, sp500)
    log.info(f"방패(A) {len(stock_a)}개  창(B) {len(stock_b)}개")

    # 코인 연동주 추출
    crypto_data: dict[str, pd.DataFrame] = {}
    for t in D_TICKERS:
        df_built = build_stock_data(raw, [t], min_bars=60)
        if df_built:
            crypto_data[t] = df_built[t]
        else:
            df_single = build_single_data(t, START, END, min_bars=60)
            if df_single is not None:
                crypto_data[t] = df_single
    log.info(f"코인 연동주: {list(crypto_data.keys())}")

    qqq_ohlc = raw['QQQ'][['High', 'Low', 'Close']].dropna()
    qqq_df   = raw['QQQ'][['Close']].dropna()
    hyg_cl   = raw['HYG']['Close'].dropna()
    qqqm_df  = raw['QQQM'][['Close']].dropna()
    vix_cl   = vix_raw['Close'].dropna()

    all_dates = sorted(set(
        d for sd in (stock_a, stock_b)
        for df in sd.values() for d in df.index
    ))
    log.info(f"백테스트 기간: {all_dates[0].date()} ~ {all_dates[-1].date()}  ({len(all_dates)}거래일)")

    log.info("시장 폭 계산 중...")
    breadth = precompute_breadth(stock_a)

    log.info("모멘텀 랭킹 계산 중...")
    mom_rank_df, mom_rs_df = precompute_momentum_ranks(stock_b, qqq_df)

    log.info("국면 시리즈 계산 중...")
    regime_df = build_regime_series(qqq_ohlc, breadth, vix_cl, hyg_cl, all_dates)

    sideways_days = (regime_df['regime'] == 'SIDEWAYS').sum()
    b_current_days  = regime_df['allow_b_current'].sum()
    b_sideways_days = regime_df['allow_b_sideways'].sum()
    log.info(
        f"국면 분포: {dict(regime_df['regime'].value_counts())}  "
        f"| B 허용일(현행) {b_current_days}일 → (신규) {b_sideways_days}일 "
        f"(+{b_sideways_days - b_current_days}일, 횡보 {sideways_days}일 중)"
    )

    # ── 백테스트 실행 ─────────────────────────────────────────────────────────
    log.info("[1] 전략 B 현행 백테스트...")
    b_current = run_b_comparison(
        stock_b, all_dates, regime_df, mom_rank_df, mom_rs_df,
        use_sideways=False, rs_min=0.0,
    )

    log.info("[1] 전략 B 횡보장 허용 백테스트...")
    b_sideways = run_b_comparison(
        stock_b, all_dates, regime_df, mom_rank_df, mom_rs_df,
        use_sideways=True, rs_min=B_SIDEWAYS_RS_MIN,
    )

    log.info("[2] 전략 D (코인 연동주) 백테스트...")
    d_result = run_strategy_d(crypto_data, all_dates, regime_df)

    log.info("[3] 전략 E (ETH) 백테스트...")
    if eth_data:
        eth_dates = sorted(set(d for df in eth_data.values() for d in df.index))
        e_result = run_strategy_e(eth_data, eth_dates, regime_df)
    else:
        e_result = None
        log.warning("ETH 데이터 없음 — 전략 E 생략")

    # ── QQQM Buy & Hold 기준선 ────────────────────────────────────────────────
    bh_shares = 0.0
    bh_hist   = []
    for i, date in enumerate(all_dates):
        try:
            sub = qqqm_df.loc[:date]
            if sub.empty:
                bh_hist.append(bh_hist[-1] if bh_hist else INITIAL_CASH)
                continue
            p = float(sub.iloc[-1]['Close'])
        except Exception:
            bh_hist.append(bh_hist[-1] if bh_hist else INITIAL_CASH)
            continue
        if bh_shares == 0.0 and p > 0:
            bh_shares = INITIAL_CASH * (1 - TOTAL_COST) / p
        bh_hist.append(bh_shares * p)
    bh_stats = calc_stats(bh_hist, INITIAL_CASH, [])

    # ─── 출력 ────────────────────────────────────────────────────────────────
    W = 90
    years = len(all_dates) / 252.0

    print("\n" + "=" * W)
    print(f"  백테스트  {START} ~ {END}  ({years:.1f}년  {len(all_dates)}거래일)".center(W))
    print("=" * W)

    # [1] 전략 B 비교
    print(f"\n  ─── [1] 전략 B 횡보장 허용 효과 비교 ───")
    print(f"  {'':30} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'거래수':>6} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
    print(f"  {'─'*30} {'─'*7} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*9} {'─'*7}")

    def _row(label, r):
        n    = r.get('n', 0)
        wr   = r.get('wr', 0.0)
        pnl  = r.get('avg_pnl_pct', float('nan'))
        hold = r.get('avg_hold_days', 0)
        pnl_str  = f"{pnl:>+8.2f}%" if not (isinstance(pnl, float) and np.isnan(pnl)) else "      N/A"
        hold_str = f"{hold:>5}일" if hold else "  N/A"
        return (
            f"  {label:<30} {r['cagr']:>+6.2f}% {r['mdd']:>7.2f}% "
            f"{r['sharpe']:>8.3f} {n:>5}건 {wr:>5.1f}% "
            f"{pnl_str} {hold_str}"
        )

    print(_row(f"B 현행 (BULL만)", b_current))
    print(_row(f"B 신규 (SIDEWAYS+VIX≤25+RS>10%)", b_sideways))
    print(_row(f"[기준] QQQM Buy & Hold", bh_stats))

    dcagr = b_sideways['cagr'] - b_current['cagr']
    dmdd  = b_sideways['mdd']  - b_current['mdd']
    dsh   = b_sideways['sharpe'] - b_current['sharpe']
    print(f"\n  ▷ 신규 vs 현행 차이:  CAGR {dcagr:>+.2f}%  MDD {dmdd:>+.2f}%  Sharpe {dsh:>+.3f}")

    # B 횡보장 구간 상세
    sw_block  = (~regime_df['allow_b_current'] & (regime_df['regime'] == 'SIDEWAYS')).sum()
    sw_allow  = (regime_df['allow_b_sideways'] & (regime_df['regime'] == 'SIDEWAYS') & (regime_df['vix'] <= B_SIDEWAYS_VIX_MAX)).sum()
    print(f"  ▷ 횡보장 {sideways_days}일 중: 현행 차단 {sw_block}일 → 신규 허용 {sw_allow}일")

    # [2] 전략 D
    print(f"\n  ─── [2] 전략 D — 코인 연동주  (대상: {', '.join(crypto_data.keys())}) ───")
    if not crypto_data:
        print("  ⚠️  데이터 없음")
    else:
        print(f"  {'':30} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'거래수':>6} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
        print(f"  {'─'*30} {'─'*7} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*9} {'─'*7}")
        print(_row(f"D (RSI<30+MA200+VIX≤30)", d_result))
        print(_row(f"[기준] QQQM Buy & Hold", bh_stats))
        print(f"\n  진입 조건: RSI < {D_RSI_BUY}  Close > MA200  Close < MA20  VIX ≤ {D_VIX_MAX}")
        print(f"  청산 조건: ATR×{D_ATR_MULT} 트레일 / TP1(RSI≥{D_RSI_TP1}→50%익절) / TP2(MA20 도달)")
        if d_result['n'] == 0:
            print("  ⚠️  진입 신호 없음 — RSI < 30 + MA200 조건이 너무 까다로울 수 있음 (RSI 임계값 완화 검토)")

    # [3] 전략 E
    print(f"\n  ─── [3] 전략 E — ETH  (대상: {', '.join(eth_data.keys()) if eth_data else '없음'}) ───")
    if not eth_data:
        print("  ⚠️  ETH 데이터 없음 (ETHA는 2024년 출시, 데이터 부족 가능)")
    elif e_result is None:
        print("  ⚠️  전략 E 실행 불가")
    else:
        eth_start = min(df.index[0] for df in eth_data.values()).date()
        eth_end   = max(df.index[-1] for df in eth_data.values()).date()
        print(f"  데이터 기간: {eth_start} ~ {eth_end}  ({len(eth_dates)}거래일)")
        print(f"  {'':30} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'거래수':>6} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
        print(f"  {'─'*30} {'─'*7} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*9} {'─'*7}")
        print(_row(f"E (ETH RSI<30+VIX≤30)", e_result))
        print(f"\n  ※ ETH-USD는 24/7 거래 (토/일 포함) → 주식 대비 신호 빈도 높음")
        print(f"  ※ ETHA는 2024년 출시, 데이터 {len(eth_data.get('ETHA', pd.DataFrame()))}일 — 해석 주의")

    # 최종 판단 요약
    print(f"\n{'=' * W}")
    print(f"  📋 종합 판단")
    print(f"{'=' * W}")

    if dcagr > 0 and dsh > 0:
        b_verdict = f"✅ 횡보장 허용 유효 (CAGR +{dcagr:.2f}%, Sharpe +{dsh:.3f})"
    elif dcagr > 0 and dsh <= 0:
        b_verdict = f"⚠️  CAGR 개선({dcagr:+.2f}%)이지만 Sharpe 저하({dsh:+.3f}) — MDD 확인 필요"
    else:
        b_verdict = f"❌ 횡보장 허용 비효율 (CAGR {dcagr:+.2f}%, Sharpe {dsh:+.3f})"

    print(f"  B 횡보장 허용: {b_verdict}")

    if d_result['n'] > 0:
        if d_result['cagr'] > bh_stats['cagr']:
            d_verdict = f"✅ QQQM BH 대비 초과수익 (D CAGR {d_result['cagr']:+.2f}% vs BH {bh_stats['cagr']:+.2f}%)"
        else:
            d_verdict = f"❌ QQQM BH 미달 (D CAGR {d_result['cagr']:+.2f}% vs BH {bh_stats['cagr']:+.2f}%)"
        print(f"  전략 D (코인주): {d_verdict}")
    else:
        print(f"  전략 D (코인주): ⚠️  신호 없음 — 임계값 조정 후 재테스트 필요")

    if e_result and e_result['n'] > 0:
        print(f"  전략 E (ETH): CAGR {e_result['cagr']:+.2f}%  승률 {e_result['wr']:.1f}%  {e_result['n']}건")
    elif e_result:
        print(f"  전략 E (ETH): ⚠️  신호 없음")

    print("=" * W + "\n")


if __name__ == '__main__':
    main()
