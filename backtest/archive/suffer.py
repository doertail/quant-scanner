import yfinance as yf
import pandas as pd
import requests
from datetime import datetime
from io import StringIO

# ─── CONFIG ────────────────────────────────────────────────────────────────
START          = '2015-01-01'
END            = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH   = 100_000.0
COMMISSION     = 0.001        # 0.1% per side
MAX_POSITIONS  = 10
POSITION_PCT   = 0.10         # 총자산의 10% per slot
RSI_PERIOD     = 14
RSI_BUY        = 65   # 모멘텀 진입 기준 (RSI > 이 값)
ATR_PERIOD     = 14
ATR_MULT       = 3.0          # Trailing Stop = Close - ATR * 3
# ───────────────────────────────────────────────────────────────────────────

def get_nasdaq100_tickers():
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    resp = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers=headers)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("Nasdaq-100 ticker 테이블을 찾을 수 없음")

def compute_indicators(df):
    """RSI(14), MA20, MA200, ATR(14) 계산"""
    delta = df['Close'].diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    df['RSI']   = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
                                  / down.ewm(com=RSI_PERIOD - 1, adjust=False).mean()))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA200'] = df['Close'].rolling(200).mean()

    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()
    return df

# ─── 1. 데이터 수집 ─────────────────────────────────────────────────────────
print("📋 Nasdaq-100 티커 수집 중...")
tickers = get_nasdaq100_tickers()
print(f"   → {len(tickers)}개 티커\n")

print(f"📥 가격 데이터 다운로드 중 ({START} ~ {END})...")
raw = yf.download(tickers, start=START, end=END, group_by='ticker',
                  threads=True, progress=True)
print()

# ─── 2. 전처리 (지표 계산, dict 적재) ─────────────────────────────────────
print("⚙️  지표 계산 중...")
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

print(f"   → {len(stock_data)}개 종목 준비 완료")

# 통합 거래일 추출
all_dates = sorted(set(d for df in stock_data.values() for d in df.index))
print(f"   → {len(all_dates)} 거래일\n")

# ─── 3. 포트폴리오 백테스팅 ─────────────────────────────────────────────────
# 상태
cash         = INITIAL_CASH
positions    = {}   # {ticker: {shares, entry_price, trailing_stop, tp1_hit}}
daily_equity = []
trades       = []   # (entry_price, exit_price)

print(f"🔄 시뮬레이션 실행 중...")

for i, date in enumerate(all_dates):
    if i % 500 == 0:
        pct = i / len(all_dates) * 100
        print(f"   {pct:5.1f}%  ({date.date()})")

    # ── Step A. 청산 (Exit Check) ────────────────────────────────────────
    to_remove = []
    for ticker, pos in list(positions.items()):
        df = stock_data.get(ticker)
        if df is None or date not in df.index:
            continue
        row   = df.loc[date]
        close = float(row['Close'])
        atr   = float(row['ATR'])

        # 방어선 상향 갱신 (ATR 기반, 후퇴 불가)
        new_stop = close - atr * ATR_MULT
        if new_stop > pos['trailing_stop']:
            pos['trailing_stop'] = new_stop

        # 방어선 붕괴 → 전량 매도
        if close <= pos['trailing_stop']:
            cash += pos['shares'] * close * (1 - COMMISSION)
            trades.append((pos['entry_price'], close))
            to_remove.append(ticker)

    for ticker in to_remove:
        positions.pop(ticker, None)

    # ── Step B. 진입 (Entry Check) ──────────────────────────────────────
    slots = MAX_POSITIONS - len(positions)

    if slots > 0:
        # 포지션 사이징 기준: 현재 equity의 10%
        equity_now = cash + sum(
            pos['shares'] * float(stock_data[t].loc[date, 'Close'])
            for t, pos in positions.items()
            if t in stock_data and date in stock_data[t].index
        )
        position_value = equity_now * POSITION_PCT

        # 진입 후보: 조건 충족 종목을 RSI 오름차순 정렬
        candidates = []
        for ticker, df in stock_data.items():
            if ticker in positions or date not in df.index:
                continue
            row   = df.loc[date]
            close = float(row['Close'])
            ma20  = float(row['MA20'])
            ma200 = float(row['MA200'])
            rsi   = float(row['RSI'])
            if close > ma200 and close > ma20 and rsi > RSI_BUY:
                candidates.append((rsi, ticker, row))
        candidates.sort(reverse=True)  # RSI 내림차순 (모멘텀 강한 순)

        for _, ticker, row in candidates[:slots]:
            if cash < position_value:
                break
            close = float(row['Close'])
            atr   = float(row['ATR'])
            shares = position_value * (1 - COMMISSION) / close
            positions[ticker] = {
                'shares':        shares,
                'entry_price':   close,
                'trailing_stop': close - atr * ATR_MULT,
            }
            cash -= position_value

    # ── Step C. 일별 자산 기록 ─────────────────────────────────────────
    equity = cash + sum(
        pos['shares'] * float(stock_data[t].loc[date, 'Close'])
        for t, pos in positions.items()
        if t in stock_data and date in stock_data[t].index
    )
    daily_equity.append(equity)

# ─── 4. 성과 지표 계산 ──────────────────────────────────────────────────────
final_equity = daily_equity[-1] if daily_equity else INITIAL_CASH
total_return = (final_equity - INITIAL_CASH) / INITIAL_CASH * 100

years = (pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25
cagr  = ((final_equity / INITIAL_CASH) ** (1 / years) - 1) * 100

pv  = pd.Series(daily_equity)
mdd = ((pv - pv.cummax()) / pv.cummax()).min() * 100

wins     = sum(1 for e, x in trades if x > e)
win_rate = wins / len(trades) * 100 if trades else 0.0

# ─── 5. 결과 출력 ───────────────────────────────────────────────────────────
print("\n" + "=" * 54)
print(f"  📊 Portfolio Backtest  |  Nasdaq-100  |  {START[:4]}~{END[:4]}")
print(f"  슬롯 {MAX_POSITIONS}개  |  종목당 {int(POSITION_PCT*100)}%  |  ATR({ATR_PERIOD}) x {ATR_MULT} 방어선")
print("=" * 54)
print(f"  초기 자본              : ${INITIAL_CASH:>12,.0f}")
print(f"  최종 자산              : ${final_equity:>12,.0f}")
print(f"  총 누적 수익률         : {total_return:>+12.2f} %")
print(f"  연평균 수익률 (CAGR)   : {cagr:>+12.2f} %")
print(f"  최대 낙폭 (MDD)        : {mdd:>+12.2f} %")
print(f"  총 매매 횟수           : {len(trades):>12} 회")
print(f"  승률                   : {win_rate:>11.1f} %  ({wins}/{len(trades)})")
print("=" * 54)
