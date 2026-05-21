"""
전략 A 청산 규칙 비교
  현재 방식: RSI >= 50 → 50% 분할 익절 + MA20 전량 청산
  신규 방식: MA20 도달 시 전량 청산 (분할 없음)
"""
import yfinance as yf
import pandas as pd
import requests
import logging
from datetime import datetime
from io import StringIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

START        = '2015-01-01'
END          = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH = 30_000.0   # 방패 30% 배분만 비교
COMMISSION   = 0.001

A_MAX_POS    = 10
A_POS_PCT    = 0.10
A_RSI_BUY    = 35
A_RSI_PARTIAL= 50
A_ATR_MULT   = 3.0
A_ATR_TIGHT  = 1.5
RSI_PERIOD   = 14
ATR_PERIOD   = 14
QQQ_MA_PERIOD= 200


def get_sp500_tickers():
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("S&P 500 ticker 테이블 없음")


def compute_indicators(df):
    delta = df['Close'].diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    df['RSI']   = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD-1, adjust=False).mean()
                                  / down.ewm(com=RSI_PERIOD-1, adjust=False).mean()))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    prev_close  = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD-1, adjust=False).mean()
    return df


def preprocess(raw, tickers):
    stock_data = {}
    for ticker in tickers:
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker][['Open', 'High', 'Low', 'Close']].copy().dropna()
            if len(df) < 220:
                continue
            df = compute_indicators(df)
            df = df.dropna(subset=['RSI', 'MA20', 'MA200', 'ATR'])
            stock_data[ticker] = df
        except Exception:
            pass
    return stock_data


def calc_stats(equity_series, initial, trades):
    final = equity_series[-1] if equity_series else initial
    ret   = (final - initial) / initial * 100
    years = (pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25
    cagr  = ((final / initial) ** (1 / years) - 1) * 100
    pv    = pd.Series(equity_series)
    mdd   = ((pv - pv.cummax()) / pv.cummax()).min() * 100
    wins  = sum(1 for e, x in trades if x > e)
    wr    = wins / len(trades) * 100 if trades else 0.0
    # Sharpe (일별 수익률 기준, 연환산)
    daily_ret = pv.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * (252 ** 0.5)) if daily_ret.std() > 0 else 0.0
    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd,
                n=len(trades), wins=wins, wr=wr, sharpe=sharpe)


def run(stock_data, bull_series, all_dates, mode: dict):
    """
    mode 예시:
      {'name': '...',  'split_rsi': [50], 'split_pct': [0.5], 'tight_rsi': 50, 'tp_rsi': None, 'tp_ma20': True}
      split_rsi  : 분할 익절 RSI 레벨 리스트
      split_pct  : 각 레벨에서 매도 비율 리스트 (누적이 아닌 잔여 주식 대비)
      tight_rsi  : ATR 타이트닝 시작 RSI (None이면 타이트닝 없음)
      tp_rsi     : RSI 도달 시 전량 청산 (None이면 미사용)
      tp_ma20    : MA20 도달 시 전량 청산 여부
    """
    cash = INITIAL_CASH
    positions = {}
    eq_hist   = []
    trades    = []

    split_levels = mode.get('split_rsi', [])
    split_pcts   = mode.get('split_pct', [])
    tight_rsi    = mode.get('tight_rsi', None)
    tp_rsi       = mode.get('tp_rsi', None)
    tp_ma20      = mode.get('tp_ma20', True)

    for date in all_dates:
        bull = bool(bull_series.get(date, True))

        to_rm = []
        for ticker, pos in list(positions.items()):
            df = stock_data.get(ticker)
            if df is None or date not in df.index:
                continue
            row   = df.loc[date]
            close = float(row['Close'])
            atr   = float(row['ATR'])
            rsi   = float(row['RSI'])
            ma20  = float(row['MA20'])

            # ATR 스톱 갱신
            mult = (A_ATR_TIGHT if (tight_rsi and rsi >= tight_rsi) else A_ATR_MULT)
            new_stop = close - atr * mult
            if new_stop > pos['trailing_stop']:
                pos['trailing_stop'] = new_stop

            # 단계별 분할 익절
            for i, level in enumerate(split_levels):
                key = f'split_{i}_done'
                if rsi >= level and not pos.get(key, False):
                    ratio = split_pcts[i]
                    sell  = pos['shares'] * ratio
                    cash += sell * close * (1 - COMMISSION)
                    trades.append((pos['entry_price'], close))
                    pos['shares'] -= sell
                    pos[key] = True

            # 전량 청산 조건
            full_exit = close <= pos['trailing_stop']
            if tp_rsi  and rsi  >= tp_rsi:  full_exit = True
            if tp_ma20 and close >= ma20:    full_exit = True

            if full_exit:
                cash += pos['shares'] * close * (1 - COMMISSION)
                trades.append((pos['entry_price'], close))
                to_rm.append(ticker)

        for t in to_rm:
            positions.pop(t, None)

        slots = A_MAX_POS - len(positions)
        if slots > 0 and bull:
            eq_now = cash + sum(
                pos['shares'] * float(stock_data[t].loc[date, 'Close'])
                for t, pos in positions.items()
                if t in stock_data and date in stock_data[t].index
            )
            pv = eq_now * A_POS_PCT
            cands = []
            for ticker, df in stock_data.items():
                if ticker in positions or date not in df.index:
                    continue
                row   = df.loc[date]
                close = float(row['Close'])
                rsi   = float(row['RSI'])
                if close > float(row['MA200']) and close < float(row['MA20']) and rsi < A_RSI_BUY:
                    cands.append((rsi, ticker, row))
            cands.sort()
            for _, ticker, row in cands[:slots]:
                if cash < pv:
                    break
                close = float(row['Close'])
                atr   = float(row['ATR'])
                positions[ticker] = {
                    'shares': pv * (1 - COMMISSION) / close,
                    'entry_price': close,
                    'trailing_stop': close - atr * A_ATR_MULT,
                }
                cash -= pv

        eq = cash + sum(
            pos['shares'] * float(stock_data[t].loc[date, 'Close'])
            for t, pos in positions.items()
            if t in stock_data and date in stock_data[t].index
        )
        eq_hist.append(eq)

    return calc_stats(eq_hist, INITIAL_CASH, trades)


# ─── 데이터 다운로드 ──────────────────────────────────────────────────────────
log.info("티커 수집 중...")
sp500_tickers = get_sp500_tickers()
all_tickers   = list(set(sp500_tickers + ['QQQ']))
log.info(f"S&P 500: {len(sp500_tickers)}개")

log.info(f"가격 데이터 다운로드 중... ({START} ~ {END})")
raw = yf.download(all_tickers, start=START, end=END,
                  group_by='ticker', threads=True, progress=True)

log.info("지표 계산 중...")
stock_data = preprocess(raw, sp500_tickers)
log.info(f"유효 종목: {len(stock_data)}개")

all_dates = sorted(set(d for df in stock_data.values() for d in df.index))

try:
    qqq_close   = raw['QQQ'][['Close']].copy().dropna()['Close']
    qqq_ma200   = qqq_close.rolling(QQQ_MA_PERIOD).mean()
    bull_series = (qqq_close > qqq_ma200).reindex(
        pd.DatetimeIndex(all_dates), method='ffill'
    ).fillna(True)
except Exception as e:
    log.warning(f"QQQ 거시 필터 실패: {e}")
    bull_series = pd.Series(True, index=pd.DatetimeIndex(all_dates))

# ─── 비교 대상 정의 ───────────────────────────────────────────────────────────
scenarios = [
    {
        'name':      '① RSI50 분할+MA20  (현재)',
        'split_rsi': [50], 'split_pct': [0.5],
        'tight_rsi': 50,   'tp_rsi': None, 'tp_ma20': True,
    },
    {
        'name':      '② MA20 전량',
        'split_rsi': [], 'split_pct': [],
        'tight_rsi': None, 'tp_rsi': None, 'tp_ma20': True,
    },
    {
        'name':      '③ ATR 스톱만',
        'split_rsi': [], 'split_pct': [],
        'tight_rsi': None, 'tp_rsi': None, 'tp_ma20': False,
    },
    {
        'name':      '④ RSI60 전량',
        'split_rsi': [], 'split_pct': [],
        'tight_rsi': 50, 'tp_rsi': 60, 'tp_ma20': True,
    },
    {
        'name':      '⑤ 3단계 분할(45/55/MA20)',
        'split_rsi': [45, 55], 'split_pct': [0.33, 0.5],  # 잔여 대비: 33%→50%→전량
        'tight_rsi': 45, 'tp_rsi': None, 'tp_ma20': True,
    },
    {
        'name':      '⑥ 이른타이트닝(RSI40)',
        'split_rsi': [50], 'split_pct': [0.5],
        'tight_rsi': 40,  'tp_rsi': None, 'tp_ma20': True,
    },
]

# ─── 실행 ─────────────────────────────────────────────────────────────────────
results = []
for sc in scenarios:
    log.info(f"{sc['name']} 실행 중...")
    s = run(stock_data, bull_series, all_dates, sc)
    results.append((sc['name'], s))

# ─── 결과 출력 ────────────────────────────────────────────────────────────────
W = 88
print("\n" + "=" * W)
print(f"  전략 A 청산 규칙 비교  |  {START[:4]} ~ {END[:4]}  |  초기 $30,000  (S&P500 방패)")
print("=" * W)
print(f"  {'규칙':<26} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'승률':>7} {'트레이드':>8}")
print(f"  {'─'*26} {'─'*7} {'─'*8} {'─'*8} {'─'*7} {'─'*8}")
for name, s in results:
    print(f"  {name:<26} {s['cagr']:>+6.2f}% {s['mdd']:>+7.2f}% {s['sharpe']:>+8.3f} {s['wr']:>+6.1f}% {s['n']:>7,}건")
print("=" * W)
