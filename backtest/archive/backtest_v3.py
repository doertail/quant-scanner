"""
📊 [전략 명세] Dual-Core Momentum & Mean Reversion (v3.1)
================================================================================
본 전략은 시장의 두 가지 핵심 동력을 결합한 하이브리드 시스템입니다.

1. 🛡️ 방패 (Shield): S&P 500 평균 회귀 (Mean Reversion)
   - 목적: 하락장에서의 복원력 및 포트폴리오 안정성 확보
   - 로직: 지수 상단(MA200)에 위치한 종목 중 단기 과매도(RSI < 35) 시 진입
   - 청산: 단기 과열(MA20 터치) 또는 ATR 기반 트레일링 스탑

2. ⚔️ 창 (Spear): Nasdaq 100 상대 강도 모멘텀 (Relative Momentum)
   - 목적: 강세장에서의 초과 수익(Alpha) 극대화
   - 로직: 나스닥 100 중 SPY 대비 6개월 상대 수익률(RS Score)이 양수인 주도주 포착
   - 가변 진입: QQQ/SPY 상대 강도(Regime)에 따라 RSI 진입 장벽 자동 조정 (65 -> 60)
   - 청산: 50일 이동평균선 이탈 또는 ATR 기반 트레일링 스탑

3. ⚖️ 리스크 관리 (Risk Management)
   - Volatility-Adjusted Sizing: 종목별 변동성(ATR)에 따라 투입 비중 자동 조절
   - Risk per Trade: 손절 시 전체 자산의 1% 이내로 손실 제한
   - Regime Switching: QQQ가 200일 이평선 아래인 '약세장'에서는 공격적 진입 제한

4. 🎯 목표 (Target)
   - 지수(QQQM DCA) 대비 압도적인 샤프 지수(Sharpe Ratio) 달성
   - 최대 낙폭(MDD) -20% 이내 방어 및 CAGR 15% 이상 유지
================================================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
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
START        = '2000-01-01'
END          = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH = 100_000.0
COMMISSION   = 0.0005         # 0.05% per side (Alpaca/IBKR 기준)
SLIPPAGE     = 0.0005         # 0.05% slippage (시장가 주문 오차 반영)
TOTAL_COST   = COMMISSION + SLIPPAGE

ALLOC_SHIELD = 0.30           # 방패(S&P 500) 자본 비율
ALLOC_SPEAR  = 0.70           # 창(나스닥 100) 자본 비율

RSI_PERIOD   = 14
ATR_PERIOD   = 14
RISK_FREE_RATE = 0.035        # 무위험 수익률 (3.5%, 미국 국채 기준)

# ── 리스크 관리 파라미터 ───────────────────────────────────────────────────
# 종목당 최대 할당 자본 (Cap)
MAX_CAP_PER_STOCK = 0.15      
# 리스크 타겟팅: 한 종목이 손절될 때 전체 자산의 몇 %를 잃을 것인가?
# 예: 0.01 (1%) 이면 ATR 스탑 도달 시 전체 자산의 1% 손실
RISK_PER_TRADE = 0.01         

# ── 전략 A: S&P 500 방패 (평균 회귀) ─────────────────────────────────────
A_MAX_POS     = 10
A_RSI_BUY     = 35
A_RSI_PARTIAL = 50
A_ATR_MULT    = 3.0
A_ATR_TIGHT   = 1.5

# ── 전략 B: 나스닥 100 창 (순수 모멘텀) ─────────────────────────────────
B_MAX_POS     = 10
B_RSI_BUY     = 65            # 기본 진입 장벽
B_RSI_BUY_AGG = 60            # 성장주 장세(QQQ/SPY 강세) 시 공격적 진입
B_ATR_MULT    = 3.0
RS_LOOKBACK   = 126           # 상대 강도 측정 기간 (6개월, 약 126 거래일)

# ── 전략 B: 청산 규칙 테스트 ───────────────────────────────────────────────
B_EXIT_STRATEGIES = {
    'MA Cross (50d)':       {'type': 'MA_CROSS',  'period': 50},
    'ATR Stop (Benchmark)': {'type': 'ATR_STOP'},
}

QQQ_MA_PERIOD = 200
RS_MA_PERIOD  = 50            # QQQ/SPY 상대강도 이동평균 기간
DCA_NORMAL    = 20.0
DCA_BEAR      = 100.0
# ────────────────────────────────────────────────────────────────────────────


def get_sp500_tickers():
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers)
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    return []

def get_nasdaq100_tickers():
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers=headers)
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    return []

def compute_indicators(df):
    delta = df['Close'].diff()
    up, down = delta.copy(), delta.copy()
    up[up < 0] = 0
    down[down > 0] = 0
    df['RSI'] = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD-1).mean() / down.abs().ewm(com=RSI_PERIOD-1).mean()))
    df['MA20']  = df['Close'].rolling(20).mean()
    df['MA50']  = df['Close'].rolling(50).mean()
    df['MA200'] = df['Close'].rolling(200).mean()
    
    # 상대 수익률 계산을 위한 n일 수익률
    df['Ret_RS'] = df['Close'].pct_change(RS_LOOKBACK)
    
    tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift(1)).abs(), (df['Low']-df['Close'].shift(1)).abs()], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD-1).mean()
    return df

def preprocess(raw, tickers, spy_returns=None):
    stock_data = {}
    for t in tickers:
        try:
            if t not in raw.columns.get_level_values(0): continue
            df = raw[t][['Open', 'High', 'Low', 'Close']].copy().dropna()
            if len(df) < 220: continue
            df = compute_indicators(df)
            
            if spy_returns is not None:
                # SPY 대비 상대 강도 점수 계산
                df['RS_Score'] = df['Ret_RS'] - spy_returns
            
            stock_data[t] = df.dropna()
        except: pass
    return stock_data

def calc_stats(equity_series, initial, trades):
    if not equity_series or len(equity_series) < 2:
        return {k: 0 for k in ['final', 'ret', 'cagr', 'mdd', 'sharpe', 'sortino', 'calmar', 'n', 'wr']}
    
    final = equity_series[-1]
    ret = (final - initial) / initial * 100
    days = len(equity_series)
    years = days / 252.0
    cagr = ((final / initial) ** (1 / years) - 1) * 100
    
    # 일간 수익률 계산
    returns = pd.Series(equity_series).pct_change().dropna()
    avg_ret = returns.mean() * 252
    std_ret = returns.std() * np.sqrt(252)
    
    # Sharpe Ratio
    sharpe = (avg_ret - RISK_FREE_RATE) / std_ret if std_ret > 0 else 0
    
    # Sortino Ratio
    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std() * np.sqrt(252)
    sortino = (avg_ret - RISK_FREE_RATE) / downside_std if downside_std > 0 else 0
    
    # MDD & Calmar
    pv = pd.Series(equity_series)
    drawdowns = (pv - pv.cummax()) / pv.cummax()
    mdd = drawdowns.min() * 100
    calmar = cagr / abs(mdd) if mdd != 0 else 0
    
    wins = sum(1 for e, x in trades if x > e)
    wr = wins / len(trades) * 100 if trades else 0.0
    
    return dict(final=final, ret=ret, cagr=cagr, mdd=mdd, 
                sharpe=sharpe, sortino=sortino, calmar=calmar,
                n=len(trades), wins=wins, wr=wr)

def run_backtest(exit_strategy_b, stock_data_a, stock_data_b, all_dates, bull_series, qqqm_df, regime_series):
    cash_a = INITIAL_CASH * ALLOC_SHIELD
    cash_b = INITIAL_CASH * ALLOC_SPEAR
    positions_a, positions_b = {}, {}
    eq_a_hist, eq_b_hist, eq_c_hist = [], [], []
    trades_a, trades_b = [], []
    qqqm_shares, total_invested_c = 0.0, 0.0

    for i, date in enumerate(all_dates):
        bull = bool(bull_series.get(date, True))
        is_growth_regime = bool(regime_series.get(date, False))
        
        # ── 전략 A: 방패 (S&P 500 MR) ──────────────────────────────────────────
        for t, pos in list(positions_a.items()):
            row = stock_data_a[t].loc[date]
            close, atr, rsi, ma20 = float(row['Close']), float(row['ATR']), float(row['RSI']), float(row['MA20'])
            mult = A_ATR_TIGHT if rsi >= A_RSI_PARTIAL else A_ATR_MULT
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * mult)
            
            if rsi >= A_RSI_PARTIAL and not pos['half_sold']:
                half = pos['shares'] * 0.5
                cash_a += half * close * (1 - TOTAL_COST)
                trades_a.append((pos['entry_price'], close))
                pos['shares'] -= half; pos['half_sold'] = True
                
            if close <= pos['trailing_stop'] or close >= ma20:
                cash_a += pos['shares'] * close * (1 - TOTAL_COST)
                trades_a.append((pos['entry_price'], close))
                del positions_a[t]

        if len(positions_a) < A_MAX_POS and bull:
            eq_a_now = cash_a + sum(p['shares']*float(stock_data_a[t].loc[date,'Close']) for t,p in positions_a.items())
            cands = []
            for t, df in stock_data_a.items():
                if t in positions_a or date not in df.index: continue
                r = df.loc[date]
                if r['Close'] > r['MA200'] and r['Close'] < r['MA20'] and r['RSI'] < A_RSI_BUY:
                    cands.append((r['RSI'], t, r))
            cands.sort()
            for _, t, r in cands[:A_MAX_POS - len(positions_a)]:
                stop_dist = float(r['ATR']) * A_ATR_MULT
                risk_amt = eq_a_now * RISK_PER_TRADE
                shares = risk_amt / stop_dist
                max_shares = (eq_a_now * MAX_CAP_PER_STOCK) / r['Close']
                shares = min(shares, max_shares)
                cost = shares * r['Close'] * (1 + TOTAL_COST)
                if cash_a >= cost:
                    positions_a[t] = {'shares': shares, 'entry_price': r['Close'], 'trailing_stop': r['Close'] - stop_dist, 'half_sold': False}
                    cash_a -= cost

        # ── 전략 B: 창 (NDX 100 Momentum + Relative Strength) ─────────────────────────
        for t, pos in list(positions_b.items()):
            row = stock_data_b[t].loc[date]
            close, atr = float(row['Close']), float(row['ATR'])
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * B_ATR_MULT)
            
            exit_signal = (close <= pos['trailing_stop'])
            if exit_strategy_b['type'] == 'MA_CROSS' and close < row[f"MA{exit_strategy_b['period']}"]:
                exit_signal = True
                
            if exit_signal:
                cash_b += pos['shares'] * close * (1 - TOTAL_COST)
                trades_b.append((pos['entry_price'], close))
                del positions_b[t]

        if len(positions_b) < B_MAX_POS and bull:
            eq_b_now = cash_b + sum(p['shares']*float(stock_data_b[t].loc[date,'Close']) for t,p in positions_b.items())
            cands = []
            # 국면(Regime)에 따른 RSI 진입 기준 조정
            rsi_threshold = B_RSI_BUY_AGG if is_growth_regime else B_RSI_BUY
            
            for t, df in stock_data_b.items():
                if t in positions_b or date not in df.index: continue
                r = df.loc[date]
                # 필터 1: 지수 대비 상대 수익률(RS_Score) > 0 (SPY보다 강한 놈만)
                # 필터 2: 기존 모멘텀 조건 (RSI, MA)
                if r.get('RS_Score', 0) > 0 and r['Close'] > r['MA200'] and r['Close'] > r['MA20'] and r['RSI'] > rsi_threshold:
                    cands.append((r['RS_Score'], t, r)) # RS_Score 높은 순으로 정렬하기 위해 변경
            
            # RS_Score가 높은 순서대로 진입
            cands.sort(key=lambda x: x[0], reverse=True)
            for _, t, r in cands[:B_MAX_POS - len(positions_b)]:
                stop_dist = float(r['ATR']) * B_ATR_MULT
                risk_amt = eq_b_now * RISK_PER_TRADE
                shares = risk_amt / stop_dist
                max_shares = (eq_b_now * MAX_CAP_PER_STOCK) / r['Close']
                shares = min(shares, max_shares)
                cost = shares * r['Close'] * (1 + TOTAL_COST)
                if cash_b >= cost:
                    positions_b[t] = {'shares': shares, 'entry_price': r['Close'], 'trailing_stop': r['Close'] - stop_dist}
                    cash_b -= cost

        # ── 전략 C: QQQM DCA ──────────────────────────────────────────────────
        dca_amt = DCA_NORMAL if bull else DCA_BEAR
        if not qqqm_df.empty and date in qqqm_df.index:
            p = qqqm_df.loc[date, 'Close']
            qqqm_shares += dca_amt * (1 - TOTAL_COST) / p
            total_invested_c += dca_amt
            eq_c_hist.append(qqqm_shares * p)
        else:
            eq_c_hist.append(eq_c_hist[-1] if eq_c_hist else 0.0)

        # ── 일별 기록 ─────────────────────────────────────────────────────────
        eq_a = cash_a + sum(p['shares']*float(stock_data_a[t].loc[date,'Close']) for t,p in positions_a.items() if date in stock_data_a[t].index)
        eq_b = cash_b + sum(p['shares']*float(stock_data_b[t].loc[date,'Close']) for t,p in positions_b.items() if date in stock_data_b[t].index)
        eq_a_hist.append(eq_a); eq_b_hist.append(eq_b)

    return {
        'A': calc_stats(eq_a_hist, INITIAL_CASH * ALLOC_SHIELD, trades_a),
        'B': calc_stats(eq_b_hist, INITIAL_CASH * ALLOC_SPEAR, trades_b),
        'Combined': calc_stats([a+b for a,b in zip(eq_a_hist, eq_b_hist)], INITIAL_CASH, trades_a + trades_b),
        'DCA': {'final': eq_c_hist[-1], 'invested': total_invested_c, 'ret': (eq_c_hist[-1]-total_invested_c)/total_invested_c*100 if total_invested_c>0 else 0}
    }

def main():
    log.info("S&P 500 / Nasdaq 100 티커 및 데이터 수집 중...")
    sp5 = get_sp500_tickers(); ndx = get_nasdaq100_tickers()
    all_t = list(set(sp5 + ndx + ['QQQ', 'QQQM', 'SPY']))
    raw = yf.download(all_t, start=START, end=END, group_by='ticker', threads=True, progress=False)
    
    # SPY 6개월 수익률 계산 (RS_Score 기준점)
    spy_c = raw['SPY']['Close'].dropna()
    spy_ret_rs = spy_c.pct_change(RS_LOOKBACK)
    
    st_a = preprocess(raw, sp5)
    st_b = preprocess(raw, ndx, spy_returns=spy_ret_rs)
    all_dates = sorted(set(d for sd in (st_a, st_b) for df in sd.values() for d in df.index))
    
    qqq_c = raw['QQQ']['Close'].dropna()
    bull_s = (qqq_c > qqq_c.rolling(QQQ_MA_PERIOD).mean()).reindex(all_dates, method='ffill').fillna(True)
    
    # Regime Switching: QQQ/SPY 상대강도 기반
    rs_ratio = (qqq_c / spy_c).dropna()
    regime_s = (rs_ratio > rs_ratio.rolling(RS_MA_PERIOD).mean()).reindex(all_dates, method='ffill').fillna(False)
    
    qqqm_df = raw['QQQM'][['Close']].dropna()

    results = {}
    for name, params in B_EXIT_STRATEGIES.items():
        results[name] = run_backtest(params, st_a, st_b, all_dates, bull_s, qqqm_df, regime_s)

    # ── 결과 출력 ──────────────────────────────────────────────────────────
    W = 105
    header = f"📊 Dual-Core Backtest v3.1 (Relative Strength Filter) | {START} ~ {END}"
    print("\n" + "="*W + f"\n{header:^105}\n" + "="*W)
    
    # 전략 B 비교표
    print(f"{'Exit Strategy (Spear)':<25} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>8} | {'Sortino':>8} | {'Calmar':>8} | {'Win%':>7} | {'Final Asset':>15}")
    print("-" * W)
    for name, res in results.items():
        b = res['B']
        print(f"{name:<25} | {b['cagr']:>7.2f}% | {b['mdd']:>7.2f}% | {b['sharpe']:>8.2f} | {b['sortino']:>8.2f} | {b['calmar']:>8.2f} | {b['wr']:>6.1f}% | ${b['final']:>14,.0f}")
    
    print("\n" + "="*W)
    comb = results['MA Cross (50d)']['Combined']
    print(f"💼 합산 포트폴리오 (Shield + Spear Combined)")
    print(f"   - 최종 자산: ${comb['final']:,.0f} (총 수익률: {comb['ret']:.2f}%)")
    print(f"   - CAGR: {comb['cagr']:.2f}% | MDD: {comb['mdd']:.2f}% | Sharpe: {comb['sharpe']:.2f} | Sortino: {comb['sortino']:.2f}")
    
    dca = results['MA Cross (50d)']['DCA']
    print(f"\n📈 QQQM DCA 결과: 총 투자 ${dca['invested']:,.0f} → 최종 ${dca['final']:,.0f} ({dca['ret']:+.2f}%)")
    print("="*W)

if __name__ == "__main__":
    main()
