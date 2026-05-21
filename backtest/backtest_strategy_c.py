"""
전략 C — VIX 패닉 매수 백테스트
─────────────────────────────────────────────────────────────────────
진입: VIX 30 상향 돌파 → 다음 거래일 시가 매수
청산 시나리오 비교:
  (1) VIX < 20 복귀 시 종가 매도  (공포 정상화)
  (2) VIX < 25 복귀 시 종가 매도
  (3) 고정 보유 3개월 후 매도
  (4) 고정 보유 6개월 후 매도
  (5) 고정 보유 1년 후 매도

비교 대상: SPY vs QQQ
바이앤홀드 기준선도 함께 계산
"""

import warnings
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

START = '1993-01-01'
END   = datetime.today().strftime('%Y-%m-%d')

def download(ticker):
    df = yf.download(ticker, start=START, end=END, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        return df[('Close', ticker)].dropna(), df[('Open', ticker)].dropna()
    return df['Close'].dropna(), df['Open'].dropna()

def find_vix30_entries(vix: pd.Series):
    """VIX 30 상향 돌파 날짜 목록"""
    entries, above = [], False
    for date, val in vix.items():
        if not above and val >= 30:
            entries.append(date)
            above = True
        elif above and val < 30:
            above = False
    return entries

def backtest_vix_strategy(
    entries: list,
    asset_close: pd.Series,
    asset_open:  pd.Series,
    vix:         pd.Series,
    exit_mode:   str,       # 'vix20' | 'vix25' | '3m' | '6m' | '1y'
) -> list[dict]:
    all_dates  = asset_close.index.tolist()
    trades     = []

    for entry_date in entries:
        # 다음 거래일 시가 진입
        future = [i for i, d in enumerate(all_dates) if d > entry_date]
        if not future:
            continue
        ei = future[0]
        entry_price = float(asset_open.iloc[ei])
        entry_actual_date = all_dates[ei]

        exit_price = None
        exit_date  = None
        exit_reason = exit_mode

        if exit_mode in ('vix20', 'vix25'):
            threshold = 20.0 if exit_mode == 'vix20' else 25.0
            for j in range(ei + 1, len(all_dates)):
                d   = all_dates[j]
                vv  = vix.asof(d) if hasattr(vix, 'asof') else vix.get(d, float('nan'))
                if pd.isna(vv):
                    continue
                if float(vv) < threshold:
                    exit_price = float(asset_close.iloc[j])
                    exit_date  = d
                    break
            if exit_price is None:  # 아직 미청산
                exit_price = float(asset_close.iloc[-1])
                exit_date  = all_dates[-1]
                exit_reason = f'{exit_mode}(미청산)'
        else:
            days_map = {'3m': 63, '6m': 126, '1y': 252}
            hold_days = days_map[exit_mode]
            xi = ei + hold_days
            if xi >= len(all_dates):
                continue
            exit_price = float(asset_close.iloc[xi])
            exit_date  = all_dates[xi]

        pnl = (exit_price - entry_price) / entry_price * 100
        # 보유 기간 (영업일)
        hold = len([d for d in all_dates if entry_actual_date <= d <= exit_date])

        trades.append({
            'entry_date': entry_actual_date,
            'exit_date':  exit_date,
            'entry_price': entry_price,
            'exit_price':  exit_price,
            'pnl':        pnl,
            'hold_days':  hold,
            'exit_reason': exit_reason,
        })
    return trades

def summarize(trades, label):
    if not trades:
        return None
    pnls  = [t['pnl'] for t in trades]
    wins  = [p for p in pnls if p > 0]
    holds = [t['hold_days'] for t in trades]
    return {
        'label':      label,
        'n':          len(pnls),
        'win_rate':   len(wins) / len(pnls) * 100,
        'avg_pnl':    np.mean(pnls),
        'median_pnl': np.median(pnls),
        'best':       max(pnls),
        'worst':      min(pnls),
        'sharpe':     np.mean(pnls) / (np.std(pnls) + 1e-9),
        'avg_hold':   np.mean(holds),
    }

def print_row(s):
    if s is None:
        return
    print(
        f"  {s['label']:<22} {s['n']:>4}  {s['win_rate']:>6.1f}%"
        f"  {s['avg_pnl']:>+7.2f}%  {s['median_pnl']:>+7.2f}%"
        f"  {s['best']:>+7.2f}%  {s['worst']:>+7.2f}%"
        f"  {s['sharpe']:>6.3f}  {s['avg_hold']:>5.0f}일"
    )

def annualized_return(close: pd.Series) -> float:
    total_years = (close.index[-1] - close.index[0]).days / 365
    return ((float(close.iloc[-1]) / float(close.iloc[0])) ** (1 / total_years) - 1) * 100

def main():
    print("=" * 80)
    print("  전략 C — VIX 패닉 매수 백테스트")
    print(f"  {START} ~ {END}")
    print("=" * 80)

    print("\n데이터 다운로드 중...")
    vix_close, _        = download('^VIX')
    spy_close, spy_open = download('SPY')
    qqq_close, qqq_open = download('QQQ')

    entries = find_vix30_entries(vix_close)
    print(f"  VIX 30 돌파 이벤트: {len(entries)}회\n")

    EXIT_MODES = [
        ('VIX < 20 복귀',   'vix20'),
        ('VIX < 25 복귀',   'vix25'),
        ('고정 3개월',       '3m'),
        ('고정 6개월',       '6m'),
        ('고정 1년',         '1y'),
    ]

    hdr = f"  {'청산 방식':<22} {'건':>4}  {'승률':>6}  {'평균PnL':>8}  {'중앙값':>8}  {'최고':>8}  {'최저':>8}  {'Sharpe':>7}  {'평균보유'}"
    sep = "  " + "─" * 78

    for asset_label, asset_close, asset_open in [
        ("SPY (S&P500)", spy_close, spy_open),
        ("QQQ (Nasdaq)", qqq_close, qqq_open),
    ]:
        bnh = annualized_return(asset_close)
        print(f"\n{'='*80}")
        print(f"  {asset_label}  |  바이앤홀드 연환산 {bnh:+.1f}%/년")
        print(f"{'='*80}")
        print(hdr)
        print(sep)
        for mode_label, mode_key in EXIT_MODES:
            trades = backtest_vix_strategy(entries, asset_close, asset_open, vix_close, mode_key)
            s = summarize(trades, mode_label)
            print_row(s)

    # ── VIX < 20 청산 전략 — 이벤트별 상세 ──────────────────────────────────
    print(f"\n{'='*80}")
    print("  [SPY — 'VIX < 20 복귀' 청산 이벤트 상세]")
    print(f"{'='*80}")
    print(f"  {'진입일':<12} {'청산일':<12} {'보유':<6} {'진입VIX':>8} {'PnL':>8}  이벤트")
    print("  " + "─" * 64)

    EVENT_MAP = {
        1997: '아시아 금융위기', 1998: 'LTCM/러시아',
        2000: '닷컴 버블', 2001: '9/11', 2002: '닷컴 붕괴',
        2007: '금융위기 전조', 2008: '금융위기', 2009: '금융위기 저점',
        2010: '유럽 재정위기', 2011: '신용등급 강등',
        2015: '중국 쇼크', 2018: '12월 급락',
        2020: 'COVID-19', 2021: '테이퍼링',
        2022: '금리 인상', 2024: '엔 캐리 청산',
        2025: '관세 전쟁',
    }

    trades_detail = backtest_vix_strategy(entries, spy_close, spy_open, vix_close, 'vix20')
    for t in trades_detail:
        yr  = t['entry_date'].year
        ev  = EVENT_MAP.get(yr, '')
        vv  = vix_close.asof(t['entry_date'])
        pnl_icon = "✅" if t['pnl'] > 0 else "❌"
        print(
            f"  {str(t['entry_date'].date()):<12} {str(t['exit_date'].date()):<12}"
            f" {t['hold_days']:>4}일  {float(vv):>7.1f}"
            f"  {pnl_icon}{t['pnl']:>+6.1f}%  {ev}"
        )

    # ── 바이앤홀드 대비 요약 ─────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("  [핵심 지표 비교]")
    print(f"{'='*80}")

    for asset_label, asset_close, asset_open in [
        ("SPY", spy_close, spy_open),
        ("QQQ", qqq_close, qqq_open),
    ]:
        bnh = annualized_return(asset_close)
        trades_v20 = backtest_vix_strategy(entries, asset_close, asset_open, vix_close, 'vix20')
        s = summarize(trades_v20, 'VIX<20 청산')
        if s:
            print(f"\n  {asset_label}")
            print(f"    바이앤홀드 연환산    : {bnh:+.1f}%/년")
            print(f"    VIX 패닉 매수 (건당) : 승률 {s['win_rate']:.0f}%  평균 {s['avg_pnl']:+.1f}%  "
                  f"중앙값 {s['median_pnl']:+.1f}%  평균보유 {s['avg_hold']:.0f}일")

            # 연평균 환산: 평균 보유일 기준
            if s['avg_hold'] > 0:
                ann = s['avg_pnl'] / (s['avg_hold'] / 252) if s['avg_hold'] > 0 else 0
                print(f"    보유기간 환산 연수익  : {ann:+.1f}%/년  (평균 {s['avg_hold']:.0f}거래일 보유)")

    print()


if __name__ == '__main__':
    main()
