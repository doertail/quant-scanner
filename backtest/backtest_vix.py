"""
VIX 필터 백테스트
─────────────────────────────────────────────────────────────────────
전략 A (방패 — 평균회귀) 기준으로 VIX 조건 유무에 따른 성과 비교.

테스트 방법:
  - Universe : S&P500 대표 30개 종목 (섹터 분산)
  - 기간     : 2015-01-01 ~ 2025-12-31 (약 10년)
  - 전략     : 전략 A 조건 충족 시 다음날 시가 매수
  - 청산     : RSI ≥ 70 또는 Close < ATR Stop (3x)
  - 비교군   : (1) 원래 전략 A  (2) 전략 A + VIX>20  (3) 전략 A + VIX>25  (4) 전략 A + VIX>30

결과 지표:
  - 총 트레이드 수 / 승률 / 평균 수익률 / MDD / Sharpe 근사치
"""

import warnings
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ─── 설정 ──────────────────────────────────────────────────────────
START       = '2015-01-01'
END         = '2025-12-31'
RSI_BUY     = 35
ATR_MULT    = 3.0
RSI_PERIOD  = 14
ATR_PERIOD  = 14

# 섹터 분산된 S&P500 대표 종목 30개
UNIVERSE = [
    # 기술
    'AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA',
    # 금융
    'JPM', 'BAC', 'GS', 'WFC', 'BRK-B',
    # 헬스케어
    'JNJ', 'UNH', 'PFE', 'ABBV', 'MRK',
    # 소비재 / 필수소비재
    'AMZN', 'HD', 'MCD', 'NKE', 'PG',
    # 에너지 / 산업재
    'XOM', 'CVX', 'CAT', 'MMM', 'BA',
    # 유틸리티 / 통신 / 기타
    'NEE', 'DUK', 'VZ', 'T', 'WMT',
]

# ─── 지표 계산 ─────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    delta  = df['Close'].diff()
    up     = delta.clip(lower=0)
    down   = -delta.clip(upper=0)
    df['RSI']  = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD-1, adjust=False).mean()
                                    / down.ewm(com=RSI_PERIOD-1, adjust=False).mean()))
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA200']= df['Close'].rolling(200).mean()
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD-1, adjust=False).mean()
    return df

# ─── 단일 종목 백테스트 ───────────────────────────────────────────
def backtest_ticker(df: pd.DataFrame, vix: pd.Series, vix_threshold: float | None) -> list[dict]:
    """
    매수 조건: RSI < 35, Close < MA20, Close > MA200 [+ VIX > threshold]
    청산 조건: RSI >= 70 OR Close <= trailing_stop (ATR 3x)
    """
    trades = []
    in_pos = False
    entry_price = 0.0
    trail_stop  = 0.0

    for i in range(201, len(df) - 1):
        row  = df.iloc[i]
        next_row = df.iloc[i + 1]

        close = float(row['Close'])
        rsi   = float(row['RSI'])
        ma20  = float(row['MA20'])
        ma200 = float(row['MA200'])
        atr   = float(row['ATR'])
        date  = df.index[i]

        if pd.isna(rsi) or pd.isna(ma20) or pd.isna(ma200) or pd.isna(atr):
            continue

        if in_pos:
            # 트레일링 스톱 갱신
            new_stop = close - atr * ATR_MULT
            if new_stop > trail_stop:
                trail_stop = new_stop

            # 청산 조건
            exit_price = None
            exit_reason = None
            if close <= trail_stop:
                exit_price  = float(next_row['Open'])
                exit_reason = 'STOP'
            elif rsi >= 70:
                exit_price  = float(next_row['Open'])
                exit_reason = 'RSI70'

            if exit_price:
                pnl = (exit_price - entry_price) / entry_price * 100
                trades.append({'pnl': pnl, 'exit_reason': exit_reason, 'entry_date': entry_date, 'exit_date': df.index[i+1]})
                in_pos = False

        else:
            # 매수 조건
            cond_base = (rsi < RSI_BUY and close < ma20 and close > ma200)
            if not cond_base:
                continue

            # VIX 조건
            if vix_threshold is not None:
                vix_val = vix.get(date, None)
                if vix_val is None or pd.isna(vix_val) or float(vix_val) <= vix_threshold:
                    continue

            entry_price = float(next_row['Open'])
            trail_stop  = entry_price - atr * ATR_MULT
            entry_date  = df.index[i+1]
            in_pos      = True

    return trades

# ─── 성과 요약 ─────────────────────────────────────────────────────
def summarize(trades: list[dict], label: str) -> dict:
    if not trades:
        return {'label': label, 'n': 0, 'win_rate': 0, 'avg_pnl': 0, 'median_pnl': 0,
                'best': 0, 'worst': 0, 'total_pnl': 0, 'sharpe': 0}
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    return {
        'label'     : label,
        'n'         : len(pnls),
        'win_rate'  : round(len(wins) / len(pnls) * 100, 1),
        'avg_pnl'   : round(np.mean(pnls), 2),
        'median_pnl': round(np.median(pnls), 2),
        'best'      : round(max(pnls), 2),
        'worst'     : round(min(pnls), 2),
        'total_pnl' : round(sum(pnls), 2),
        'sharpe'    : round(np.mean(pnls) / (np.std(pnls) + 1e-9), 3),
    }

# ─── 메인 ─────────────────────────────────────────────────────────
def main():
    print("="*70)
    print("  VIX 필터 백테스트  |  전략 A (방패 — 평균회귀)")
    print(f"  Universe: {len(UNIVERSE)}개 종목  |  {START} ~ {END}")
    print("="*70)

    # VIX 다운로드
    print("\n[1/3] VIX 데이터 다운로드...")
    vix_raw = yf.download('^VIX', start=START, end=END, progress=False)
    if vix_raw.empty:
        print("  VIX 데이터 수신 실패")
        return

    # VIX Close 추출 (MultiIndex 대응)
    if isinstance(vix_raw.columns, pd.MultiIndex):
        vix_series = vix_raw[('Close', '^VIX')]
    else:
        vix_series = vix_raw['Close']
    vix_series = vix_series.dropna()
    print(f"  VIX 데이터: {len(vix_series)}일  |  평균 {vix_series.mean():.1f}  최고 {vix_series.max():.1f}")

    # 종목 다운로드
    print("\n[2/3] 종목 데이터 다운로드...")
    raw = yf.download(UNIVERSE, start=START, end=END, group_by='ticker', progress=False, threads=True)

    # 백테스트 실행
    print("\n[3/3] 백테스트 실행 중...\n")
    scenarios = [
        ('전략 A (원래)',        None),
        ('전략 A + VIX > 20', 20.0),
        ('전략 A + VIX > 25', 25.0),
        ('전략 A + VIX > 30', 30.0),
    ]

    all_results = {label: [] for label, _ in scenarios}

    valid_tickers = 0
    for ticker in UNIVERSE:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw[ticker][['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            else:
                df = raw[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            df = df.dropna(subset=['Close'])
            if len(df) < 220:
                continue
            df = compute_indicators(df)
            valid_tickers += 1

            for label, threshold in scenarios:
                trades = backtest_ticker(df, vix_series, threshold)
                all_results[label].extend(trades)
        except Exception as e:
            pass

    print(f"  유효 종목: {valid_tickers}개\n")

    # 결과 출력
    summaries = []
    for label, _ in scenarios:
        s = summarize(all_results[label], label)
        summaries.append(s)

    header = f"  {'시나리오':<22} {'트레이드':>8} {'승률':>7} {'평균PnL':>8} {'중앙값':>8} {'최고':>7} {'최저':>8} {'Sharpe':>8}"
    print("─"*90)
    print(header)
    print("─"*90)
    for s in summaries:
        print(
            f"  {s['label']:<22} {s['n']:>8} {s['win_rate']:>6.1f}%"
            f" {s['avg_pnl']:>+7.2f}% {s['median_pnl']:>+7.2f}%"
            f" {s['best']:>+6.2f}% {s['worst']:>+7.2f}%  {s['sharpe']:>7.3f}"
        )
    print("─"*90)

    # VIX 구간별 트레이드 분포 (원래 전략 기준)
    base_trades = all_results['전략 A (원래)']
    if base_trades:
        print("\n  [VIX 구간별 성과 분포] — 전략 A 전체 트레이드 기준")
        print(f"  {'VIX 구간':<15} {'트레이드':>8} {'승률':>7} {'평균PnL':>9}")
        print("  " + "─"*42)
        buckets = [
            ('<= 15',   lambda v: v <= 15),
            ('15–20',   lambda v: 15 < v <= 20),
            ('20–25',   lambda v: 20 < v <= 25),
            ('25–30',   lambda v: 25 < v <= 30),
            ('30–40',   lambda v: 30 < v <= 40),
            ('> 40',    lambda v: v > 40),
        ]
        # 진입일의 VIX 값 조회
        for ticker in UNIVERSE:
            pass  # 이미 all_results에 trade별 날짜 있음

        # 전체 base_trades에 VIX 값 붙이기
        enriched = []
        for t in base_trades:
            entry_date = t.get('entry_date')
            if entry_date is None:
                continue
            # entry_date가 Timestamp인 경우 date 변환
            if hasattr(entry_date, 'date'):
                ed = entry_date.date()
            else:
                ed = entry_date
            # VIX 시리즈에서 entry 전날 값
            vix_dates = vix_series.index
            # entry_date 이전 가장 가까운 VIX 값 찾기
            mask = vix_dates <= pd.Timestamp(ed)
            if mask.any():
                vv = float(vix_series[mask].iloc[-1])
                enriched.append({'pnl': t['pnl'], 'vix': vv})

        for bucket_name, fn in buckets:
            bt = [e for e in enriched if fn(e['vix'])]
            if not bt:
                print(f"  {bucket_name:<15} {'0':>8} {'—':>7} {'—':>9}")
                continue
            pnls_b = [e['pnl'] for e in bt]
            wins_b = [p for p in pnls_b if p > 0]
            win_r  = len(wins_b) / len(pnls_b) * 100
            avg_p  = np.mean(pnls_b)
            print(f"  {bucket_name:<15} {len(pnls_b):>8} {win_r:>6.1f}%  {avg_p:>+7.2f}%")

    print("\n  ✅ 백테스트 완료")
    print()

    # 요약 해석
    print("─"*70)
    print("  [해석]")
    base = summaries[0]
    for s in summaries[1:]:
        delta_wr  = s['win_rate']  - base['win_rate']
        delta_pnl = s['avg_pnl']   - base['avg_pnl']
        delta_sh  = s['sharpe']    - base['sharpe']
        icon = "✅" if delta_pnl > 0 else "❌"
        print(f"  {icon} {s['label']:<22}: 트레이드 {s['n']:+d}건 대비 원래 {base['n']}건  "
              f"승률 {delta_wr:+.1f}%  평균PnL {delta_pnl:+.2f}%  Sharpe {delta_sh:+.3f}")
    print("─"*70)


if __name__ == '__main__':
    main()
