"""
backtest_crypto_momentum.py
─────────────────────────────────────────────────────────────────────────────
코인 연동주에 Strategy B(모멘텀) 방식 적용

기존 D/E 문제: S&P500 스타일(RSI<30 + MA200) → 코인에 안 맞음
  - 코인주는 강세장에 MA200 위에서 달리고, 약세장엔 MA200 아래 오래 머묾
  - 평균회귀보다 추세 추종이 맞음

개선: B 로직을 코인 유니버스에 적용
  진입: 6M 수익률 상위 + BTC 대비 아웃퍼폼 + Close > MA20
  필터: BTC > MA50 (코인판 "QQQ > MA200")
  청산: ATR × 3.0 트레일 / MA50 이탈
  VIX : ≤ 30

유니버스: MSTR, BLOK, MARA, RIOT, COIN, BITO
  - MSTR/MARA/RIOT: 2018~
  - BLOK: 2018~
  - COIN/BITO: 2021~

비교:
  [1] 코인 모멘텀 (BTC>MA50 필터)
  [2] 코인 모멘텀 (BTC 필터 없음)
  [3] BTC-USD Buy & Hold
  [4] QQQM Buy & Hold
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
START          = '2018-01-01'
END            = datetime.today().strftime('%Y-%m-%d')
INITIAL_CASH   = 100_000.0
COMMISSION     = 0.001    # 코인주는 스프레드 넓어 비용 높게
SLIPPAGE       = 0.001
TOTAL_COST     = COMMISSION + SLIPPAGE
RISK_FREE_RATE = 0.035
RISK_PER_TRADE = 0.015    # 코인주 변동성 높아 약간 높임
MAX_CAP        = 0.25     # 종목당 최대 25% (유니버스 작음)
MAX_POS        = 4        # 최대 동시 보유

CRYPTO_TICKERS = ['MSTR', 'BLOK', 'MARA', 'RIOT', 'COIN', 'BITO']
BTC_TICKER     = 'BTC-USD'
QQQM_TICKER    = 'QQQM'

# 전략 파라미터
MOM_LONG   = 126   # 6M
MOM_SHORT  = 63    # 3M (BTC 대비 아웃퍼폼 기간)
ATR_MULT   = 3.0
BTC_MA     = 50    # BTC 레짐 필터 MA
VIX_MAX    = 30.0

RSI_PERIOD = 14
ATR_PERIOD = 14


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


def download_single(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, multi_level_index=False)
        if df is None or len(df) < 60:
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
        return compute_indicators(df)
    except Exception as e:
        log.warning(f"{ticker} 다운로드 실패: {e}")
        return None


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
        return {'n':0,'wr':0.0,'avg_pnl_pct':0.0,
                'avg_win_pct':0.0,'avg_loss_pct':0.0,'avg_hold_days':0}
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


def _price(data: dict, ticker: str, date) -> float | None:
    df = data.get(ticker)
    if df is None:
        return None
    try:
        sub = df.loc[:date]
        return float(sub.iloc[-1]['Close']) if not sub.empty else None
    except Exception:
        return None


# ─── 모멘텀 사전 계산 ────────────────────────────────────────────────────────

def precompute_momentum(
    crypto_data: dict[str, pd.DataFrame],
    btc_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    rank_df : 날짜별 6M 수익률 백분위 (0~1)
    rs_df   : 날짜별 종목 3M 수익률 - BTC 3M 수익률
    """
    close_df   = pd.DataFrame({t: df['Close'] for t, df in crypto_data.items()})
    ret_6m     = close_df.pct_change(MOM_LONG)
    rank_df    = ret_6m.rank(axis=1, pct=True)
    ret_3m     = close_df.pct_change(MOM_SHORT)
    btc_ret_3m = btc_df['Close'].pct_change(MOM_SHORT)
    rs_df      = ret_3m.subtract(btc_ret_3m, axis=0)
    return rank_df, rs_df


# ─── 백테스트 ────────────────────────────────────────────────────────────────

def run_crypto_momentum(
    crypto_data: dict[str, pd.DataFrame],
    btc_df:      pd.DataFrame,
    vix_df:      pd.DataFrame,
    rank_df:     pd.DataFrame,
    rs_df:       pd.DataFrame,
    all_dates:   list,
    use_btc_filter: bool,
) -> dict:
    """
    use_btc_filter=True  : BTC > MA50일 때만 진입
    use_btc_filter=False : BTC 필터 없음
    """
    cash       = INITIAL_CASH
    positions: dict = {}
    eq_hist    = []
    trades     = []

    btc_ma50 = btc_df['Close'].rolling(BTC_MA).mean()

    for date in all_dates:
        # VIX 조회
        vix = float('nan')
        try:
            sub = vix_df.loc[:date]
            if not sub.empty:
                vix = float(sub.iloc[-1]['Close'])
        except Exception:
            pass

        # BTC 레짐 확인
        btc_ok = True
        if use_btc_filter:
            try:
                btc_price = float(btc_df.loc[:date].iloc[-1]['Close'])
                ma50_val  = float(btc_ma50.loc[:date].iloc[-1])
                btc_ok    = btc_price > ma50_val
            except Exception:
                btc_ok = False

        total_eq = cash + sum(
            p['shares'] * (_price(crypto_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )

        # 청산: ATR 트레일 / MA50 이탈
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
            ma50  = float(row['MA50'])
            pos   = positions[t]
            pos['trailing_stop'] = max(pos['trailing_stop'], close - atr * ATR_MULT)
            if close <= pos['trailing_stop'] or close < ma50:
                cash += pos['shares'] * close * (1 - TOTAL_COST)
                trades.append((pos['entry_price'], close, pos['entry_date'], date))
                del positions[t]

        # 진입
        can_enter = (not np.isnan(vix) and vix <= VIX_MAX) and btc_ok
        if can_enter and len(positions) < MAX_POS:
            cands = []
            for t, df in crypto_data.items():
                if t in positions:
                    continue
                try:
                    row = df.loc[:date].iloc[-1]
                except Exception:
                    continue
                close = float(row['Close'])
                ma20  = float(row['MA20'])

                # 진입 조건: Close > MA20 (단기 상승 추세)
                if close <= ma20:
                    continue

                # 모멘텀 랭킹 + BTC 아웃퍼폼
                try:
                    rank = float(rank_df.at[date, t]) if (date in rank_df.index and t in rank_df.columns) else float('nan')
                    rs   = float(rs_df.at[date, t])   if (date in rs_df.index   and t in rs_df.columns)   else float('nan')
                except Exception:
                    continue
                if np.isnan(rank) or np.isnan(rs):
                    continue

                # 상위 50% 이상 + BTC 아웃퍼폼 (유니버스가 작아서 25% → 50%)
                if rank >= 0.5 and rs > 0:
                    cands.append((rank, t, row))

            cands.sort(reverse=True)
            for _, t, row in cands[:MAX_POS - len(positions)]:
                stop_dist = float(row['ATR']) * ATR_MULT
                if stop_dist <= 0:
                    continue
                risk_amt = total_eq * RISK_PER_TRADE
                shares   = min(risk_amt / stop_dist,
                               (total_eq * MAX_CAP) / float(row['Close']))
                cost     = shares * float(row['Close']) * (1 + TOTAL_COST)
                if cash >= cost > 0:
                    positions[t] = {
                        'shares':        shares,
                        'entry_price':   float(row['Close']),
                        'entry_date':    date,
                        'trailing_stop': float(row['Close']) - stop_dist,
                    }
                    cash -= cost

        eq = cash + sum(
            p['shares'] * (_price(crypto_data, t, date) or p['entry_price'])
            for t, p in positions.items()
        )
        eq_hist.append(eq)

    stats = calc_stats(eq_hist, INITIAL_CASH, [(t[0], t[1]) for t in trades])
    stats.update(trade_stats(trades))
    return stats


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    log.info(f"데이터 다운로드 중... ({START} ~ {END})")

    crypto_data: dict[str, pd.DataFrame] = {}
    for t in CRYPTO_TICKERS:
        df = download_single(t, START, END)
        if df is not None:
            crypto_data[t] = df
            log.info(f"  {t:<6}: {len(df)}일  ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            log.warning(f"  {t}: 데이터 없음")

    log.info(f"BTC-USD 다운로드 중...")
    btc_df = download_single(BTC_TICKER, START, END)
    if btc_df is None:
        log.error("BTC 데이터 없음 — 종료")
        return
    log.info(f"  BTC-USD: {len(btc_df)}일  ({btc_df.index[0].date()} ~ {btc_df.index[-1].date()})")

    log.info("VIX 다운로드 중...")
    vix_raw = yf.download('^VIX', start=START, end=END, progress=False, multi_level_index=False)
    vix_df  = vix_raw[['Close']].dropna()

    log.info("QQQM 다운로드 중...")
    qqqm_df = download_single(QQQM_TICKER, START, END)

    # 공통 날짜 (주식 거래일 기준, BTC는 365일이라 주식 거래일로 맞춤)
    stock_dates = sorted(set(
        d for df in crypto_data.values() for d in df.index
    ))
    log.info(f"백테스트 기간: {stock_dates[0].date()} ~ {stock_dates[-1].date()}  ({len(stock_dates)}거래일)")
    log.info(f"코인 유니버스: {list(crypto_data.keys())}")

    log.info("모멘텀 랭킹 사전 계산 중...")
    rank_df, rs_df = precompute_momentum(crypto_data, btc_df)

    log.info("[1] 코인 모멘텀 (BTC>MA50 필터) 백테스트...")
    r_filtered = run_crypto_momentum(
        crypto_data, btc_df, vix_df, rank_df, rs_df, stock_dates,
        use_btc_filter=True,
    )

    log.info("[2] 코인 모멘텀 (BTC 필터 없음) 백테스트...")
    r_nofilter = run_crypto_momentum(
        crypto_data, btc_df, vix_df, rank_df, rs_df, stock_dates,
        use_btc_filter=False,
    )

    # BTC B&H
    btc_shares = 0.0
    btc_hist   = []
    for date in stock_dates:
        try:
            sub = btc_df.loc[:date]
            p   = float(sub.iloc[-1]['Close']) if not sub.empty else None
        except Exception:
            p = None
        if p is None:
            btc_hist.append(btc_hist[-1] if btc_hist else INITIAL_CASH)
            continue
        if btc_shares == 0.0:
            btc_shares = INITIAL_CASH * (1 - TOTAL_COST) / p
        btc_hist.append(btc_shares * p)
    btc_bh = calc_stats(btc_hist, INITIAL_CASH, [])

    # QQQM B&H
    qqqm_shares = 0.0
    qqqm_hist   = []
    for date in stock_dates:
        try:
            sub = qqqm_df.loc[:date] if qqqm_df is not None else pd.DataFrame()
            p   = float(sub.iloc[-1]['Close']) if not sub.empty else None
        except Exception:
            p = None
        if p is None:
            qqqm_hist.append(qqqm_hist[-1] if qqqm_hist else INITIAL_CASH)
            continue
        if qqqm_shares == 0.0:
            qqqm_shares = INITIAL_CASH * (1 - TOTAL_COST) / p
        qqqm_hist.append(qqqm_shares * p)
    qqqm_bh = calc_stats(qqqm_hist, INITIAL_CASH, [])

    # ── 출력 ────────────────────────────────────────────────────────────────
    W     = 105
    years = len(stock_dates) / 252.0

    print("\n" + "=" * W)
    print(f"  코인 모멘텀 전략 백테스트  {START} ~ {END}  ({years:.1f}년  {len(stock_dates)}거래일)".center(W))
    print("=" * W)

    print(f"\n  ─── 유니버스 데이터 현황 ───")
    for t, df in crypto_data.items():
        days = len(df)
        print(f"    {t:<6}: {days}거래일  ({df.index[0].date()} ~ {df.index[-1].date()})")
    btc_ma50_latest = float(btc_df['Close'].rolling(BTC_MA).mean().iloc[-1])
    btc_latest      = float(btc_df['Close'].iloc[-1])
    btc_regime      = "🟢 BTC > MA50 (진입 허용)" if btc_latest > btc_ma50_latest else "🔴 BTC < MA50 (진입 차단)"
    print(f"\n  현재 BTC: ${btc_latest:,.0f}  MA{BTC_MA}: ${btc_ma50_latest:,.0f}  → {btc_regime}")

    print(f"\n  ─── 전략 설명 ───")
    print(f"    진입: 6M 수익률 상위 50% + BTC 대비 3M 아웃퍼폼 + Close > MA20 + VIX ≤ {VIX_MAX}")
    print(f"    청산: ATR×{ATR_MULT} 트레일링 스톱 / MA50 이탈")
    print(f"    레짐: BTC > MA{BTC_MA} (코인판 QQQ>MA200)")
    print(f"    비용: 편도 {TOTAL_COST*100:.1f}% (스프레드 포함)")

    print(f"\n  ─── 성과 비교 ───")
    print(f"  {'':38} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'Sortino':>8} {'거래':>6} {'승률':>6} {'평균수익':>9} {'평균보유':>7}")
    print(f"  {'─'*38} {'─'*7} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*9} {'─'*7}")

    def row(label, r):
        n    = r.get('n', 0)
        wr   = r.get('wr', 0.0)
        pnl  = r.get('avg_pnl_pct', float('nan'))
        hold = r.get('avg_hold_days', 0)
        pnl_s  = f"{pnl:>+8.2f}%" if not (isinstance(pnl, float) and np.isnan(pnl)) else "      N/A"
        hold_s = f"{hold:>5}일" if hold else "   N/A"
        print(
            f"  {label:<38} {r['cagr']:>+6.2f}% {r['mdd']:>7.2f}% "
            f"{r['sharpe']:>8.3f} {r['sortino']:>8.3f} "
            f"{n:>5}건 {wr:>5.1f}% {pnl_s} {hold_s}"
        )

    row(f"코인 모멘텀 (BTC>MA{BTC_MA} 필터)", r_filtered)
    row("코인 모멘텀 (BTC 필터 없음)",        r_nofilter)
    row("[기준] BTC-USD Buy & Hold",          btc_bh)
    row("[기준] QQQM Buy & Hold",             qqqm_bh)

    # 필터 효과
    dc = r_filtered['cagr']   - r_nofilter['cagr']
    ds = r_filtered['sharpe'] - r_nofilter['sharpe']
    dm = r_filtered['mdd']    - r_nofilter['mdd']
    print(f"\n  ▷ BTC 필터 효과: CAGR {dc:>+.2f}%  MDD {dm:>+.2f}%  Sharpe {ds:>+.3f}")

    # BTC B&H 대비
    db = r_filtered['cagr'] - btc_bh['cagr']
    dq = r_filtered['cagr'] - qqqm_bh['cagr']
    print(f"  ▷ BTC B&H 대비  : CAGR {db:>+.2f}%  (양수=모멘텀 초과, 음수=그냥 BTC 사는 게 나음)")
    print(f"  ▷ QQQM B&H 대비 : CAGR {dq:>+.2f}%")

    # ── 종합 판단 ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  📋 종합 판단")
    print(f"{'=' * W}")

    best_sharpe = max(r_filtered['sharpe'], r_nofilter['sharpe'])
    btc_sharpe  = btc_bh['sharpe']

    if r_filtered['sharpe'] > btc_sharpe and r_filtered['cagr'] > 0:
        verdict = "✅ 코인 모멘텀 유효: BTC B&H 대비 리스크 조정 수익 우위"
        action  = "scanner_v4에 전략 D (코인 모멘텀) 추가 권장"
    elif r_filtered['cagr'] > btc_bh['cagr']:
        verdict = "⚠️  CAGR은 BTC 초과지만 Sharpe 열위 — 변동성 감수해야"
        action  = "소규모(현금 10% 이하) 배분 고려, 전체 시스템 편입은 보류"
    else:
        verdict = "❌ BTC 직접 보유보다 못함 — 코인주 모멘텀 전략 비효율"
        action  = "코인 노출 원하면 BTC ETF(IBIT) 직접 보유가 더 효율적"

    print(f"  결과  : {verdict}")
    print(f"  권장  : {action}")
    print(f"\n  ─── 핵심 파라미터 (조정 가능) ───")
    print(f"    BTC MA 기간  : {BTC_MA}일  (20/50/100 중 50 최적 예상)")
    print(f"    모멘텀 기간  : {MOM_LONG}거래일 6M / {MOM_SHORT}거래일 3M")
    print(f"    랭킹 커트라인: 상위 50%  (유니버스 6개 → 3개 이상 통과)")
    print(f"    VIX 상한     : {VIX_MAX}")
    print("=" * W + "\n")


if __name__ == '__main__':
    main()
