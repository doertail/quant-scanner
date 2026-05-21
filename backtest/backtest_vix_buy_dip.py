"""
VIX 공포 매수 백테스트
─────────────────────────────────────────────────────────────────────
전략: VIX가 특정 임계값을 돌파할 때 분할 매수, VIX < 20 시 전량 청산

매수 조건:
  - VIX 30 상향 돌파 시 1/3 매수  (1차)
  - VIX 35 상향 돌파 시 추가 1/3  (2차)
  - VIX 40 상향 돌파 시 나머지    (3차)

청산 조건:
  - VIX < 20 하락 시 전량 청산

대상: SPY, VOO, QQQ, BTC-USD
기간: 2015-01-01 ~ 2025-12-31
"""

import warnings
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np

START  = '2004-01-01'
END    = '2025-12-31'

TICKERS = ['SPY', 'QQQ']  # SPLG/SSO/UPRO/QLD는 2004년 이전 데이터 없음

VIX_LEVELS   = [30.0, 35.0, 40.0]   # 분할 매수 진입 VIX 레벨
VIX_EXIT     = 20.0                  # 청산 VIX 레벨
ALLOC_FRAC   = [1/3, 1/3, 1/3]      # 각 레벨별 비중
PROFIT_EXIT  = 15.0                  # 수익률 목표 청산 (%, 절반 청산)


def run_backtest(ticker: str, price: pd.Series, vix: pd.Series) -> list[dict]:
    """
    분할 매수 / VIX < 20 청산 시뮬레이션.
    초기 자본 1 (normalized). 현금 비중으로 추적.
    """
    episodes = []   # 완결된 에피소드 기록

    cash            = 1.0
    holdings        = 0.0   # 보유 주식 수 (normalized)
    entry_log       = []    # [(date, price, frac)]
    triggered       = [False, False, False]  # 레벨별 진입 여부

    dates = price.index
    for i in range(1, len(dates)):
        d    = dates[i]
        p    = float(price.iloc[i])
        prev_vix = vix.get(dates[i-1], None)
        curr_vix = vix.get(d, None)

        if prev_vix is None or curr_vix is None:
            continue
        if pd.isna(p) or pd.isna(prev_vix) or pd.isna(curr_vix):
            continue

        # ── 분할 매수 진입 ──────────────────────────────
        for j, level in enumerate(VIX_LEVELS):
            if not triggered[j] and prev_vix <= level and curr_vix > level:
                spend = ALLOC_FRAC[j]
                if spend > cash:
                    spend = cash
                shares_bought = spend / p
                holdings += shares_bought
                cash     -= spend
                triggered[j] = True
                entry_log.append({'date': d, 'price': p, 'frac': spend, 'level': level})

        if holdings <= 0:
            continue

        # ── VIX < 20 전량 청산 ──────────────────────────
        if prev_vix >= VIX_EXIT and curr_vix < VIX_EXIT:
            exit_value  = holdings * p
            total_spent = sum(e['frac'] for e in entry_log)
            pnl_pct     = (exit_value + (cash - (1.0 - total_spent)) - total_spent) / total_spent * 100 \
                          if total_spent > 0 else 0
            # 단순하게: 남은 holdings 기준 수익률
            remaining_spent = total_spent * (holdings / (holdings + (cash - (1.0 - total_spent)) / p)) \
                              if holdings > 0 else total_spent
            pnl_pct = (exit_value - total_spent) / total_spent * 100 if total_spent > 0 else 0

            entry_dates = [e['date'] for e in entry_log]
            days_held   = (d - min(entry_dates)).days if entry_dates else 0

            episodes.append({
                'entry_dates' : entry_dates,
                'exit_date'   : d,
                'days_held'   : days_held,
                'total_spent' : round(total_spent, 4),
                'exit_value'  : round(exit_value, 4),
                'pnl_pct'     : round(pnl_pct, 2),
                'tranches'    : len(entry_log),
                'exit_reason' : 'VIX<20',
            })

            # 리셋
            cash      += exit_value
            holdings   = 0.0
            entry_log  = []
            triggered  = [False, False, False]

    # 미청산 포지션 처리 (마지막 가격 기준)
    if holdings > 0:
        last_price  = float(price.iloc[-1])
        exit_value  = holdings * last_price
        total_spent = sum(e['frac'] for e in entry_log)
        pnl_pct     = (exit_value - total_spent) / total_spent * 100 if total_spent > 0 else 0
        entry_dates = [e['date'] for e in entry_log]
        episodes.append({
            'entry_dates' : entry_dates,
            'exit_date'   : dates[-1],
            'days_held'   : (dates[-1] - min(entry_dates)).days if entry_dates else 0,
            'total_spent' : round(total_spent, 4),
            'exit_value'  : round(exit_value, 4),
            'pnl_pct'     : round(pnl_pct, 2),
            'tranches'    : len(entry_log),
            'exit_reason' : 'OPEN',
            'open'        : True,
        })

    return episodes


def compute_buyhold(price: pd.Series) -> float:
    """단순 Buy & Hold 수익률 (전체 기간)"""
    return (float(price.iloc[-1]) / float(price.dropna().iloc[0]) - 1) * 100


def main():
    print("=" * 72)
    print("  VIX 공포 매수 백테스트")
    print(f"  전략: VIX 30/35/40 상향 분할 매수 → VIX<20 전량 청산")
    print(f"  기간: {START} ~ {END}")
    print("=" * 72)

    # VIX 다운로드
    print("\n[1/2] VIX & 종목 데이터 다운로드...")
    vix_raw = yf.download('^VIX', start=START, end=END, progress=False)
    if isinstance(vix_raw.columns, pd.MultiIndex):
        vix_series = vix_raw[('Close', '^VIX')]
    else:
        vix_series = vix_raw['Close']
    vix_series = vix_series.dropna()

    # 종목 다운로드
    raw = yf.download(TICKERS, start=START, end=END, group_by='ticker', progress=False)

    print(f"  VIX: {len(vix_series)}일  평균 {vix_series.mean():.1f}  최고 {vix_series.max():.1f}\n")

    print("[2/2] 백테스트 실행...\n")

    all_summaries = []

    for ticker in TICKERS:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                price = raw[ticker]['Close'].dropna()
            else:
                price = raw['Close'].dropna()

            episodes = run_backtest(ticker, price, vix_series)
            bh = compute_buyhold(price)

            if not episodes:
                all_summaries.append({'ticker': ticker, 'n': 0, 'bh': round(bh, 1)})
                continue

            pnls     = [e['pnl_pct'] for e in episodes]
            wins     = [p for p in pnls if p > 0]
            avg_days = np.mean([e['days_held'] for e in episodes])
            open_ep  = [e for e in episodes if e.get('open')]

            all_summaries.append({
                'ticker'   : ticker,
                'n'        : len(episodes),
                'win_rate' : round(len(wins) / len(pnls) * 100, 1),
                'avg_pnl'  : round(np.mean(pnls), 2),
                'best'     : round(max(pnls), 2),
                'worst'    : round(min(pnls), 2),
                'avg_days' : round(avg_days, 0),
                'bh'       : round(bh, 1),
                'episodes' : episodes,
                'open_ep'  : open_ep,
            })
        except Exception as e:
            print(f"  {ticker} 오류: {e}")

    # ── 결과 출력 ────────────────────────────────────────────────────
    header = f"  {'종목':<10} {'에피소드':>8} {'승률':>7} {'평균PnL':>9} {'최고':>8} {'최저':>8} {'평균보유일':>9} {'Buy&Hold':>10}"
    print("─" * 78)
    print(header)
    print("─" * 78)
    for s in all_summaries:
        if s['n'] == 0:
            print(f"  {s['ticker']:<10} {'신호없음':>8}  {'—':>6}  {'—':>8}  {'—':>7}  {'—':>7}  {'—':>8}  {s.get('bh', 0):>+8.1f}%")
            continue
        print(
            f"  {s['ticker']:<10} {s['n']:>8} {s['win_rate']:>6.1f}%"
            f" {s['avg_pnl']:>+8.2f}%"
            f" {s['best']:>+7.2f}%"
            f" {s['worst']:>+7.2f}%"
            f" {s['avg_days']:>9.0f}일"
            f" {s['bh']:>+9.1f}%"
        )
    print("─" * 78)

    # ── 에피소드 상세 ────────────────────────────────────────────────
    print("\n  [에피소드 상세]")
    for s in all_summaries:
        if s['n'] == 0:
            continue
        print(f"\n  ▶ {s['ticker']}")
        print(f"  {'진입일(첫번째)':<18} {'청산일':<14} {'트랑쉐':>6} {'PnL':>9} {'보유일':>7} {'비고'}")
        print("  " + "─" * 65)
        for ep in s['episodes']:
            first_entry = str(ep['entry_dates'][0])[:10] if ep['entry_dates'] else '—'
            exit_d      = str(ep['exit_date'])[:10]
            note        = f"★ {ep.get('exit_reason','')}" if ep.get('open') else ep.get('exit_reason','')
            print(f"  {first_entry:<18} {exit_d:<14} {ep['tranches']:>6} {ep['pnl_pct']:>+8.2f}% {ep['days_held']:>6}일  {note}")

    # ── VIX 구간별 기회 횟수 ─────────────────────────────────────────
    print("\n\n  [VIX 임계값 돌파 횟수 — 진입 기회]")
    prev = vix_series.iloc[0]
    counts = {30: 0, 35: 0, 40: 0}
    for v in vix_series.iloc[1:]:
        for lvl in counts:
            if prev <= lvl < v:
                counts[lvl] += 1
        prev = v
    for lvl, cnt in counts.items():
        print(f"  VIX {lvl} 상향 돌파: {cnt}회")

    print("\n  ✅ 완료\n")


if __name__ == '__main__':
    main()
