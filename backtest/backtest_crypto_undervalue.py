"""
backtest_crypto_undervalue.py
─────────────────────────────────────────────────────────────────────────────
저평가 지표 기반 코인 모멘텀 전략 백테스트

저평가 지표 5개 → 점수제 필터로 진입 게이트 제어:
  [1] Fear & Greed < 45         (공포 구간)
  [2] ETH/BTC 비율 하위 35%     (ETH 상대 저평가, 252일 롤링)
  [3] ETH 주봉 RSI < 50         (모멘텀 아직 회복 전)
  [4] MVRV 프록시 (ETH/2Y MA) < 1.5  (실현가 대비 저평가)
  [5] ETH > MA50                (단기 추세 반전 확인)

비교:
  A. 점수 ≥ 3 (완화)
  B. 점수 ≥ 4 (강화)
  C. ETH>MA50만 (이전 기준선)
  D. BTC>MA50만 (이전 BTC 기준선)
  [기준] ETH B&H / BTC B&H / QQQM B&H
─────────────────────────────────────────────────────────────────────────────
"""

import logging, time
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
START          = '2018-02-01'   # Fear & Greed 시작일 (2018-02-01)
END            = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH   = 100_000.0
COMMISSION     = 0.001
SLIPPAGE       = 0.001
TOTAL_COST     = COMMISSION + SLIPPAGE
RISK_FREE_RATE = 0.035
RISK_PER_TRADE = 0.015
MAX_CAP        = 0.25
MAX_POS        = 4
MOM_LONG       = 126
MOM_SHORT      = 63
ATR_MULT       = 3.0
VIX_MAX        = 30.0
RSI_PERIOD     = 14
ATR_PERIOD     = 14

CRYPTO_TICKERS = ['MSTR', 'BLOK', 'MARA', 'RIOT', 'COIN', 'BITO']

# 저평가 지표 임계값
FNG_FEAR_THRESHOLD   = 45       # Fear & Greed < 45
ETHBTC_PCTILE        = 35.0     # ETH/BTC 하위 35% (롤링 252일)
WEEKLY_RSI_THRESHOLD = 50.0     # 주봉 RSI < 50
MVRV_THRESHOLD       = 1.5      # MVRV 프록시 < 1.5
ETH_MA_PERIOD        = 50       # ETH MA50


# ─── Fear & Greed 히스토리 ────────────────────────────────────────────────────

def fetch_fear_greed() -> pd.Series:
    """alternative.me에서 전체 히스토리 당겨오기"""
    log.info("Fear & Greed Index 히스토리 다운로드 중...")
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.alternative.me/fng/?limit=0&format=json",
                timeout=15,
            )
            data = resp.json()['data']
            records = {
                pd.Timestamp(int(d['timestamp']), unit='s').normalize(): int(d['value'])
                for d in data
            }
            s = pd.Series(records).sort_index()
            log.info(f"  Fear & Greed: {len(s)}일  ({s.index[0].date()} ~ {s.index[-1].date()})")
            return s
        except Exception as e:
            log.warning(f"  시도 {attempt+1} 실패: {e}")
            time.sleep(2)
    log.error("Fear & Greed 다운로드 실패 — 50 고정으로 대체")
    return pd.Series(dtype=float)


# ─── 지표 계산 ───────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    delta     = df['Close'].diff()
    up        = delta.clip(lower=0)
    down      = -delta.clip(upper=0)
    df['RSI'] = 100 - (100 / (
        1 + up.ewm(com=RSI_PERIOD-1, adjust=False).mean()
          / down.ewm(com=RSI_PERIOD-1, adjust=False).mean()
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
    df['ATR'] = tr.ewm(com=ATR_PERIOD-1, adjust=False).mean()
    return df


def download_single(ticker, start, end, min_bars=60):
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, multi_level_index=False)
        if df is None or len(df) < min_bars:
            return None
        df = df[['Open','High','Low','Close','Volume']].dropna(subset=['Close'])
        return compute_indicators(df)
    except Exception as e:
        log.warning(f"{ticker}: {e}")
        return None


def build_signal_df(
    eth_df:  pd.DataFrame,
    btc_df:  pd.DataFrame,
    fng_s:   pd.Series,
    all_dates,
) -> pd.DataFrame:
    """
    날짜별 저평가 점수 (0~5) + 개별 신호 계산
    """
    dates = pd.DatetimeIndex(all_dates)

    def fill(s): return s.reindex(dates, method='ffill')

    eth_cl  = fill(eth_df['Close'])
    btc_cl  = fill(btc_df['Close'].reindex(eth_df.index, method='ffill'))
    eth_ma50  = fill(eth_df['Close'].rolling(ETH_MA_PERIOD).mean())
    eth_ma2y  = fill(eth_df['Close'].rolling(504).mean())   # 2년 MA

    # [1] Fear & Greed
    fng_filled = fill(fng_s) if not fng_s.empty else pd.Series(50, index=dates)
    sig1 = (fng_filled < FNG_FEAR_THRESHOLD).astype(int)

    # [2] ETH/BTC 하위 35% (252일 롤링 백분위)
    eth_btc = eth_cl / btc_cl.replace(0, np.nan)
    sig2 = eth_btc.rolling(252).rank(pct=True).apply(
        lambda x: 1 if (not np.isnan(x) and x <= ETHBTC_PCTILE / 100) else 0
    )

    # [3] ETH 주봉 RSI < 50
    eth_weekly_cl = eth_df['Close'].resample('W').last().dropna()
    delta = eth_weekly_cl.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    weekly_rsi = 100 - 100 / (
        1 + up.ewm(com=RSI_PERIOD-1, adjust=False).mean()
          / down.ewm(com=RSI_PERIOD-1, adjust=False).mean()
    )
    weekly_rsi_daily = weekly_rsi.reindex(dates, method='ffill')
    sig3 = (weekly_rsi_daily < WEEKLY_RSI_THRESHOLD).astype(int)

    # [4] MVRV 프록시 < 1.5
    mvrv = eth_cl / eth_ma2y.replace(0, np.nan)
    sig4 = (mvrv < MVRV_THRESHOLD).astype(int)

    # [5] ETH > MA50
    sig5 = (eth_cl > eth_ma50).astype(int)

    score = sig1 + sig2 + sig3 + sig4 + sig5

    return pd.DataFrame({
        'fng':         fng_filled,
        'sig1_fng':    sig1,
        'sig2_ethbtc': sig2,
        'sig3_rsi':    sig3,
        'sig4_mvrv':   sig4,
        'sig5_ma50':   sig5,
        'score':       score,
        'eth_ma50':    sig5,   # ETH>MA50만 (기준선 비교용)
    })


# ─── 통계 ────────────────────────────────────────────────────────────────────

def calc_stats(equity, initial, trades):
    if len(equity) < 2:
        return {k:0 for k in ['final','ret','cagr','mdd','sharpe','sortino','calmar','n','wins','wr']}
    final = equity[-1]; ret = (final-initial)/initial*100
    years = len(equity)/252; cagr = ((final/initial)**(1/years)-1)*100 if years>0 else 0
    rets  = pd.Series(equity).pct_change().dropna()
    avg_r = rets.mean()*252; std_r = rets.std()*np.sqrt(252)
    sharpe = (avg_r-RISK_FREE_RATE)/std_r if std_r>0 else 0
    dn_r   = rets[rets<0].std()*np.sqrt(252)
    sortino= (avg_r-RISK_FREE_RATE)/dn_r if dn_r>0 else 0
    pv = pd.Series(equity); mdd = ((pv-pv.cummax())/pv.cummax()).min()*100
    calmar = cagr/abs(mdd) if mdd!=0 else 0
    wins = sum(1 for t in trades if t[1]>t[0])
    wr   = wins/len(trades)*100 if trades else 0.0
    return dict(final=final,ret=ret,cagr=cagr,mdd=mdd,
                sharpe=sharpe,sortino=sortino,calmar=calmar,
                n=len(trades),wins=wins,wr=wr)


def trade_stats(trades):
    if not trades:
        return {'n':0,'wr':0,'avg_pnl':0,'avg_win':0,'avg_loss':0,'avg_hold':0}
    wins  = [t for t in trades if t[1]>t[0]]
    pnls  = [(t[1]-t[0])/t[0]*100 for t in trades]
    wp    = [(t[1]-t[0])/t[0]*100 for t in trades if t[1]>t[0]]
    lp    = [(t[1]-t[0])/t[0]*100 for t in trades if t[1]<=t[0]]
    holds = [(t[3]-t[2]).days for t in trades]
    return {
        'n':         len(trades),
        'wr':        len(wins)/len(trades)*100,
        'avg_pnl':   sum(pnls)/len(pnls) if pnls else 0,
        'avg_win':   sum(wp)/len(wp)     if wp   else 0,
        'avg_loss':  sum(lp)/len(lp)     if lp   else 0,
        'avg_hold':  int(sum(holds)/len(holds)) if holds else 0,
    }


def _price(data, ticker, date):
    df = data.get(ticker)
    if df is None: return None
    try:
        sub = df.loc[:date]
        return float(sub.iloc[-1]['Close']) if not sub.empty else None
    except: return None


# ─── 백테스트 ────────────────────────────────────────────────────────────────

def run(
    crypto_data:  dict,
    signal_df:    pd.DataFrame,
    rank_df:      pd.DataFrame,
    rs_df:        pd.DataFrame,
    all_dates:    list,
    allow_col:    str,       # 'eth_ma50' | 'score_3' | 'score_4'
    min_score:    int = 0,   # score 기반일 때 최소 점수
) -> dict:
    cash = INITIAL_CASH
    positions = {}
    eq_hist = []
    trades  = []

    for date in all_dates:
        # 진입 허용 여부 판단
        allow = False
        if date in signal_df.index:
            row = signal_df.loc[date]
            if allow_col == 'eth_ma50':
                allow = bool(row['eth_ma50'])
            else:  # score 기반
                allow = int(row['score']) >= min_score

        total_eq = cash + sum(
            p['shares']*(_price(crypto_data,t,date) or p['entry_price'])
            for t,p in positions.items()
        )

        # 청산
        for t in list(positions.keys()):
            df = crypto_data.get(t)
            if df is None: continue
            try: row2 = df.loc[:date].iloc[-1]
            except: continue
            close = float(row2['Close']); atr = float(row2['ATR']); ma50 = float(row2['MA50'])
            pos = positions[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr*ATR_MULT)
            if close <= pos['trailing_stop'] or close < ma50:
                cash += pos['shares']*close*(1-TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions[t]

        # 진입
        if allow and len(positions) < MAX_POS:
            cands = []
            for t, df in crypto_data.items():
                if t in positions: continue
                try: r = df.loc[:date].iloc[-1]
                except: continue
                cl, ma20 = float(r['Close']), float(r['MA20'])
                if cl <= ma20: continue
                try:
                    rank = float(rank_df.at[date,t]) if (date in rank_df.index and t in rank_df.columns) else float('nan')
                    rs   = float(rs_df.at[date,t])   if (date in rs_df.index   and t in rs_df.columns)   else float('nan')
                except: continue
                if np.isnan(rank) or np.isnan(rs): continue
                if rank >= 0.5 and rs > 0:
                    cands.append((rank, t, r))

            cands.sort(reverse=True)
            for _, t, r in cands[:MAX_POS-len(positions)]:
                stop_dist = float(r['ATR'])*ATR_MULT
                if stop_dist <= 0: continue
                shares = min(total_eq*RISK_PER_TRADE/stop_dist, (total_eq*MAX_CAP)/float(r['Close']))
                cost   = shares*float(r['Close'])*(1+TOTAL_COST)
                if cash >= cost > 0:
                    positions[t] = {
                        'shares':        shares,
                        'entry_price':   float(r['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(r['Close'])-stop_dist,
                    }
                    cash -= cost

        eq = cash + sum(
            p['shares']*(_price(crypto_data,t,date) or p['entry_price'])
            for t,p in positions.items()
        )
        eq_hist.append(eq)

    stats = calc_stats(eq_hist, INITIAL_CASH, [(t[0],t[1]) for t in trades])
    stats.update(trade_stats(trades))
    return stats


def bh(df, dates, initial=INITIAL_CASH):
    shares=0.0; hist=[]
    for d in dates:
        try:
            sub=df.loc[:d]; p=float(sub.iloc[-1]['Close']) if not sub.empty else None
        except: p=None
        if p is None:
            hist.append(hist[-1] if hist else initial); continue
        if shares==0.0: shares=initial*(1-TOTAL_COST)/p
        hist.append(shares*p)
    return calc_stats(hist, initial, [])


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    # 데이터 다운로드
    log.info(f"코인 데이터 다운로드 중... ({START} ~ {END})")
    crypto_data = {}
    for t in CRYPTO_TICKERS:
        df = download_single(t, START, END)
        if df is not None:
            crypto_data[t] = df
            log.info(f"  {t:<6}: {len(df)}일")
        else:
            log.warning(f"  {t}: 없음")

    eth_df  = download_single('ETH-USD', START, END)
    btc_df  = download_single('BTC-USD', START, END)
    qqqm_df = download_single('QQQM',    START, END)
    log.info(f"ETH-USD: {len(eth_df)}일  BTC-USD: {len(btc_df)}일")

    fng_s = fetch_fear_greed()

    # 공통 날짜
    stock_dates = sorted(set(d for df in crypto_data.values() for d in df.index))
    log.info(f"백테스트 기간: {stock_dates[0].date()} ~ {stock_dates[-1].date()} ({len(stock_dates)}거래일)")

    # 모멘텀 랭킹 (ETH 기준)
    log.info("모멘텀 랭킹 계산 중...")
    close_df    = pd.DataFrame({t: df['Close'] for t, df in crypto_data.items()})
    rank_df     = close_df.pct_change(MOM_LONG).rank(axis=1, pct=True)
    ret_3m      = close_df.pct_change(MOM_SHORT)
    eth_ret_3m  = eth_df['Close'].pct_change(MOM_SHORT)
    rs_df       = ret_3m.subtract(eth_ret_3m, axis=0)

    # 신호 계산
    log.info("저평가 지표 시리즈 계산 중...")
    signal_df = build_signal_df(eth_df, btc_df, fng_s, stock_dates)

    # 신호 분포 출력
    sc = signal_df['score']
    log.info(f"점수 분포: {dict(sc.value_counts().sort_index())}")
    log.info(f"점수≥3 허용일: {(sc>=3).sum()}일  점수≥4: {(sc>=4).sum()}일  ETH>MA50: {signal_df['eth_ma50'].sum()}일")

    # 백테스트
    log.info("[A] 점수 ≥ 3 백테스트...")
    r_s3 = run(crypto_data, signal_df, rank_df, rs_df, stock_dates, 'score', min_score=3)

    log.info("[B] 점수 ≥ 4 백테스트...")
    r_s4 = run(crypto_data, signal_df, rank_df, rs_df, stock_dates, 'score', min_score=4)

    log.info("[C] ETH>MA50만 (기준선) 백테스트...")
    r_eth = run(crypto_data, signal_df, rank_df, rs_df, stock_dates, 'eth_ma50')

    eth_bh  = bh(eth_df,  stock_dates)
    btc_bh  = bh(btc_df,  stock_dates)
    qqqm_bh = bh(qqqm_df, stock_dates) if qqqm_df is not None else {'cagr':0,'mdd':0,'sharpe':0,'sortino':0,'n':0,'wr':0}

    # ── 출력 ────────────────────────────────────────────────────────────────
    W     = 108
    years = len(stock_dates)/252.0

    print("\n" + "="*W)
    print(f"  저평가 지표 기반 코인 모멘텀 백테스트  {START} ~ {END}  ({years:.1f}년  {len(stock_dates)}거래일)".center(W))
    print("="*W)

    # 신호 분포
    print(f"\n  ─── 저평가 지표 발동 현황 ({len(stock_dates)}거래일 기준) ───")
    labels = [
        ('sig1_fng',    f'[1] Fear & Greed < {FNG_FEAR_THRESHOLD}'),
        ('sig2_ethbtc', f'[2] ETH/BTC 하위 {ETHBTC_PCTILE:.0f}% (롤링 252일)'),
        ('sig3_rsi',    f'[3] ETH 주봉 RSI < {WEEKLY_RSI_THRESHOLD:.0f}'),
        ('sig4_mvrv',   f'[4] MVRV 프록시 < {MVRV_THRESHOLD:.1f}'),
        ('sig5_ma50',   f'[5] ETH > MA{ETH_MA_PERIOD}'),
    ]
    for col, label in labels:
        n = int(signal_df[col].sum())
        print(f"    {label:<35}: {n:>5}일 ({n/len(stock_dates)*100:>5.1f}%)")

    sc = signal_df['score']
    print(f"\n  점수별 허용일:")
    for s in range(6):
        n = int((sc == s).sum())
        c = int((sc >= s).sum())
        bar = '█' * (n // 50)
        print(f"    점수={s}: {n:>5}일 ({n/len(sc)*100:>4.1f}%)  {bar}   (≥{s}이면 {c}일 허용)")

    # 현재 신호
    last = signal_df.iloc[-1]
    print(f"\n  현재 신호 ({signal_df.index[-1].date()}):")
    for col, label in labels:
        v = '✅' if int(last[col]) == 1 else '❌'
        print(f"    {v} {label}")
    print(f"    → 현재 점수: {int(last['score'])}/5")

    # 성과 비교
    print(f"\n  ─── 성과 비교 ───")
    print(f"  {'':40} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'Sortino':>7} {'거래':>5} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
    print(f"  {'─'*40} {'─'*7} {'─'*8} {'─'*8} {'─'*7} {'─'*5} {'─'*6} {'─'*9} {'─'*7}")

    def row(label, r):
        n=r.get('n',0); wr=r.get('wr',0); pnl=r.get('avg_pnl',0); hold=r.get('avg_hold',0)
        print(
            f"  {label:<40} {r['cagr']:>+6.2f}% {r['mdd']:>7.2f}% "
            f"{r['sharpe']:>8.3f} {r['sortino']:>7.3f} "
            f"{n:>4}건 {wr:>5.1f}% {pnl:>+8.2f}% {hold:>5}일"
        )

    allow_s3   = int((signal_df['score']>=3).sum())
    allow_s4   = int((signal_df['score']>=4).sum())
    allow_eth  = int(signal_df['eth_ma50'].sum())
    row(f"A. 점수≥3  (허용 {allow_s3}일, {allow_s3/len(stock_dates)*100:.0f}%)", r_s3)
    row(f"B. 점수≥4  (허용 {allow_s4}일, {allow_s4/len(stock_dates)*100:.0f}%)", r_s4)
    row(f"C. ETH>MA50만 (허용 {allow_eth}일, {allow_eth/len(stock_dates)*100:.0f}%)", r_eth)
    row("[기준] ETH-USD Buy & Hold",  eth_bh)
    row("[기준] BTC-USD Buy & Hold",  btc_bh)
    row("[기준] QQQM Buy & Hold",     qqqm_bh)

    # 비교
    print(f"\n  ─── ETH>MA50 기준선 대비 차이 ───")
    for label, r in [("A(점수≥3)", r_s3), ("B(점수≥4)", r_s4)]:
        dc=r['cagr']-r_eth['cagr']; ds=r['sharpe']-r_eth['sharpe']; dm=r['mdd']-r_eth['mdd']
        print(f"    {label}: CAGR {dc:>+.2f}%  MDD {dm:>+.2f}%  Sharpe {ds:>+.3f}")

    # 종합 판단
    print(f"\n{'='*W}")
    print(f"  📋 종합 판단 및 scanner_v4 반영 여부")
    print(f"{'='*W}")

    best = max([r_s3, r_s4, r_eth], key=lambda r: r['sharpe'])
    best_label = {id(r_s3):"A(점수≥3)", id(r_s4):"B(점수≥4)", id(r_eth):"C(ETH>MA50)"}[id(best)]

    print(f"  최고 Sharpe: {best_label}  ({best['sharpe']:.3f})")

    if best['sharpe'] > r_eth['sharpe'] and best['cagr'] > r_eth['cagr']:
        verdict = f"✅ 저평가 지표 추가 효과 있음 → scanner_v4에 전략 D 추가 권장"
        best_config = best_label
    elif best['sharpe'] > r_eth['sharpe']:
        verdict = f"⚠️  Sharpe 개선이지만 CAGR 열위 → 보수적 운용 시 추가 고려"
        best_config = best_label
    else:
        verdict = f"⚪ ETH>MA50 단순 필터가 가장 효율적 → 복잡한 지표 추가 불필요"
        best_config = "C(ETH>MA50)"

    print(f"  결론: {verdict}")
    print(f"  권장 설정: {best_config}")

    # 현재 상태
    cur_score = int(signal_df.iloc[-1]['score'])
    print(f"\n  현재({signal_df.index[-1].date()}) 점수 {cur_score}/5 →", end=" ")
    if cur_score >= 4:
        print("🟢 강한 저평가 신호 — 점수≥4 조건 충족, 진입 허용")
    elif cur_score >= 3:
        print("🟡 저평가 신호 — 점수≥3 조건 충족, 진입 허용")
    else:
        print("🔴 저평가 신호 미충족 — 진입 차단")

    print(f"\n  ─── scanner_v4 전략 D 파라미터 (권장) ───")
    print(f"    유니버스  : {', '.join(crypto_data.keys())}")
    print(f"    레짐 필터 : {best_config}")
    print(f"    진입      : 6M 상위 50% + ETH 3M 아웃퍼폼 + Close > MA20")
    print(f"    청산      : ATR×{ATR_MULT} 트레일 / MA50 이탈")
    print(f"    VIX 상한  : {VIX_MAX}")
    print("="*W + "\n")


if __name__ == '__main__':
    main()
