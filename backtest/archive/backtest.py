import yfinance as yf
import pandas as pd
import requests
import logging
from datetime import datetime
from io import StringIO

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
START        = '2015-01-01'
END          = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH = 100_000.0
COMMISSION   = 0.001          # 0.1% per side

ALLOC_SHIELD = 0.30           # 방패(S&P 500) 자본 비율
ALLOC_SPEAR  = 0.70           # 창(나스닥 100) 자본 비율

RSI_PERIOD   = 14
ATR_PERIOD   = 14

# ── 전략 A: S&P 500 방패 (평균 회귀) ─────────────────────────────────────
A_MAX_POS     = 10
A_POS_PCT     = 0.10
A_RSI_BUY     = 35            # RSI < 35 진입
A_RSI_PARTIAL = 50            # RSI >= 50 → 50% 분할 익절
A_ATR_MULT    = 3.0           # 기본 트레일링 스톱 배수
A_ATR_TIGHT   = 1.5           # RSI >= 50 이후 조인 배수

# ── 전략 B: 나스닥 100 창 (순수 모멘텀) ─────────────────────────────────
B_MAX_POS     = 10
B_POS_PCT     = 0.10
B_RSI_BUY     = 65            # RSI > 65 진입
B_ATR_MULT    = 3.0           # 트레일링 스톱 (단일)

# ── 거시 필터: QQQ MA200 ─────────────────────────────────────────────────
QQQ_MA_PERIOD = 200           # QQQ 이동평균 기간

# ── 전략 C: QQQM 매일 DCA ────────────────────────────────────────────────
DCA_NORMAL    = 20.0          # 상승장 일일 적립금 ($)
DCA_BEAR      = 100.0         # 하락장 일일 적립금 ($, 5배 증액)
# ────────────────────────────────────────────────────────────────────────────


# ─── 유틸리티 ────────────────────────────────────────────────────────────────
def get_sp500_tickers():
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("S&P 500 ticker 테이블을 찾을 수 없음")


def get_nasdaq100_tickers():
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    resp = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers=headers)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("Nasdaq-100 ticker 테이블을 찾을 수 없음")


def compute_indicators(df):
    delta = df['Close'].diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    df['RSI']   = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
                                  / down.ewm(com=RSI_PERIOD - 1, adjust=False).mean()))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    prev_close  = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()
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
    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd,
                n=len(trades), wins=wins, wr=wr)


# ─── 1. 티커 수집 ───────────────────────────────────────────────────────────
log.info("티커 수집 중...")
sp500_tickers  = get_sp500_tickers()
nasdaq_tickers = get_nasdaq100_tickers()
all_tickers    = list(set(sp500_tickers + nasdaq_tickers + ['QQQM', 'QQQ']))
log.info(f"S&P 500: {len(sp500_tickers)}개  |  나스닥 100: {len(nasdaq_tickers)}개  "
         f"|  합계(중복제거): {len(all_tickers)}개")

# ─── 2. 데이터 다운로드 (일괄) ──────────────────────────────────────────────
log.info(f"가격 데이터 다운로드 중... ({START} ~ {END})")
raw = yf.download(all_tickers, start=START, end=END,
                  group_by='ticker', threads=True, progress=True)

# ─── 3. 전처리 ──────────────────────────────────────────────────────────────
log.info("지표 계산 중...")
stock_data_a = preprocess(raw, sp500_tickers)   # 방패 유니버스
stock_data_b = preprocess(raw, nasdaq_tickers)  # 창 유니버스
log.info(f"방패 유니버스: {len(stock_data_a)}개  |  창 유니버스: {len(stock_data_b)}개")

# 통합 거래일 추출 (bull_series 계산에 필요하므로 먼저 정의)
all_dates = sorted(set(
    d
    for sd in (stock_data_a, stock_data_b)
    for df in sd.values()
    for d in df.index
))

# QQQM DCA용 종가 시리즈 추출 (2020-10 상장 이후만 존재)
try:
    qqqm_df = raw['QQQM'][['Close']].copy().dropna()
    log.info(f"QQQM 데이터: {len(qqqm_df)}일 ({qqqm_df.index[0].date()} ~ {qqqm_df.index[-1].date()})")
except Exception:
    qqqm_df = pd.DataFrame()
    log.warning("QQQM 데이터 없음 — DCA 전략 스킵")

# 거시 필터: QQQ MA200 일별 상승장/하락장 판별 (True = 상승장)
# MA200 미충족 초기 구간은 상승장으로 간주
try:
    qqq_close   = raw['QQQ'][['Close']].copy().dropna()['Close']
    qqq_ma200   = qqq_close.rolling(QQQ_MA_PERIOD).mean()
    bull_series = (qqq_close > qqq_ma200).reindex(
        pd.DatetimeIndex(all_dates), method='ffill'
    ).fillna(True)
    log.info(f"QQQ 거시 필터 계산 완료  |  하락장 구간: {(~bull_series).sum()}일")
except Exception as e:
    log.warning(f"QQQ 거시 필터 실패 (전 기간 상승장 적용): {e}")
    bull_series = pd.Series(True, index=pd.DatetimeIndex(all_dates))
log.info(f"총 거래일: {len(all_dates)}일\n")

# ─── 4. 포트폴리오 초기화 ───────────────────────────────────────────────────
cash_a      = INITIAL_CASH * ALLOC_SHIELD   # $30,000
cash_b      = INITIAL_CASH * ALLOC_SPEAR    # $70,000
positions_a = {}   # {ticker: {shares, entry_price, trailing_stop, half_sold}}
positions_b = {}   # {ticker: {shares, entry_price, trailing_stop}}
eq_a_hist   = []
eq_b_hist   = []
trades_a    = []   # (entry_price, exit_price)
trades_b    = []

# 전략 C: QQQM DCA
qqqm_shares      = 0.0
total_invested_c = 0.0
eq_c_hist        = []

log.info(f"시뮬레이션 시작  |  방패: ${cash_a:,.0f}  |  창: ${cash_b:,.0f}  |  QQQM DCA: 상승장 ${DCA_NORMAL}/일  하락장 ${DCA_BEAR}/일")

# ─── 5. 백테스팅 루프 ───────────────────────────────────────────────────────
for i, date in enumerate(all_dates):
    if i % 500 == 0:
        log.info(f"  {i / len(all_dates) * 100:5.1f}%  ({date.date()})")

    # 거시 필터: 당일 상승장 여부 (QQQ > MA200)
    bull = bool(bull_series.get(date, True))

    # ════════════════════════════════════════════════════════════════════════
    # 전략 A: S&P 500 방패 — 평균 회귀
    # ════════════════════════════════════════════════════════════════════════

    # A-Exit
    to_rm_a = []
    for ticker, pos in list(positions_a.items()):
        df = stock_data_a.get(ticker)
        if df is None or date not in df.index:
            continue
        row   = df.loc[date]
        close = float(row['Close'])
        atr   = float(row['ATR'])
        rsi   = float(row['RSI'])
        ma20  = float(row['MA20'])

        # 적응형 트레일링 스톱 갱신 (RSI >= 50 이후 조임)
        mult = A_ATR_TIGHT if rsi >= A_RSI_PARTIAL else A_ATR_MULT
        new_stop = close - atr * mult
        if new_stop > pos['trailing_stop']:
            pos['trailing_stop'] = new_stop

        # 1단계: RSI >= 50 → 50% 분할 익절
        if rsi >= A_RSI_PARTIAL and not pos['half_sold']:
            half = pos['shares'] * 0.5
            cash_a += half * close * (1 - COMMISSION)
            trades_a.append((pos['entry_price'], close))
            pos['shares']   -= half
            pos['half_sold'] = True

        # 전량 청산: 트레일링 스톱 또는 MA20 도달
        if close <= pos['trailing_stop'] or close >= ma20:
            cash_a += pos['shares'] * close * (1 - COMMISSION)
            trades_a.append((pos['entry_price'], close))
            to_rm_a.append(ticker)

    for t in to_rm_a:
        positions_a.pop(t, None)

    # A-Entry (상승장에서만 신규 진입)
    slots_a = A_MAX_POS - len(positions_a)
    if slots_a > 0 and bull:
        eq_a_now = cash_a + sum(
            pos['shares'] * float(stock_data_a[t].loc[date, 'Close'])
            for t, pos in positions_a.items()
            if t in stock_data_a and date in stock_data_a[t].index
        )
        pv_a = eq_a_now * A_POS_PCT

        cands_a = []
        for ticker, df in stock_data_a.items():
            if ticker in positions_a or date not in df.index:
                continue
            row   = df.loc[date]
            close = float(row['Close'])
            rsi   = float(row['RSI'])
            if (close > float(row['MA200'])
                    and close < float(row['MA20'])
                    and rsi < A_RSI_BUY):
                cands_a.append((rsi, ticker, row))
        cands_a.sort()  # RSI 오름차순 (가장 과매도 우선)

        for _, ticker, row in cands_a[:slots_a]:
            if cash_a < pv_a:
                break
            close = float(row['Close'])
            atr   = float(row['ATR'])
            positions_a[ticker] = {
                'shares':        pv_a * (1 - COMMISSION) / close,
                'entry_price':   close,
                'trailing_stop': close - atr * A_ATR_MULT,
                'half_sold':     False,
            }
            cash_a -= pv_a

    # ════════════════════════════════════════════════════════════════════════
    # 전략 B: 나스닥 100 창 — 순수 모멘텀
    # ════════════════════════════════════════════════════════════════════════

    # B-Exit
    to_rm_b = []
    for ticker, pos in list(positions_b.items()):
        df = stock_data_b.get(ticker)
        if df is None or date not in df.index:
            continue
        row   = df.loc[date]
        close = float(row['Close'])
        atr   = float(row['ATR'])

        new_stop = close - atr * B_ATR_MULT
        if new_stop > pos['trailing_stop']:
            pos['trailing_stop'] = new_stop

        if close <= pos['trailing_stop']:
            cash_b += pos['shares'] * close * (1 - COMMISSION)
            trades_b.append((pos['entry_price'], close))
            to_rm_b.append(ticker)

    for t in to_rm_b:
        positions_b.pop(t, None)

    # B-Entry (상승장에서만 신규 진입)
    slots_b = B_MAX_POS - len(positions_b)
    if slots_b > 0 and bull:
        eq_b_now = cash_b + sum(
            pos['shares'] * float(stock_data_b[t].loc[date, 'Close'])
            for t, pos in positions_b.items()
            if t in stock_data_b and date in stock_data_b[t].index
        )
        pv_b = eq_b_now * B_POS_PCT

        cands_b = []
        for ticker, df in stock_data_b.items():
            if ticker in positions_b or date not in df.index:
                continue
            row   = df.loc[date]
            close = float(row['Close'])
            rsi   = float(row['RSI'])
            if (close > float(row['MA200'])
                    and close > float(row['MA20'])
                    and rsi > B_RSI_BUY):
                cands_b.append((rsi, ticker, row))
        cands_b.sort(reverse=True)  # RSI 내림차순 (모멘텀 강한 순)

        for _, ticker, row in cands_b[:slots_b]:
            if cash_b < pv_b:
                break
            close = float(row['Close'])
            atr   = float(row['ATR'])
            positions_b[ticker] = {
                'shares':        pv_b * (1 - COMMISSION) / close,
                'entry_price':   close,
                'trailing_stop': close - atr * B_ATR_MULT,
            }
            cash_b -= pv_b

    # ════════════════════════════════════════════════════════════════════════
    # 전략 C: QQQM DCA (상승장 $20 / 하락장 $100)
    # ════════════════════════════════════════════════════════════════════════
    dca_today = DCA_NORMAL if bull else DCA_BEAR
    if not qqqm_df.empty and date in qqqm_df.index:
        close_qqqm    = float(qqqm_df.loc[date, 'Close'])
        qqqm_shares  += dca_today * (1 - COMMISSION) / close_qqqm
        total_invested_c += dca_today
        eq_c_hist.append(qqqm_shares * close_qqqm)
    elif eq_c_hist:
        eq_c_hist.append(eq_c_hist[-1])   # 데이터 없는 날은 전일 평가액 유지
    else:
        eq_c_hist.append(0.0)

    # ── 일별 자산 기록 ─────────────────────────────────────────────────
    eq_a = cash_a + sum(
        pos['shares'] * float(stock_data_a[t].loc[date, 'Close'])
        for t, pos in positions_a.items()
        if t in stock_data_a and date in stock_data_a[t].index
    )
    eq_b = cash_b + sum(
        pos['shares'] * float(stock_data_b[t].loc[date, 'Close'])
        for t, pos in positions_b.items()
        if t in stock_data_b and date in stock_data_b[t].index
    )
    eq_a_hist.append(eq_a)
    eq_b_hist.append(eq_b)

# ─── 6. 성과 집계 ───────────────────────────────────────────────────────────
sa = calc_stats(eq_a_hist, INITIAL_CASH * ALLOC_SHIELD, trades_a)
sb = calc_stats(eq_b_hist, INITIAL_CASH * ALLOC_SPEAR,  trades_b)
sc = calc_stats(
    [a + b for a, b in zip(eq_a_hist, eq_b_hist)],
    INITIAL_CASH,
    trades_a + trades_b,
)

# 전략 C: DCA 수익률 (총 투자금 대비)
c_final = eq_c_hist[-1] if eq_c_hist else 0.0
c_ret   = (c_final - total_invested_c) / total_invested_c * 100 if total_invested_c > 0 else 0.0
pv_c    = pd.Series(eq_c_hist)
c_mdd   = ((pv_c - pv_c.cummax()) / pv_c.cummax()).min() * 100 if not pv_c.empty else 0.0

# ─── 7. 결과 출력 ───────────────────────────────────────────────────────────
W = 66
print("\n" + "=" * W)
print(f"  📊 Dual-Core + DCA Backtest  |  {START[:4]} ~ {END[:4]}")
print(f"  🛡 방패 {int(ALLOC_SHIELD*100)}% S&P500 MR  +  ⚔ 창 {int(ALLOC_SPEAR*100)}% NDX100 MOM  +  📈 QQQM DCA")
print("=" * W)

rows = [
    ("🛡  방패  S&P 500 MR",   INITIAL_CASH * ALLOC_SHIELD, sa),
    ("⚔  창   NDX100 MOM",    INITIAL_CASH * ALLOC_SPEAR,  sb),
    ("💼 듀얼코어 합산",        INITIAL_CASH,                sc),
]

for label, init, s in rows:
    print(f"\n  {label}")
    print(f"    초기 자본  : ${init:>10,.0f}   →   최종 자산  : ${s['final']:>12,.0f}")
    print(f"    총 수익률  : {s['ret']:>+9.2f} %       CAGR       : {s['cagr']:>+8.2f} %")
    print(f"    MDD        : {s['mdd']:>+9.2f} %       승률       : {s['wr']:>6.1f} %"
          f"  ({s['wins']}/{s['n']})")

bear_days = int((~bull_series).sum())
print(f"\n  📈 QQQM DCA  (상승장 ${DCA_NORMAL}/일  |  하락장 ${DCA_BEAR}/일)")
print(f"    총 투자금  : ${total_invested_c:>10,.0f}   →   최종 자산  : ${c_final:>12,.0f}")
print(f"    총 수익률  : {c_ret:>+9.2f} %       MDD        : {c_mdd:>+8.2f} %")

print("\n" + "=" * W)
print(f"  거시 필터  : QQQ > MA{QQQ_MA_PERIOD} → 상승장  |  하락장 구간: {bear_days}일")
print(f"  진입 — 방패: RSI<{A_RSI_BUY} + Close<MA20 + Close>MA200  (상승장만)")
print(f"  청산 — 방패: RSI≥{A_RSI_PARTIAL}(50%↓) → MA20(나머지) | ATR×{A_ATR_MULT}→×{A_ATR_TIGHT} 적응 스톱")
print(f"  진입 — 창  : RSI>{B_RSI_BUY} + Close>MA20 + Close>MA200  (상승장만)")
print(f"  청산 — 창  : ATR×{B_ATR_MULT} 트레일링 스톱 (추세 추종)")
print(f"  DCA       : QQQM 상승장 ${DCA_NORMAL}/일  하락장 ${DCA_BEAR}/일, 매도 없음")
print("=" * W)
