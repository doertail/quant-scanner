"""
backtest_b_ndx_regime.py
─────────────────────────────────────────────────────────────────────────────
전략 B 국면 감지 개선 백테스트

문제: S&P500 기반 3-레이어 국면 판단이 SIDEWAYS → B 차단
      그러나 NDX100 종목을 트레이딩하는 B의 국면 판단을 S&P 기준으로 하는 건 미스매치

개선: B 전용 NDX 국면 추가
      ndx_bull = QQQ > MA50 AND QQQ 3M수익률 > SPY 3M수익률
      allow_b_ndx = allow_b_current OR (ndx_bull AND VIX ≤ 25)

비교 3가지:
  [1] B 현행   : BULL만 (S&P500 3-레이어 기준)
  [2] B NDX    : 현행 + ndx_bull 조건 추가
  [3] QQQM B&H : 기준선
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
START          = '2005-01-01'
END            = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH   = 100_000.0
COMMISSION     = 0.0005
SLIPPAGE       = 0.0005
TOTAL_COST     = COMMISSION + SLIPPAGE
RISK_FREE_RATE = 0.035
RISK_PER_TRADE = 0.01
MAX_CAP        = 0.15
B_MAX_POS      = 10
B_MOM_LONG     = 126
B_MOM_SHORT    = 63
B_RANK_TOP     = 0.25
B_ATR_MULT     = 3.0

# NDX 국면 파라미터
NDX_QQQ_MA     = 50       # QQQ MA 기간 (단기 추세)
NDX_MOM_SHORT  = 63       # 3개월 (거래일)
NDX_VIX_MAX    = 25.0     # NDX bull 허용 최대 VIX

# 시장 국면 (scanner_v4 동일)
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
    tr   = pd.concat([
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


def build_stock_data(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    result = {}
    for t in tickers:
        try:
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].copy().dropna(subset=['Close'])
            if len(df) < 210:
                continue
            df = compute_indicators(df)
            if df[['RSI', 'MA20', 'MA50', 'MA200', 'ATR']].iloc[-1].isna().any():
                continue
            result[t] = df
        except Exception:
            pass
    return result


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


# ─── 국면 시리즈 (NDX 레이어 추가) ──────────────────────────────────────────

def build_regime_series(
    qqq_ohlc: pd.DataFrame,
    spy_close: pd.Series,
    breadth_series: pd.Series,
    vix_close: pd.Series,
    hyg_close: pd.Series,
    all_dates,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(all_dates)

    def fill(s: pd.Series) -> pd.Series:
        return s.reindex(dates, method='ffill')

    # ── 기존 3-레이어 (scanner_v4 동일) ──────────────────────────────────────
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

    allow_b_current = ~danger & ~panic & ~sweet_block & (regime_s == 'BULL')

    # ── NDX 전용 레이어 (신규) ─────────────────────────────────────────────────
    # 조건 1: QQQ > MA50 (단기 추세 유지)
    qqq_ma50  = fill(qqq_cl.rolling(NDX_QQQ_MA).mean())
    ndx_trend = cl_s > qqq_ma50

    # 조건 2: QQQ 3M 수익률 > SPY 3M 수익률 (NDX가 S&P 아웃퍼폼)
    spy_s       = fill(spy_close)
    qqq_ret_3m  = cl_s.pct_change(NDX_MOM_SHORT)
    spy_ret_3m  = spy_s.pct_change(NDX_MOM_SHORT)
    ndx_outperf = qqq_ret_3m > spy_ret_3m

    # ndx_bull: 두 조건 모두 만족
    ndx_bull = ndx_trend & ndx_outperf

    # allow_b_ndx: 현행 OR (ndx_bull AND VIX ≤ NDX_VIX_MAX AND DANGER/PANIC 아님)
    allow_b_ndx = (
        allow_b_current
        | (ndx_bull & (vix_s <= NDX_VIX_MAX) & ~danger & ~panic & ~sweet_block)
    )

    return pd.DataFrame({
        'regime':           regime_s,
        'vix_zone':         vix_zone,
        'vix':              vix_s,
        'allow_b_current':  allow_b_current,
        'allow_b_ndx':      allow_b_ndx,
        'ndx_bull':         ndx_bull,
        'ndx_trend':        ndx_trend,
        'ndx_outperf':      ndx_outperf,
    })


# ─── 통계 ────────────────────────────────────────────────────────────────────

def calc_stats(equity: list, initial: float, trades: list) -> dict:
    if len(equity) < 2:
        return {k: 0 for k in ['final','ret','cagr','mdd','sharpe','sortino','calmar','n','wins','wr']}
    final   = equity[-1]
    ret     = (final - initial) / initial * 100
    years   = len(equity) / 252.0
    cagr    = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0
    rets    = pd.Series(equity).pct_change().dropna()
    avg_r   = rets.mean() * 252
    std_r   = rets.std()  * np.sqrt(252)
    sharpe  = (avg_r - RISK_FREE_RATE) / std_r if std_r > 0 else 0
    dn_r    = rets[rets < 0].std() * np.sqrt(252)
    sortino = (avg_r - RISK_FREE_RATE) / dn_r if dn_r > 0 else 0
    pv      = pd.Series(equity)
    mdd     = ((pv - pv.cummax()) / pv.cummax()).min() * 100
    calmar  = cagr / abs(mdd) if mdd != 0 else 0
    wins    = sum(1 for t in trades if t[1] > t[0])
    wr      = wins / len(trades) * 100 if trades else 0.0
    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd,
                sharpe=sharpe, sortino=sortino, calmar=calmar,
                n=len(trades), wins=wins, wr=wr)


def trade_stats(trades: list) -> dict:
    if not trades:
        return {'n':0,'wr':0.0,'avg_pnl_pct':0.0,'avg_win_pct':0.0,'avg_loss_pct':0.0,'avg_hold_days':0}
    wins      = [t for t in trades if t[1] > t[0]]
    pnl_pcts  = [(t[1]-t[0])/t[0]*100 for t in trades]
    win_pcts  = [(t[1]-t[0])/t[0]*100 for t in trades if t[1]>t[0]]
    loss_pcts = [(t[1]-t[0])/t[0]*100 for t in trades if t[1]<=t[0]]
    hold_days = [(t[3]-t[2]).days for t in trades]
    return {
        'n':             len(trades),
        'wr':            len(wins)/len(trades)*100,
        'avg_pnl_pct':   sum(pnl_pcts)/len(pnl_pcts) if pnl_pcts else 0.0,
        'avg_win_pct':   sum(win_pcts)/len(win_pcts)  if win_pcts else 0.0,
        'avg_loss_pct':  sum(loss_pcts)/len(loss_pcts) if loss_pcts else 0.0,
        'avg_hold_days': int(sum(hold_days)/len(hold_days)) if hold_days else 0,
    }


def _get_price(data: dict, ticker: str, date) -> float | None:
    df = data.get(ticker)
    if df is None:
        return None
    try:
        return float(df.loc[:date].iloc[-1]['Close'])
    except Exception:
        return None


# ─── 전략 B 백테스트 ──────────────────────────────────────────────────────────

def run_b(
    stock_b:     dict[str, pd.DataFrame],
    all_dates:   list,
    regime_df:   pd.DataFrame,
    mom_rank_df: pd.DataFrame,
    mom_rs_df:   pd.DataFrame,
    allow_col:   str,    # 'allow_b_current' or 'allow_b_ndx'
) -> dict:
    cash       = INITIAL_CASH
    positions: dict = {}
    eq_hist    = []
    trades     = []

    for date in all_dates:
        if date not in regime_df.index:
            eq_hist.append(eq_hist[-1] if eq_hist else INITIAL_CASH)
            continue

        reg     = regime_df.loc[date]
        allow_b = bool(reg[allow_col])

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
                if rank >= (1.0 - B_RANK_TOP) and rs > 0:
                    cands.append((rank, t, r))

            cands.sort(reverse=True)
            for _, t, r in cands[:B_MAX_POS - len(positions)]:
                stop_dist = float(r['ATR']) * B_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * RISK_PER_TRADE
                shares   = min(risk_amt / stop_dist,
                               (total_eq * MAX_CAP) / float(r['Close']))
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
    stats.update(trade_stats(trades))
    return stats


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    log.info("티커 수집 중...")
    sp500  = get_sp500_tickers()
    ndx100 = get_nasdaq100_tickers()
    log.info(f"S&P500 {len(sp500)}개  NDX100 {len(ndx100)}개")

    base_tickers = list(set(sp500 + ndx100 + ['QQQ', 'SPY', 'HYG', 'QQQM']))
    log.info(f"데이터 다운로드 중... ({START} ~ {END}, {len(base_tickers)}개)")
    raw = yf.download(
        base_tickers,
        start=START, end=END,
        group_by='ticker', threads=True, progress=False,
    )
    log.info("^VIX 다운로드 중...")
    vix_raw = yf.download('^VIX', start=START, end=END, progress=False, multi_level_index=False)

    log.info("지표 계산 중...")
    stock_a = build_stock_data(raw, sp500)
    stock_b = build_stock_data(raw, ndx100)
    log.info(f"방패(A) {len(stock_a)}개  창(B) {len(stock_b)}개")

    qqq_ohlc  = raw['QQQ'][['High', 'Low', 'Close']].dropna()
    qqq_df    = raw['QQQ'][['Close']].dropna()
    spy_close = raw['SPY']['Close'].dropna()
    hyg_cl    = raw['HYG']['Close'].dropna()
    qqqm_df   = raw['QQQM'][['Close']].dropna()
    vix_cl    = vix_raw['Close'].dropna()

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
    regime_df = build_regime_series(qqq_ohlc, spy_close, breadth, vix_cl, hyg_cl, all_dates)

    # 국면 분포 출력
    rc = regime_df['regime'].value_counts()
    log.info("국면 분포: " + "  ".join(f"{k} {v}일" for k, v in rc.items()))

    b_curr_days = regime_df['allow_b_current'].sum()
    b_ndx_days  = regime_df['allow_b_ndx'].sum()
    ndx_bull_days = regime_df['ndx_bull'].sum()
    sideways_days = (regime_df['regime'] == 'SIDEWAYS').sum()
    ndx_bull_sideways = (regime_df['ndx_bull'] & (regime_df['regime'] == 'SIDEWAYS')).sum()
    log.info(
        f"B 허용일: 현행 {b_curr_days}일 → NDX개선 {b_ndx_days}일 (+{b_ndx_days-b_curr_days}일)"
    )
    log.info(
        f"ndx_bull 발동: {ndx_bull_days}일  "
        f"(횡보장 {sideways_days}일 중 {ndx_bull_sideways}일 = {ndx_bull_sideways/sideways_days*100:.1f}% 포착)"
    )

    log.info("[1] B 현행 백테스트...")
    r_current = run_b(stock_b, all_dates, regime_df, mom_rank_df, mom_rs_df, 'allow_b_current')

    log.info("[2] B NDX 개선 백테스트...")
    r_ndx = run_b(stock_b, all_dates, regime_df, mom_rank_df, mom_rs_df, 'allow_b_ndx')

    # QQQM Buy & Hold
    bh_shares = 0.0
    bh_hist   = []
    for date in all_dates:
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

    # ── 출력 ────────────────────────────────────────────────────────────────
    W     = 100
    years = len(all_dates) / 252.0

    print("\n" + "=" * W)
    print(f"  전략 B NDX 국면 개선 백테스트  {START} ~ {END}  ({years:.1f}년  {len(all_dates)}거래일)".center(W))
    print("=" * W)

    print(f"\n  ─── 국면 분포 ───")
    for k in ['BULL', 'SIDEWAYS', 'BEAR']:
        v = rc.get(k, 0)
        print(f"    {k:<10}: {v:>5}일 ({v/len(regime_df)*100:.1f}%)")

    print(f"\n  ─── ndx_bull 발동 분석 ───")
    print(f"    전체 발동     : {ndx_bull_days}일 ({ndx_bull_days/len(regime_df)*100:.1f}%)")
    print(f"    횡보장 발동   : {ndx_bull_sideways}일 / 횡보 {sideways_days}일 ({ndx_bull_sideways/sideways_days*100:.1f}%)")
    ndx_bull_bull = (regime_df['ndx_bull'] & (regime_df['regime'] == 'BULL')).sum()
    ndx_bull_bear = (regime_df['ndx_bull'] & (regime_df['regime'] == 'BEAR')).sum()
    print(f"    상승장 발동   : {ndx_bull_bull}일  하락장 발동: {ndx_bull_bear}일 (bear에서 B 열리는 위험 감지용)")
    print(f"    B 허용일 변화 : {b_curr_days}일 → {b_ndx_days}일 (+{b_ndx_days-b_curr_days}일)")

    print(f"\n  ─── 성과 비교 ───")
    print(f"  {'':35} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'Sortino':>8} {'Calmar':>7} {'거래':>6} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
    print(f"  {'─'*35} {'─'*7} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*6} {'─'*6} {'─'*9} {'─'*7}")

    def row(label, r):
        n    = r.get('n', 0)
        wr   = r.get('wr', 0.0)
        pnl  = r.get('avg_pnl_pct', float('nan'))
        hold = r.get('avg_hold_days', 0)
        pnl_s  = f"{pnl:>+8.2f}%" if not (isinstance(pnl, float) and np.isnan(pnl)) else "      N/A"
        hold_s = f"{hold:>5}일"    if hold else "   N/A"
        print(
            f"  {label:<35} {r['cagr']:>+6.2f}% {r['mdd']:>7.2f}% "
            f"{r['sharpe']:>8.3f} {r['sortino']:>8.3f} {r['calmar']:>7.3f} "
            f"{n:>5}건 {wr:>5.1f}% {pnl_s} {hold_s}"
        )

    row("B 현행  (BULL만, S&P 기준)", r_current)
    row(f"B NDX개선 (QQQ>MA{NDX_QQQ_MA} + QQQ>SPY 3M)", r_ndx)
    row("[기준] QQQM Buy & Hold", bh_stats)

    print(f"\n  ─── 현행 대비 차이 ───")
    dc = r_ndx['cagr']    - r_current['cagr']
    dm = r_ndx['mdd']     - r_current['mdd']
    ds = r_ndx['sharpe']  - r_current['sharpe']
    dt = r_ndx['sortino'] - r_current['sortino']
    dk = r_ndx['calmar']  - r_current['calmar']
    print(f"    CAGR    : {dc:>+.2f}%")
    print(f"    MDD     : {dm:>+.2f}%  (음수 = 낙폭 개선)")
    print(f"    Sharpe  : {ds:>+.3f}")
    print(f"    Sortino : {dt:>+.3f}")
    print(f"    Calmar  : {dk:>+.3f}")
    print(f"    거래수  : {r_current['n']}건 → {r_ndx['n']}건 (+{r_ndx['n']-r_current['n']}건)")

    # ── 판단 ─────────────────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  📋 종합 판단")
    print(f"{'=' * W}")

    if dc > 0 and ds > 0:
        verdict = f"✅ NDX 국면 개선 유효: CAGR +{dc:.2f}%, Sharpe +{ds:.3f}"
        recommend = "scanner_v4.py에 ndx_bull 조건 추가 권장"
    elif dc > 0 and ds <= 0:
        verdict = f"⚠️  CAGR 개선({dc:+.2f}%)이지만 Sharpe 저하({ds:+.3f}) — 거래 질 확인 필요"
        recommend = "rs_vs_qqq 강화 조건 추가 검토 (예: rs > 5%) 후 재테스트"
    else:
        verdict = f"❌ NDX 국면 개선 비효율 (CAGR {dc:+.2f}%, Sharpe {ds:+.3f})"
        recommend = "현행 유지 권장"

    print(f"  결과  : {verdict}")
    print(f"  권장  : {recommend}")
    print(f"\n  ─── NDX 개선 핵심 파라미터 ───")
    print(f"    QQQ MA 기간   : {NDX_QQQ_MA}일  (변경 가능: 20~100)")
    print(f"    비교 기간     : {NDX_MOM_SHORT}거래일 (3개월, 변경 가능: 21~126)")
    print(f"    VIX 허용 상한 : {NDX_VIX_MAX}  (변경 가능: 20~30)")
    print("=" * W + "\n")


if __name__ == '__main__':
    main()
