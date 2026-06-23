"""
backtest_v4.py — scanner_v4 전략 완전 반영 백테스트
────────────────────────────────────────────────────────────────────────────
scanner_v4와 동일한 로직:
  - 3-레이어 시장 국면 판단 (ADX / 시장폭 / VIX-RV) → BULL / SIDEWAYS / BEAR
  - VIX 구간 필터 (NORMAL / SWEET / DANGER / PANIC)
  - HYG 크레딧 필터 (VIX SWEET + HYG < MA50 → DANGER 상향)
  - 전략 A: S&P500 평균회귀 (RSI < 35, Close < MA20, Close > MA200)
  - 전략 B: NDX100 모멘텀  (6개월 수익률 상위 25% + 3개월 QQQ 아웃퍼폼, Close > MA20/MA200) — BULL만
  - 전략 C: VIX > 30 SPY/QQQ 패닉 매수 (VIX < 20 청산)
  - QQQM DCA: BULL $20 / SIDEWAYS $50 / BEAR $100

비교 기준:
  - QQQM DCA (매일 국면별 금액 적립)
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
START        = '2005-01-01'    # VIX 데이터 안정 구간
END          = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH = 100_000.0
COMMISSION   = 0.0005          # 0.05% per side
SLIPPAGE     = 0.0005          # 0.05% slippage
TOTAL_COST   = COMMISSION + SLIPPAGE
RISK_FREE_RATE = 0.035

# 포지션 사이징
RISK_PER_TRADE    = 0.01       # 손절 시 전체 자산의 1% 손실
MAX_CAP_PER_STOCK = 0.15       # 종목당 최대 15%
A_MAX_POS         = 10
B_MAX_POS         = 10

# 전략 A — S&P500 방패 (평균회귀)
A_RSI_BUY     = 35
A_RSI_PARTIAL = 50
A_ATR_MULT    = 3.0
A_ATR_TIGHT   = 1.5

# 전략 B — NDX100 모멘텀 (6개월 수익률 랭킹 + QQQ 상대강도)
# 구 방식: RSI > 65 (단기 과열 추격)
# 신 방식: 6개월 수익률 상위 25% + 3개월 수익률 QQQ 아웃퍼폼
B_MOM_LONG   = 126    # 6개월 (거래일)
B_MOM_SHORT  = 63     # 3개월 (거래일)
B_RANK_TOP   = 0.25   # 상위 25% 커트라인
B_ATR_MULT   = 3.0

# 전략 C — VIX 패닉 매수 (청산: VIX < 20)
C_TICKERS      = ['SPY', 'QQQ']
C_POSITION_PCT = 20.0          # 가용 현금의 20%
VIX_C_ENTRY    = 30.0
VIX_C_EXIT     = 20.0

# 전략 D — VIX 패닉 매수 (청산: RSI >= 70)
D_TICKERS      = ['SPY', 'QQQ']
D_POSITION_PCT = 20.0
D_RSI_EXIT     = 70.0

# 시장 국면 — scanner_v4 동일
QQQ_MA_PERIOD           = 200
RSI_PERIOD              = 14
ATR_PERIOD              = 14
ADX_PERIOD              = 14
ADX_TREND_THRESHOLD     = 25
ADX_SIDEWAYS_THRESHOLD  = 20
BREADTH_BULL            = 60.0
BREADTH_BEAR            = 40.0
VIX_RV_HIGH             = 1.2
VIX_RV_LOW              = 0.8

# VIX 구간 — scanner_v4 동일
VIX_SWEET_LOW  = 20.0
VIX_DANGER_LOW = 25.0
VIX_PANIC      = 30.0

# HYG
HYG_MA_PERIOD = 50

# DCA
DCA_BULL     = 20.0
DCA_BEAR     = 100.0
DCA_SIDEWAYS = 50.0


# ─── 티커 수집 ────────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    resp = requests.get(
        'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        headers=headers, timeout=15,
    )
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    return []


def get_nasdaq100_tickers() -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    resp = requests.get(
        'https://en.wikipedia.org/wiki/Nasdaq-100',
        headers=headers, timeout=15,
    )
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    return []


# ─── 지표 계산 ─────────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """RSI(14), MA20, MA50, MA200, ATR(14) — scanner_v4 동일"""
    delta      = df['Close'].diff()
    up         = delta.clip(lower=0)
    down       = -delta.clip(upper=0)
    df['RSI']  = 100 - (100 / (
        1 + up.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
          / down.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    ))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    prev        = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev).abs(),
        (df['Low']  - prev).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()
    return df


def compute_adx_series(df: pd.DataFrame) -> pd.DataFrame:
    """ADX, DI+, DI- 시리즈 반환 (scanner_v4 동일 로직의 시리즈 버전)"""
    high, low, close = df['High'], df['Low'], df['Close']
    prev   = close.shift(1)
    tr     = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    up_m   = high - high.shift(1)
    dn_m   = low.shift(1) - low
    p_dm   = up_m.where((up_m > dn_m) & (up_m > 0), 0.0)
    m_dm   = dn_m.where((dn_m > up_m) & (dn_m > 0), 0.0)
    atr14  = tr.ewm(com=ADX_PERIOD - 1, adjust=False).mean()
    p_di   = 100 * p_dm.ewm(com=ADX_PERIOD - 1, adjust=False).mean() / atr14
    m_di   = 100 * m_dm.ewm(com=ADX_PERIOD - 1, adjust=False).mean() / atr14
    dx     = 100 * (p_di - m_di).abs() / (p_di + m_di).replace(0, np.nan)
    adx    = dx.ewm(com=ADX_PERIOD - 1, adjust=False).mean()
    return pd.DataFrame({'ADX': adx, 'DI_plus': p_di, 'DI_minus': m_di})


def build_stock_data(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """종목별 OHLCV + 지표 DataFrame 딕셔너리 반환"""
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
    """S&P500 시장 폭 (MA200 상회 비율%) — 벡터 연산으로 고속 계산"""
    close_df = pd.DataFrame({t: df['Close']  for t, df in sp500_data.items()})
    ma200_df = pd.DataFrame({t: df['MA200']  for t, df in sp500_data.items()})
    above    = (close_df > ma200_df).sum(axis=1)
    total    = close_df.notna().sum(axis=1)
    return (above / total.replace(0, np.nan) * 100).rename('breadth')


def precompute_momentum_ranks(
    stock_data_b: dict[str, pd.DataFrame],
    qqq_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    NDX100 종목의 모멘텀 지표 사전 계산 (벡터 연산).
    반환:
      rank_df : DataFrame[date × ticker] = 6개월 수익률 백분위 (0~1, 높을수록 상위)
      rs_df   : DataFrame[date × ticker] = 종목 3개월 수익률 - QQQ 3개월 수익률
    """
    close_df   = pd.DataFrame({t: df['Close'] for t, df in stock_data_b.items()})
    ret_6m     = close_df.pct_change(B_MOM_LONG)
    rank_df    = ret_6m.rank(axis=1, pct=True)          # 날짜별 백분위 랭크

    ret_3m     = close_df.pct_change(B_MOM_SHORT)
    qqq_ret_3m = qqq_df['Close'].pct_change(B_MOM_SHORT)
    rs_df      = ret_3m.subtract(qqq_ret_3m, axis=0)    # 상대강도 (종목 - QQQ)

    return rank_df, rs_df


# ─── 통계 계산 ─────────────────────────────────────────────────────────────────

def calc_stats(equity_series: list, initial: float, trades: list) -> dict:
    if len(equity_series) < 2:
        return {k: 0 for k in ['final', 'ret', 'cagr', 'mdd', 'sharpe', 'sortino', 'calmar', 'n', 'wins', 'wr']}
    final   = equity_series[-1]
    ret     = (final - initial) / initial * 100
    years   = len(equity_series) / 252.0
    cagr    = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0
    rets    = pd.Series(equity_series).pct_change().dropna()
    avg_r   = rets.mean() * 252
    std_r   = rets.std()  * np.sqrt(252)
    sharpe  = (avg_r - RISK_FREE_RATE) / std_r if std_r > 0 else 0
    dn_r    = rets[rets < 0].std() * np.sqrt(252)
    sortino = (avg_r - RISK_FREE_RATE) / dn_r if dn_r > 0 else 0
    pv      = pd.Series(equity_series)
    mdd     = ((pv - pv.cummax()) / pv.cummax()).min() * 100
    calmar  = cagr / abs(mdd) if mdd != 0 else 0
    wins    = sum(1 for t in trades if t[1] > t[0])
    wr      = wins / len(trades) * 100 if trades else 0.0
    return dict(
        final=final, ret=ret, cagr=cagr, mdd=mdd,
        sharpe=sharpe, sortino=sortino, calmar=calmar,
        n=len(trades), wins=wins, wr=wr,
    )


# ─── 국면 시리즈 빌드 ─────────────────────────────────────────────────────────

def build_regime_series(
    qqq_ohlc: pd.DataFrame,       # QQQ High/Low/Close (ADX + MA200 + RV용)
    breadth_series: pd.Series,    # precompute_breadth() 결과
    vix_close: pd.Series,         # ^VIX Close
    hyg_close: pd.Series,         # HYG Close
    all_dates,
) -> pd.DataFrame:
    """
    날짜별로 (regime, vix_zone, vix, allow_a, allow_b, dca) 계산.
    scanner_v4 main() 로직의 벡터화 버전.
    """
    dates = pd.DatetimeIndex(all_dates)

    def fill(s: pd.Series) -> pd.Series:
        return s.reindex(dates, method='ffill')

    # QQQ 지표
    qqq_cl   = qqq_ohlc['Close'].dropna()
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

    bull_qqq = cl_s > ma200_s   # QQQ > MA200 폴백

    # ── Layer 1: ADX ─────────────────────────────────────────────────────
    # 기본값: QQQ vs MA200
    l1 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    # ADX < 20 → SIDEWAYS
    mask_sw = adx_s < ADX_SIDEWAYS_THRESHOLD
    l1 = l1.mask(mask_sw, 'SIDEWAYS')
    # ADX >= 25 → DI 방향
    mask_tr = adx_s >= ADX_TREND_THRESHOLD
    l1 = l1.mask(mask_tr &  (pdi_s > mdi_s),  'BULL')
    l1 = l1.mask(mask_tr & ~(pdi_s > mdi_s),  'BEAR')

    # ── Layer 2: 시장 폭 ─────────────────────────────────────────────────
    l2 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    l2 = l2.mask(brd_s > BREADTH_BULL, 'BULL')
    l2 = l2.mask(brd_s < BREADTH_BEAR, 'BEAR')
    l2 = l2.mask((brd_s >= BREADTH_BEAR) & (brd_s <= BREADTH_BULL) & brd_s.notna(), 'SIDEWAYS')

    # ── Layer 3: VIX/RV ──────────────────────────────────────────────────
    l3 = pd.Series(np.where(bull_qqq, 'BULL', 'BEAR'), index=dates, dtype=object)
    l3 = l3.mask(
        (ratio_s >= VIX_RV_LOW) & (ratio_s <= VIX_RV_HIGH) & ratio_s.notna(),
        'SIDEWAYS',
    )

    # ── 다수결 (2-of-3) ───────────────────────────────────────────────────
    vote_df = pd.DataFrame({'l1': l1, 'l2': l2, 'l3': l3})

    def _vote(row):
        v = list(row)
        if v.count('SIDEWAYS') >= 2:
            return 'SIDEWAYS'
        if v.count('BULL') >= 2:
            return 'BULL'
        return 'BEAR'

    regime_s = vote_df.apply(_vote, axis=1)

    # ── VIX 구간 ─────────────────────────────────────────────────────────
    vix_zone = pd.Series('NORMAL', index=dates, dtype=object)
    vix_zone = vix_zone.mask(vix_s > VIX_SWEET_LOW,  'SWEET')
    vix_zone = vix_zone.mask(vix_s > VIX_DANGER_LOW, 'DANGER')
    vix_zone = vix_zone.mask(vix_s > VIX_PANIC,      'PANIC')

    # ── allow_entry ───────────────────────────────────────────────────────
    danger      = vix_zone == 'DANGER'
    panic       = vix_zone == 'PANIC'
    hyg_ok      = hyg_s > hyg_ma
    sweet_block = (vix_zone == 'SWEET') & ~hyg_ok

    allow_a = ~danger & ~sweet_block
    allow_b = ~danger & ~panic & ~sweet_block & (regime_s == 'BULL')

    # ── DCA 금액 ──────────────────────────────────────────────────────────
    dca = pd.Series(DCA_SIDEWAYS, index=dates)
    dca = dca.mask(regime_s == 'BULL', DCA_BULL)
    dca = dca.mask(regime_s == 'BEAR', DCA_BEAR)

    return pd.DataFrame({
        'regime':   regime_s,
        'vix_zone': vix_zone,
        'vix':      vix_s,
        'allow_a':  allow_a,
        'allow_b':  allow_b,
        'dca':      dca,
    })


# ─── 헬퍼 ───────────────────────────────────────────────────────────────────

def _get_price(etf_data: dict[str, pd.DataFrame], ticker: str, date) -> float | None:
    df = etf_data.get(ticker)
    if df is None:
        return None
    try:
        row = df.loc[:date].iloc[-1]
        return float(row['Close'])
    except Exception:
        return None


def _portfolio_value(
    pos_a: dict, pos_b: dict, pos_c: dict,
    data_a: dict, data_b: dict,
    etf_data: dict,
    date,
) -> float:
    val = 0.0
    for t, p in pos_a.items():
        if t in data_a:
            try:
                val += p['shares'] * float(data_a[t].loc[:date].iloc[-1]['Close'])
            except Exception:
                pass
    for t, p in pos_b.items():
        if t in data_b:
            try:
                val += p['shares'] * float(data_b[t].loc[:date].iloc[-1]['Close'])
            except Exception:
                pass
    for t, p in pos_c.items():
        price = _get_price(etf_data, t, date)
        if price:
            val += p['shares'] * price
    return val


# ─── VIX 패닉 단독 전략 백테스트 ────────────────────────────────────────────

def run_vix_panic_variant(
    all_dates: list,
    regime_df: pd.DataFrame,
    etf_data: dict[str, pd.DataFrame],       # {'SPY': Close df, 'QQQ': Close df}
    etf_indicators: dict[str, pd.DataFrame], # {'SPY': RSI/MA50/ATR df, 'QQQ': ...}
    initial_cash: float,
    tickers: list,
    position_pct: float,
    exit_config: dict,
) -> dict:
    """
    VIX 패닉 매수 전략 단독 백테스트 (A/B 없이 독립 실행).

    exit_config 타입:
      vix_lt       : VIX < threshold
      rsi_gte      : RSI >= threshold
      atr_stop     : ATR × atr_mult 트레일링 스톱만
      vix_and_ma50 : VIX < vix_threshold AND price > MA50
      split        : VIX < vix_threshold → 50% / 나머지는 ATR stop
      vix_or_pct   : VIX < vix_threshold OR 수익률 >= pct
    """
    cash       = initial_cash
    positions  = {}   # {ticker: {shares, entry_price, entry_date, trailing_stop, half_sold}}
    eq_hist    = []
    trades     = []   # [{'ep', 'xp', 'ed', 'xd'}]
    regime_idx = set(regime_df.index)

    for date in all_dates:
        if date not in regime_idx:
            eq_hist.append(eq_hist[-1] if eq_hist else initial_cash)
            continue

        reg      = regime_df.loc[date]
        vix_zone = str(reg['vix_zone'])
        vix      = float(reg['vix']) if pd.notna(reg['vix']) else float('nan')

        # ── 청산 ──────────────────────────────────────────────────────────
        for t in list(positions.keys()):
            if t not in positions:
                continue
            price = _get_price(etf_data, t, date)
            if not price:
                continue
            pos = positions[t]

            rsi = ma50 = atr = float('nan')
            if t in etf_indicators:
                try:
                    r    = etf_indicators[t].loc[:date].iloc[-1]
                    rsi  = float(r['RSI'])
                    ma50 = float(r['MA50'])
                    atr  = float(r['ATR'])
                except Exception:
                    pass

            etype = exit_config['type']

            # ATR 트레일링 스톱 갱신
            if etype in ('atr_stop', 'split') and not np.isnan(atr):
                new_stop = price - atr * exit_config.get('atr_mult', 3.0)
                pos['trailing_stop'] = max(pos.get('trailing_stop', new_stop), new_stop)

            should_exit = False

            if etype == 'hold_forever':
                should_exit = False  # 청산 없음 — 기간 말까지 보유

            elif etype == 'vix_lt':
                should_exit = not np.isnan(vix) and vix < exit_config['threshold']

            elif etype == 'rsi_gte':
                should_exit = not np.isnan(rsi) and rsi >= exit_config['threshold']

            elif etype == 'atr_stop':
                should_exit = price <= pos.get('trailing_stop', -999)

            elif etype == 'vix_and_ma50':
                should_exit = (not np.isnan(vix) and vix < exit_config['vix_threshold']
                               and not np.isnan(ma50) and price > ma50)

            elif etype == 'split':
                # TP1: VIX < 20 → 50% 청산
                if not pos.get('half_sold') and not np.isnan(vix) and vix < exit_config['vix_threshold']:
                    half = pos['shares'] * 0.5
                    cash += half * price * (1 - TOTAL_COST)
                    trades.append({'ep': pos['entry_price'], 'xp': price,
                                   'ed': pos['entry_date'],  'xd': date})
                    pos['shares']   -= half
                    pos['half_sold'] = True
                # TP2: ATR 스톱
                should_exit = price <= pos.get('trailing_stop', -999)

            elif etype == 'vix_or_pct':
                pnl = (price - pos['entry_price']) / pos['entry_price']
                should_exit = ((not np.isnan(vix) and vix < exit_config['vix_threshold'])
                               or pnl >= exit_config['pct'])

            if should_exit and t in positions:
                cash += pos['shares'] * price * (1 - TOTAL_COST)
                trades.append({'ep': pos['entry_price'], 'xp': price,
                               'ed': pos['entry_date'],  'xd': date})
                del positions[t]

        # ── 진입 ──────────────────────────────────────────────────────────
        if vix_zone == 'PANIC' and not np.isnan(vix):
            for t in tickers:
                if t in positions:
                    continue
                price = _get_price(etf_data, t, date)
                if not price:
                    continue
                alloc  = cash * position_pct / 100
                shares = alloc * (1 - TOTAL_COST) / price
                cost   = shares * price * (1 + TOTAL_COST)
                if cash < cost or cost <= 0:
                    continue
                pos = {
                    'shares':      shares,
                    'entry_price': price,
                    'entry_date':  date,
                    'half_sold':   False,
                }
                if exit_config['type'] in ('atr_stop', 'split'):
                    atr = float('nan')
                    if t in etf_indicators:
                        try:
                            atr = float(etf_indicators[t].loc[:date].iloc[-1]['ATR'])
                        except Exception:
                            pass
                    mult = exit_config.get('atr_mult', 3.0)
                    pos['trailing_stop'] = (price - atr * mult
                                            if not np.isnan(atr) else price * 0.85)
                positions[t] = pos
                cash -= cost

        # ── 자산 기록 ──────────────────────────────────────────────────────
        port_val = sum(
            p['shares'] * (_get_price(etf_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )
        eq_hist.append(cash + port_val)

    # ── 통계 ───────────────────────────────────────────────────────────────
    stats     = calc_stats(eq_hist, initial_cash, [(t['ep'], t['xp']) for t in trades])
    pnl_pcts  = [(t['xp'] - t['ep']) / t['ep'] * 100 for t in trades]
    wins      = [t for t in trades if t['xp'] > t['ep']]
    losses    = [t for t in trades if t['xp'] <= t['ep']]
    holds     = [(t['xd'] - t['ed']).days for t in trades]

    stats['avg_pnl_pct']   = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0
    stats['avg_win_pct']   = sum((t['xp']-t['ep'])/t['ep']*100 for t in wins) / len(wins) if wins else 0.0
    stats['avg_loss_pct']  = sum((t['xp']-t['ep'])/t['ep']*100 for t in losses) / len(losses) if losses else 0.0
    stats['avg_hold_days'] = int(sum(holds) / len(holds)) if holds else 0
    return stats


# ─── 백테스트 루프 ───────────────────────────────────────────────────────────

def run_backtest(
    stock_data_a: dict[str, pd.DataFrame],
    stock_data_b: dict[str, pd.DataFrame],
    all_dates: list,
    regime_df: pd.DataFrame,
    qqqm_df: pd.DataFrame,
    etf_data: dict[str, pd.DataFrame],          # {'SPY': df(Close), 'QQQ': df(Close)}
    etf_indicators: dict[str, pd.DataFrame],    # {'SPY': df(RSI/MA...), 'QQQ': df(RSI/MA...)}
    mom_rank_df: pd.DataFrame,                  # 6개월 수익률 백분위 (precompute_momentum_ranks)
    mom_rs_df: pd.DataFrame,                    # 3개월 QQQ 상대강도
) -> dict:
    cash          = INITIAL_CASH
    positions_a: dict = {}
    positions_b: dict = {}
    positions_c: dict = {}
    positions_d: dict = {}
    eq_hist            = []
    trades_a, trades_b, trades_c, trades_d = [], [], [], []
    qqqm_shares        = 0.0
    total_invested_dca = 0.0

    # 날짜 인덱스 변환 (str → Timestamp 매핑 방지)
    regime_idx = set(regime_df.index)

    for date in all_dates:
        if date not in regime_idx:
            eq_hist.append(eq_hist[-1] if eq_hist else INITIAL_CASH)
            continue

        reg      = regime_df.loc[date]
        allow_a  = bool(reg['allow_a'])
        allow_b  = bool(reg['allow_b'])
        vix_zone = str(reg['vix_zone'])
        vix      = float(reg['vix']) if pd.notna(reg['vix']) else float('nan')
        dca      = float(reg['dca'])

        total_eq = cash + _portfolio_value(
            positions_a, positions_b, {**positions_c, **positions_d},
            stock_data_a, stock_data_b, etf_data, date,
        )

        # ── 전략 C: 청산 (VIX < 20) ────────────────────────────────────────
        if not np.isnan(vix) and vix < VIX_C_EXIT:
            for t in list(positions_c.keys()):
                price = _get_price(etf_data, t, date)
                if price:
                    pos   = positions_c.pop(t)
                    cash += pos['shares'] * price * (1 - TOTAL_COST)
                    trades_c.append((pos['entry_price'], price, pos['entry_date'], date))

        # ── 전략 D: 청산 (RSI >= 70) ─────────────────────────────────────────
        for t in list(positions_d.keys()):
            ind_df = etf_indicators.get(t)
            if ind_df is None:
                continue
            try:
                rsi = float(ind_df.loc[:date].iloc[-1]['RSI'])
            except Exception:
                continue
            if rsi >= D_RSI_EXIT:
                price = _get_price(etf_data, t, date)
                if price:
                    pos   = positions_d.pop(t)
                    cash += pos['shares'] * price * (1 - TOTAL_COST)
                    trades_d.append((pos['entry_price'], price, pos['entry_date'], date))

        # ── 전략 A: 청산 ────────────────────────────────────────────────────
        for t in list(positions_a.keys()):
            df = stock_data_a.get(t)
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
            pos   = positions_a[t]
            mult  = A_ATR_TIGHT if rsi >= A_RSI_PARTIAL else A_ATR_MULT
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * mult)

            # TP1: RSI >= 50 → 50% 익절
            if rsi >= A_RSI_PARTIAL and not pos['half_sold']:
                half  = pos['shares'] * 0.5
                cash += half * close * (1 - TOTAL_COST)
                trades_a.append((pos['entry_price'], close, pos['entry_date'], date))
                pos['shares']   -= half
                pos['half_sold'] = True

            # STOP 또는 TP2 (MA20 도달)
            if close <= pos['trailing_stop'] or close >= ma20:
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades_a.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions_a[t]

        # ── 전략 B: 청산 ────────────────────────────────────────────────────
        for t in list(positions_b.keys()):
            df = stock_data_b.get(t)
            if df is None:
                continue
            try:
                row = df.loc[:date].iloc[-1]
            except Exception:
                continue
            close = float(row['Close'])
            atr   = float(row['ATR'])
            ma50  = float(row['MA50'])
            pos   = positions_b[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * B_ATR_MULT)

            if close <= pos['trailing_stop'] or close < ma50:
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades_b.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions_b[t]

        # ── 전략 A: 진입 ────────────────────────────────────────────────────
        if allow_a and len(positions_a) < A_MAX_POS:
            cands = []
            for t, df in stock_data_a.items():
                if t in positions_a:
                    continue
                try:
                    r = df.loc[:date].iloc[-1]
                except Exception:
                    continue
                cl, rsi, ma20, ma200 = float(r['Close']), float(r['RSI']), float(r['MA20']), float(r['MA200'])
                if cl > ma200 and cl < ma20 and rsi < A_RSI_BUY:
                    cands.append((rsi, t, r))
            cands.sort()  # RSI 낮은 순
            for _, t, r in cands[:A_MAX_POS - len(positions_a)]:
                stop_dist = float(r['ATR']) * A_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * RISK_PER_TRADE
                shares   = risk_amt / stop_dist
                max_sh   = (total_eq * MAX_CAP_PER_STOCK) / float(r['Close'])
                shares   = min(shares, max_sh)
                cost     = shares * float(r['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions_a[t] = {
                        'shares':        shares,
                        'entry_price':   float(r['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(r['Close']) - stop_dist,
                        'half_sold':     False,
                    }
                    cash -= cost

        # ── 전략 B: 진입 (BULL + allow_b만) ─────────────────────────────────
        # 신호: 6개월 수익률 상위 25% + 3개월 QQQ 아웃퍼폼 + MA20/MA200 위
        if allow_b and len(positions_b) < B_MAX_POS:
            cands = []
            for t, df in stock_data_b.items():
                if t in positions_b:
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
                if rank >= (1.0 - B_RANK_TOP) and rs > 0:   # 상위 25% AND QQQ 아웃퍼폼
                    cands.append((rank, t, r))
            cands.sort(reverse=True)  # 랭크 높은 순
            for _, t, r in cands[:B_MAX_POS - len(positions_b)]:
                stop_dist = float(r['ATR']) * B_ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * RISK_PER_TRADE
                shares   = risk_amt / stop_dist
                max_sh   = (total_eq * MAX_CAP_PER_STOCK) / float(r['Close'])
                shares   = min(shares, max_sh)
                cost     = shares * float(r['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions_b[t] = {
                        'shares':        shares,
                        'entry_price':   float(r['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(r['Close']) - stop_dist,
                    }
                    cash -= cost

        # ── 전략 C: 진입 (VIX > 30) ─────────────────────────────────────────
        if vix_zone == 'PANIC' and not np.isnan(vix):
            for t in C_TICKERS:
                if t in positions_c:
                    continue
                price = _get_price(etf_data, t, date)
                if not price:
                    continue
                alloc  = cash * C_POSITION_PCT / 100
                shares = alloc * (1 - TOTAL_COST) / price
                cost   = shares * price * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions_c[t] = {'shares': shares, 'entry_price': price, 'entry_date': date}
                    cash -= cost

        # ── 전략 D: 진입 (VIX > 30, C와 동일 진입 / 청산만 다름) ─────────────
        if vix_zone == 'PANIC' and not np.isnan(vix):
            for t in D_TICKERS:
                if t in positions_d:
                    continue
                price = _get_price(etf_data, t, date)
                if not price:
                    continue
                alloc  = cash * D_POSITION_PCT / 100
                shares = alloc * (1 - TOTAL_COST) / price
                cost   = shares * price * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions_d[t] = {'shares': shares, 'entry_price': price, 'entry_date': date}
                    cash -= cost

        # ── QQQM DCA ─────────────────────────────────────────────────────────
        if date in qqqm_df.index:
            p = float(qqqm_df.loc[date, 'Close'])
            if p > 0:
                qqqm_shares        += dca * (1 - TOTAL_COST) / p
                total_invested_dca += dca

        # ── 일별 자산 기록 ───────────────────────────────────────────────────
        eq = cash + _portfolio_value(
            positions_a, positions_b, {**positions_c, **positions_d},
            stock_data_a, stock_data_b, etf_data, date,
        )
        eq_hist.append(eq)

    dca_final = (
        qqqm_shares * float(qqqm_df['Close'].iloc[-1])
        if not qqqm_df.empty else 0.0
    )

    def _trade_stats(trades: list) -> dict:
        if not trades:
            return {'n': 0, 'wins': 0, 'wr': 0.0,
                    'avg_pnl_pct': 0.0, 'avg_win_pct': 0.0, 'avg_loss_pct': 0.0,
                    'avg_hold_days': 0, 'avg_cagr_pct': 0.0}
        # trades 원소: (entry_price, exit_price) or (entry_price, exit_price, entry_date, exit_date)
        has_dates = len(trades[0]) == 4
        wins      = [t for t in trades if t[1] > t[0]]
        pnl_pcts  = [(t[1] - t[0]) / t[0] * 100 for t in trades]
        win_pcts  = [(t[1] - t[0]) / t[0] * 100 for t in trades if t[1] > t[0]]
        loss_pcts = [(t[1] - t[0]) / t[0] * 100 for t in trades if t[1] <= t[0]]

        if has_dates:
            hold_days = [(t[3] - t[2]).days for t in trades]
            avg_hold  = sum(hold_days) / len(hold_days)
            # 연환산 수익률: 평균 수익률을 평균 보유기간 기준으로 연환산
            # "이 속도로 계속 재투자하면 연간 몇 %"를 나타냄 (거래일 기준 252일)
            avg_pnl = sum(pnl_pcts) / len(pnl_pcts) / 100
            avg_cagr = ((1 + avg_pnl) ** (252.0 / avg_hold) - 1) * 100 if avg_hold > 0 else 0.0
        else:
            avg_hold = 0
            avg_cagr = 0.0

        return {
            'n':             len(trades),
            'wins':          len(wins),
            'wr':            len(wins) / len(trades) * 100,
            'avg_pnl_pct':   sum(pnl_pcts)  / len(pnl_pcts)  if pnl_pcts  else 0.0,
            'avg_win_pct':   sum(win_pcts)  / len(win_pcts)  if win_pcts  else 0.0,
            'avg_loss_pct':  sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0,
            'avg_hold_days': int(avg_hold),
            'avg_cagr_pct':  avg_cagr,
        }

    return {
        'main': calc_stats(eq_hist, INITIAL_CASH, trades_a + trades_b + trades_c + trades_d),
        'equity': eq_hist,   # 일별 자산곡선 (len == all_dates) — 레버리지/벤치마크 분석용
        'A':    _trade_stats(trades_a),
        'B':    _trade_stats(trades_b),
        'C':    _trade_stats(trades_c),
        'D':    _trade_stats(trades_d),
        'DCA': {
            'final':    dca_final,
            'invested': total_invested_dca,
            'ret':      (dca_final - total_invested_dca) / total_invested_dca * 100
                        if total_invested_dca > 0 else 0.0,
        },
    }


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    log.info("티커 수집 중...")
    sp500  = get_sp500_tickers()
    ndx100 = get_nasdaq100_tickers()
    log.info(f"S&P500 {len(sp500)}개  NDX100 {len(ndx100)}개")

    base_tickers = list(set(sp500 + ndx100 + ['QQQ', 'SPY', 'HYG', 'QQQM']))
    log.info(f"데이터 다운로드 중... ({START} ~ {END}, 총 {len(base_tickers)}개 티커)")
    raw = yf.download(
        base_tickers,
        start=START, end=END,
        group_by='ticker', threads=True, progress=False,
    )
    log.info("^VIX 개별 다운로드 중...")
    vix_raw = yf.download(
        '^VIX', start=START, end=END,
        progress=False, multi_level_index=False,
    )

    log.info("지표 계산 중...")
    stock_a = build_stock_data(raw, sp500)
    stock_b = build_stock_data(raw, ndx100)
    log.info(f"방패(A) 유니버스 {len(stock_a)}개  창(B) 유니버스 {len(stock_b)}개")

    qqq_ohlc = raw['QQQ'][['High', 'Low', 'Close']].dropna()
    hyg_cl   = raw['HYG']['Close'].dropna()
    spy_df   = raw['SPY'][['Close']].dropna()
    qqq_df   = raw['QQQ'][['Close']].dropna()
    qqqm_df  = raw['QQQM'][['Close']].dropna()
    vix_cl   = vix_raw['Close'].dropna()

    # 전략 D용: SPY/QQQ RSI 계산
    log.info("SPY/QQQ 지표 계산 중 (전략 D용)...")
    etf_indicators = {}
    for t in ['SPY', 'QQQ']:
        df_ohlcv = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].copy().dropna(subset=['Close'])
        etf_indicators[t] = compute_indicators(df_ohlcv)

    all_dates = sorted(set(
        d for sd in (stock_a, stock_b)
        for df in sd.values() for d in df.index
    ))
    log.info(f"백테스트 기간: {all_dates[0].date()} ~ {all_dates[-1].date()}  ({len(all_dates)}거래일)")

    log.info("시장 폭 사전 계산 중...")
    breadth = precompute_breadth(stock_a)

    log.info("모멘텀 랭킹 사전 계산 중... (전략 B 신호)")
    mom_rank_df, mom_rs_df = precompute_momentum_ranks(stock_b, qqq_df)

    log.info("국면 시리즈 계산 중...")
    regime_df = build_regime_series(qqq_ohlc, breadth, vix_cl, hyg_cl, all_dates)
    log.info(
        "국면 분포: " +
        "  ".join(f"{k} {v}일" for k, v in regime_df['regime'].value_counts().items())
    )
    log.info(
        "VIX 구간: " +
        "  ".join(f"{k} {v}일" for k, v in regime_df['vix_zone'].value_counts().items())
    )

    log.info("백테스트 실행 중... (수 분 소요)")
    result = run_backtest(
        stock_a, stock_b, all_dates, regime_df,
        qqqm_df,
        etf_data={'SPY': spy_df, 'QQQ': qqq_df},
        etf_indicators=etf_indicators,
        mom_rank_df=mom_rank_df,
        mom_rs_df=mom_rs_df,
    )

    # ── 결과 출력 ─────────────────────────────────────────────────────────
    W = 80
    years = len(all_dates) / 252.0

    print("\n" + "=" * W)
    print(f"  📊 Backtest v4 — scanner_v4 전략 완전 반영  |  {START} ~ {END}".center(W))
    print("=" * W)

    m = result['main']
    print(f"\n  ▶ 합산 포트폴리오 (전략 A + B + C 통합)")
    print(f"    초기 자산   : ${INITIAL_CASH:>15,.0f}")
    print(f"    최종 자산   : ${m['final']:>15,.0f}    (총 수익률 {m['ret']:+.1f}%)")
    print(f"    CAGR        : {m['cagr']:>+.2f}%")
    print(f"    MDD         : {m['mdd']:.2f}%")
    print(f"    Sharpe      : {m['sharpe']:.3f}")
    print(f"    Sortino     : {m['sortino']:.3f}")
    print(f"    Calmar      : {m['calmar']:.3f}")
    print(f"    총 거래수   : {m['n']}건  승률 {m['wr']:.1f}%")

    print(f"\n  ▶ 전략별 세부")
    print(f"    {'':27} {'거래':>5} {'승률':>6} {'평균보유':>7} {'평균수익':>8} {'연환산CAGR':>10} {'승평균':>7} {'패평균':>7}")
    print(f"    {'─'*27} {'─'*5} {'─'*6} {'─'*7} {'─'*8} {'─'*10} {'─'*7} {'─'*7}")
    a = result['A']
    print(f"    {'A (방패/평균회귀)':<27} {a['n']:>5}건 {a['wr']:>5.1f}% {a['avg_hold_days']:>5}일 {a['avg_pnl_pct']:>+7.2f}% {a['avg_cagr_pct']:>+9.1f}% {a['avg_win_pct']:>+6.1f}% {a['avg_loss_pct']:>+6.1f}%")
    b = result['B']
    print(f"    {'B (6M랭킹+RS/모멘텀)':<27} {b['n']:>5}건 {b['wr']:>5.1f}% {b['avg_hold_days']:>5}일 {b['avg_pnl_pct']:>+7.2f}% {b['avg_cagr_pct']:>+9.1f}% {b['avg_win_pct']:>+6.1f}% {b['avg_loss_pct']:>+6.1f}%")
    c = result['C']
    if c['n'] > 0:
        print(f"    {'C (VIX패닉/VIX<20청산)':<27} {c['n']:>5}건 {c['wr']:>5.1f}% {c['avg_hold_days']:>5}일 {c['avg_pnl_pct']:>+7.2f}% {c['avg_cagr_pct']:>+9.1f}% {c['avg_win_pct']:>+6.1f}% {c['avg_loss_pct']:>+6.1f}%")
    else:
        print(f"    C (VIX패닉/VIX<20청산): 진입 없음")
    d = result['D']
    if d['n'] > 0:
        print(f"    {'D (VIX패닉/RSI70청산)':<27} {d['n']:>5}건 {d['wr']:>5.1f}% {d['avg_hold_days']:>5}일 {d['avg_pnl_pct']:>+7.2f}% {d['avg_cagr_pct']:>+9.1f}% {d['avg_win_pct']:>+6.1f}% {d['avg_loss_pct']:>+6.1f}%")
    else:
        print(f"    D (VIX패닉/RSI70청산) : 진입 없음")

    print(f"\n  ▶ C vs D 비교 (진입 동일 — VIX>30, 청산 기준만 다름)")
    print(f"    {'항목':<12}  {'C (VIX<20)':>14}  {'D (RSI≥70)':>14}")
    print(f"    {'─'*12}  {'─'*14}  {'─'*14}")
    print(f"    {'거래수':<12}  {c['n']:>14}건  {d['n']:>14}건")
    if c['n'] > 0 and d['n'] > 0:
        print(f"    {'승률':<12}  {c['wr']:>13.1f}%  {d['wr']:>13.1f}%")
        print(f"    {'평균수익':<12}  {c['avg_pnl_pct']:>+13.2f}%  {d['avg_pnl_pct']:>+13.2f}%")
        print(f"    {'평균승리':<12}  {c['avg_win_pct']:>+13.1f}%  {d['avg_win_pct']:>+13.1f}%")
        print(f"    {'평균손실':<12}  {c['avg_loss_pct']:>+13.1f}%  {d['avg_loss_pct']:>+13.1f}%")

    dca = result['DCA']
    print(f"\n  ▶ QQQM DCA (비교 기준선)")
    print(f"    총 투자금   : ${dca['invested']:>15,.0f}")
    print(f"    최종 자산   : ${dca['final']:>15,.0f}    (총 수익률 {dca['ret']:+.1f}%)")
    dca_cagr = ((dca['final'] / dca['invested']) ** (1 / years) - 1) * 100 if dca['invested'] > 0 else 0
    print(f"    CAGR(투자비기준): {dca_cagr:+.2f}%")

    print(f"\n  ▶ 시장 국면 분포 ({len(all_dates)}거래일)")
    rc = regime_df['regime'].value_counts()
    for k in ['BULL', 'SIDEWAYS', 'BEAR']:
        v = rc.get(k, 0)
        print(f"    {k:<10}: {v:>5}일 ({v/len(regime_df)*100:.1f}%)")

    print(f"\n  ▶ VIX 구간 분포")
    vc = regime_df['vix_zone'].value_counts()
    for k in ['NORMAL', 'SWEET', 'DANGER', 'PANIC']:
        v = vc.get(k, 0)
        print(f"    {k:<10}: {v:>5}일 ({v/len(regime_df)*100:.1f}%)")

    print(f"\n  ▶ 전략 차단 비율")
    block_a = (~regime_df['allow_a']).sum()
    block_b = (~regime_df['allow_b']).sum()
    print(f"    방패(A) 차단: {block_a}일 ({block_a/len(regime_df)*100:.1f}%)")
    print(f"    창  (B) 차단: {block_b}일 ({block_b/len(regime_df)*100:.1f}%)")
    print("=" * W + "\n")

    # ── VIX 패닉 청산 전략 독립 비교 ─────────────────────────────────────────
    PANIC_CASH = 100_000.0
    etf_cl = {'SPY': spy_df, 'QQQ': qqq_df}
    panic_variants = {
        'C: VIX<20 청산':           {'type': 'vix_lt',       'threshold': 20.0},
        'D: RSI≥70 청산':           {'type': 'rsi_gte',      'threshold': 70.0},
        'E: ATR트레일(3x)':         {'type': 'atr_stop',     'atr_mult': 3.0},
        'F: VIX<20 + MA50확인':     {'type': 'vix_and_ma50', 'vix_threshold': 20.0},
        'G: 분할(50%VIX+ATR나머지)': {'type': 'split',        'vix_threshold': 20.0, 'atr_mult': 3.0},
        'H: VIX<20 또는 +20%':     {'type': 'vix_or_pct',   'vix_threshold': 20.0, 'pct': 0.20},
        'I: 패닉매수 영구보유':       {'type': 'hold_forever'},
    }

    panic_results = {}
    for label, cfg in panic_variants.items():
        log.info(f"VIX 패닉 독립 백테스트: {label}")
        panic_results[label] = run_vix_panic_variant(
            all_dates, regime_df, etf_cl, etf_indicators,
            PANIC_CASH, ['SPY', 'QQQ'], 20.0, cfg,
        )

    # 바이앤홀드 벤치마크: 첫날 SPY+QQQ 50:50 매수, 절대 팔지 않음
    log.info("바이앤홀드 벤치마크 계산 중...")
    bnh_cash   = PANIC_CASH
    bnh_shares = {}
    bnh_hist   = []
    bnh_etf    = ['SPY', 'QQQ']
    for i, date in enumerate(all_dates):
        if i == 0:
            for t in bnh_etf:
                p = _get_price(etf_cl, t, date)
                if p:
                    alloc = bnh_cash / len(bnh_etf) * (1 - TOTAL_COST)
                    bnh_shares[t] = alloc / p
                    bnh_cash -= alloc
        val = bnh_cash + sum(
            bnh_shares.get(t, 0) * (_get_price(etf_cl, t, date) or 0)
            for t in bnh_etf
        )
        bnh_hist.append(val)
    bnh_stats = calc_stats(bnh_hist, PANIC_CASH, [])
    bnh_stats.update({'avg_pnl_pct': 0, 'avg_win_pct': 0,
                      'avg_loss_pct': 0, 'avg_hold_days': len(all_dates)})

    W2 = 110
    print("=" * W2)
    print(f"  🔍 VIX 패닉 매수 청산 전략 비교  (독립 실행, 초기 ${PANIC_CASH:,.0f} / SPY+QQQ 각 20%)".center(W2))
    print(f"  진입 조건 동일: VIX > 30  |  청산 조건만 상이".center(W2))
    print("=" * W2)
    print(
        f"  {'전략':<26} {'최종자산':>12} {'CAGR':>8} {'MDD':>8} "
        f"{'Sharpe':>8} {'거래':>5} {'승률':>7} {'평균수익':>9} {'평균보유':>8}"
    )
    print(f"  {'─'*26} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*5} {'─'*7} {'─'*9} {'─'*8}")
    for label, r in panic_results.items():
        print(
            f"  {label:<26} ${r['final']:>11,.0f} {r['cagr']:>+7.2f}% {r['mdd']:>7.2f}% "
            f"{r['sharpe']:>8.3f} {r['n']:>4}건 {r['wr']:>6.1f}% "
            f"{r['avg_pnl_pct']:>+8.2f}% {r['avg_hold_days']:>6}일"
        )
    # 바이앤홀드 기준선
    r = bnh_stats
    print(
        f"  {'[벤치] SPY+QQQ 그냥보유':<26} ${r['final']:>11,.0f} {r['cagr']:>+7.2f}% {r['mdd']:>7.2f}% "
        f"{r['sharpe']:>8.3f} {'─':>4}  {'─':>6}  {'─':>9} {'전기간':>6}"
    )
    print("=" * W2)

    # C 기준 상대 비교
    base = panic_results.get('C: VIX<20 청산', {})
    if base:
        print(f"\n  ▶ C 대비 성과 차이 (기준: C)")
        print(f"  {'전략':<26} {'CAGR차이':>10} {'MDD차이':>10} {'Sharpe차이':>11} {'평균수익차이':>12} {'평균보유차이':>12}")
        print(f"  {'─'*26} {'─'*10} {'─'*10} {'─'*11} {'─'*12} {'─'*12}")
        for label, r in panic_results.items():
            if label == 'C: VIX<20 청산':
                continue
            dcagr  = r['cagr']           - base['cagr']
            dmdd   = r['mdd']            - base['mdd']
            dsh    = r['sharpe']         - base['sharpe']
            dpnl   = r['avg_pnl_pct']    - base['avg_pnl_pct']
            dhold  = r['avg_hold_days']  - base['avg_hold_days']
            print(
                f"  {label:<26} {dcagr:>+9.2f}% {dmdd:>+9.2f}% "
                f"{dsh:>+10.3f}  {dpnl:>+11.2f}%  {dhold:>+10}일"
            )
    print("=" * W2 + "\n")


if __name__ == '__main__':
    main()
