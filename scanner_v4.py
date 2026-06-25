"""
Market Scanner v4
─────────────────────────────────────────────────────────────────────
Phase 1  유니버스 스캔 (S&P500 + NDX100 전수 조사)
  거시 필터 : QQQ > MA200
  VIX 필터  : 구간별 전략 허용/차단
  전략 A (방패): S&P500 평균회귀   RSI < 35, Close < MA20, Close > MA200
  전략 B (창):  NDX100 모멘텀     6개월 수익률 상위 25% + 3개월 QQQ 아웃퍼폼, Close > MA20/MA200
  전략 C (지수): VIX 30 돌파 시 SPY / QQQ 직접 매수 → VIX < 20 복귀 시 청산
                백테스트: SPY 승률 96.4% / 평균 +11.5% (보유 88일), Sharpe 1.21
  전략 D (크립토): ETH>MA50 레짐 + 크립토 관련주 모멘텀 (MSTR/MARA/RIOT/COIN/BITO/BLOK)
                  백테스트: CAGR +17.62%, Sharpe 0.813 (2017–2024)

Phase 2  포트폴리오 모니터 (portfolio.json)
  - 전략 A 청산: ATR 트레일링 스톱 / TP1(RSI ≥ 50 → 50% 분할) / TP2(MA20 도달 → 전량)
  - 전략 B 청산: ATR 트레일링 스톱 / MA50 하향 이탈
  - 전략 C 청산: VIX < 20 복귀 시 전량 매도 (트레일링 스톱 없음)
  - 전략 D 청산: ATR 트레일링 스톱 / MA50 하향 이탈

VIX 구간 (백테스트 근거)
  VIX < 20   정상     → A·B 허용
  VIX 20–25  스위트스팟 → A·B 허용 (A 최우수 구간)
  VIX 25–30  위험 구간  → A·B 차단 (최악 구간, 승률 19.6%)
  VIX > 30   공황 이후  → A 허용, B 차단, C 진입 시그널

운용
  거래량 이상 감지 (현재 거래량 / 20일 평균)
  Gemini AI 분석, Discord 웹훅 알림, QQQM DCA
"""

import json
import logging
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from google import genai

from candidate_finders import (
    find_candidates,
    find_crypto_candidates,
    find_momentum_candidates,
)
from config import (
    A_ATR_MULT, A_ATR_TIGHT, A_POSITION_PCT, A_RSI_BUY, A_RSI_PARTIAL,
    ADX_PERIOD, ADX_SIDEWAYS_THRESHOLD, ADX_TREND_THRESHOLD,
    B_ATR_MULT, B_POSITION_PCT, B_RANK_TOP,
    BASE_DIR,
    BREADTH_BEAR, BREADTH_BULL,
    C_POSITION_PCT, C_TICKERS,
    D_ATR_MULT, D_POSITION_PCT, D_RANK_TOP, D_TICKERS,
    DCA_BEAR, DCA_BULL, DCA_SIDEWAYS,
    EARNINGS_BLOCK_DAYS, EARNINGS_FILTER_ENABLE,
    HYG_MA_PERIOD,
    LOOKBACK_DAYS,
    NEWS_FILTER_ENABLE, NEWS_FILTER_TOP_N,
    QQQ_MA_PERIOD,
    VIX_C_EXIT, VIX_DANGER_LOW, VIX_PANIC, VIX_RV_HIGH, VIX_RV_LOW, VIX_SWEET_LOW,
)
from indicators import build_stock_data, compute_adx, compute_indicators, vol_ratio_of
from news_filter import filter_candidates_by_earnings, filter_candidates_by_sentiment
from notify import send_discord, strip_ansi
from portfolio_io import load_portfolio, save_portfolio
from universe import get_nasdaq100_tickers, get_sp500_tickers

# cron/launchd 환경에서 HOME 미설정 시 SQLite 캐시 오류 방지
yf.set_tz_cache_location(str(BASE_DIR / '.yf_cache'))

# ─── 초기화 ──────────────────────────────────────────────────────────────────
load_dotenv(BASE_DIR / '.env', override=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(BASE_DIR / 'scanner_v4.log', encoding='utf-8'),
    ],
)
log = logging.getLogger(__name__)

# ─── 메인 ────────────────────────────────────────────────────────────────────

def _run_risk_briefing() -> None:
    """스캔 직후 위험 브리핑 실행 — Gemini 유무와 무관, 실패해도 스캔 결과는 보존."""
    try:
        from risk_briefing import run_briefing
        print("\n📊 위험 브리핑 실행 중...")
        run_briefing()
    except Exception as e:
        log.warning(f"위험 브리핑 실패(스캔은 정상 완료): {e}")


def main() -> None:
    today = datetime.today().strftime('%Y-%m-%d')
    tomorrow = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')  # yfinance end는 exclusive
    start = (datetime.today() - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')

    log.info("=" * 60)
    log.info(f"Market Scanner v4  |  {today}")
    log.info("=" * 60)

    # ── 1. 티커 수집 ─────────────────────────────────────────────────────────
    log.info("티커 수집 중...")
    try:
        sp500  = get_sp500_tickers()
        ndx100 = get_nasdaq100_tickers()
    except Exception as e:
        log.error(f"티커 수집 실패: {e}")
        return

    holdings, portfolio_cash = load_portfolio()
    core_assets  = {t for t, pos in holdings.items() if pos.get('strategy') == 'Core'}
    port_tickers = list(holdings.keys())
    all_tickers  = list(set(sp500 + ndx100 + port_tickers + D_TICKERS + ['QQQ', 'HYG']))
    log.info(
        f"S&P500 {len(sp500)}  NDX100 {len(ndx100)}  "
        f"포트폴리오 {len(port_tickers)}  합계(중복제거) {len(all_tickers)}"
    )

    # ── 2. 데이터 다운로드 (단일 배치) ──────────────────────────────────────
    log.info(f"데이터 다운로드 중... ({start} ~ {today})")
    try:
        raw = yf.download(
            all_tickers,
            start=start, end=tomorrow,
            group_by='ticker',
            threads=True,
            progress=False,
        )
    except Exception as e:
        log.error(f"데이터 다운로드 실패: {e}")
        return

    # ── 3. 거시 필터: QQQ vs MA200 + ADX ─────────────────────────────────────
    bull_regime = True   # 하위 호환성용, 아래에서 재산출
    qqq_price = qqq_ma200 = adx_val = plus_di_val = minus_di_val = float('nan')

    def _load_qqq(qdf):
        nonlocal qqq_price, qqq_ma200, adx_val, plus_di_val, minus_di_val
        qdf = qdf[['High', 'Low', 'Close']].dropna()
        if len(qdf) < QQQ_MA_PERIOD:        # 배치 NaN/부족 → 예외 발생시켜 개별 재시도 유도
            raise ValueError(f"QQQ 데이터 부족 ({len(qdf)} < {QQQ_MA_PERIOD})")
        qqq_price = float(qdf['Close'].iloc[-1])
        qqq_ma200 = float(qdf['Close'].rolling(QQQ_MA_PERIOD).mean().iloc[-1])
        adx_val, plus_di_val, minus_di_val = compute_adx(qdf, ADX_PERIOD)

    try:
        _load_qqq(raw['QQQ'])
    except Exception as e:
        log.warning(f"QQQ 배치 실패 → 개별 다운로드 재시도: {e}")
        try:
            _load_qqq(yf.download('QQQ', period='400d', progress=False, multi_level_index=False))
            log.info(f"QQQ 개별 다운로드 성공: ${qqq_price:.2f}")
        except Exception as e2:
            log.error(f"QQQ 개별 다운로드도 실패 — 레짐 폴백 중립(SIDEWAYS): {e2}")

    # ── 3b. VIX 레벨 파악 (배치 다운로드 실패 방지 위해 개별 다운로드) ─────────
    vix_price = float('nan')
    try:
        vix_df    = yf.download('^VIX', period='5d', progress=False, multi_level_index=False)
        vix_price = float(vix_df['Close'].dropna().iloc[-1])
        log.info(f"VIX 개별 다운로드 완료: {vix_price:.2f}")
    except Exception as e:
        log.warning(f"VIX 데이터 로드 실패: {e}")

    # VIX 구간 판단 (백테스트 근거)
    if pd.isna(vix_price):                  # 데이터 없음 → 안전 기본값
        vix_zone        = 'NORMAL'
        allow_entry_a   = True
        allow_entry_b   = True
        vix_label       = "VIX 데이터 없음 (정상 가정, 신규 진입 허용)"
    elif vix_price <= VIX_SWEET_LOW:        # VIX < 20
        vix_zone        = 'NORMAL'
        allow_entry_a   = True
        allow_entry_b   = True
        vix_label       = f"정상 (VIX {vix_price:.1f} ≤ {VIX_SWEET_LOW})"
    elif vix_price <= VIX_DANGER_LOW:       # 20 < VIX ≤ 25
        vix_zone        = 'SWEET'
        allow_entry_a   = True
        allow_entry_b   = True
        vix_label       = f"스위트스팟 (VIX {vix_price:.1f}, 20–25 구간 최우수)"
    elif vix_price <= VIX_PANIC:            # 25 < VIX ≤ 30
        vix_zone        = 'DANGER'
        allow_entry_a   = False
        allow_entry_b   = False
        vix_label       = f"위험 구간 (VIX {vix_price:.1f}, 25–30 최악 구간 — 신규 전면 차단)"
    else:                                   # VIX > 30
        vix_zone        = 'PANIC'
        allow_entry_a   = True
        allow_entry_b   = False
        vix_label       = f"공황 이후 (VIX {vix_price:.1f} > {VIX_PANIC} — A만 허용)"

    log.info(f"VIX 필터: {vix_label}")

    # QQQ 실현변동성 (30일 연율화)
    realized_vol_pct = float('nan')
    vix_rv_ratio     = float('nan')
    try:
        qqq_close_s      = raw['QQQ']['Close'].dropna()
        log_returns      = np.log(qqq_close_s / qqq_close_s.shift(1)).dropna()
        realized_vol_pct = float(log_returns.iloc[-30:].std() * np.sqrt(252) * 100)
        if not pd.isna(vix_price) and realized_vol_pct > 0:
            vix_rv_ratio = round(vix_price / realized_vol_pct, 3)
    except Exception as e:
        log.warning(f"실현변동성 계산 실패: {e}")

    # ── 3c. HYG 크레딧 필터 ───────────────────────────────────────────────────
    hyg_ok       = True
    hyg_close    = float('nan')
    hyg_ma50_val = float('nan')
    try:
        hyg_s        = raw['HYG']['Close'].dropna()
        hyg_close    = float(hyg_s.iloc[-1])
        hyg_ma50_val = float(hyg_s.rolling(HYG_MA_PERIOD).mean().iloc[-1])
        hyg_ok       = hyg_close > hyg_ma50_val
    except Exception as e:
        log.warning(f"HYG 크레딧 필터 실패 (정상 가정): {e}")

    hyg_label = (
        f"HYG ${hyg_close:.2f} / MA{HYG_MA_PERIOD} ${hyg_ma50_val:.2f} → "
        f"{'정상' if hyg_ok else '⚠️ 신용 조건 악화'}"
        if not (pd.isna(hyg_close) or pd.isna(hyg_ma50_val))
        else "HYG 데이터 없음 (정상 가정)"
    )
    log.info(f"HYG 크레딧 필터: {hyg_label}")

    # VIX × HYG 복합 조정
    hyg_upgrade = vix_zone == 'SWEET' and not hyg_ok  # 스위트스팟이지만 신용 악화 → 위험 구간 상향
    if hyg_upgrade:
        allow_entry_a = False
        allow_entry_b = False
        log.info("HYG 악화 + VIX SWEET → DANGER 상향: 신규 진입 전면 차단")
    elif vix_zone == 'NORMAL' and not hyg_ok:
        log.info("HYG 악화 감지 (VIX 정상) → 포지션 사이즈 축소 권고 (진입은 유지)")

    # ── 4. 지표 계산 ─────────────────────────────────────────────────────────
    log.info("지표 계산 중...")
    stock_sp     = build_stock_data(raw, sp500)
    stock_ndx    = build_stock_data(raw, ndx100)
    stock_etc    = build_stock_data(raw, port_tickers)
    stock_crypto = build_stock_data(raw, D_TICKERS)
    log.info(f"방패 유니버스 {len(stock_sp)}개  창 유니버스 {len(stock_ndx)}개  크립토(D) {len(stock_crypto)}개")

    # ETH-USD 레짐 필터 (개별 다운로드)
    eth_close  = pd.Series(dtype=float)
    eth_regime = False
    eth_cur_p  = float('nan')
    eth_ma50_p = float('nan')
    try:
        eth_raw    = yf.download('ETH-USD', start=start, end=tomorrow, progress=False, multi_level_index=False)
        eth_close  = eth_raw['Close'].dropna()
        eth_cur_p  = float(eth_close.iloc[-1])
        eth_ma50_p = float(eth_close.rolling(50).mean().iloc[-1])
        eth_regime = eth_cur_p > eth_ma50_p
        log.info(
            f"ETH 레짐: {'✅ 허용' if eth_regime else '⛔ 차단'} "
            f"(${eth_cur_p:.0f} {'>' if eth_regime else '<='} MA50 ${eth_ma50_p:.0f})"
        )
    except Exception as e:
        log.warning(f"ETH 데이터 로드 실패 — 전략 D 비활성: {e}")

    # ── 포트폴리오 종목 배치 실패 시 개별 재시도 ─────────────────────────────
    missing_port = [t for t in port_tickers if t not in stock_etc and t not in stock_sp and t not in stock_ndx]
    if missing_port:
        log.info(f"포트폴리오 종목 개별 재다운로드: {missing_port}")
        for ticker in missing_port:
            try:
                raw_single = yf.download(
                    ticker,
                    start=start, end=tomorrow,
                    progress=False,
                    multi_level_index=False,
                )
                if raw_single is not None and len(raw_single) >= 210:
                    df = raw_single[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
                    df = compute_indicators(df)
                    if not df[['RSI', 'MA20', 'MA50', 'MA200', 'ATR']].iloc[-1].isna().any():
                        stock_etc[ticker] = df
                        log.info(f"{ticker} 개별 재다운로드 성공")
                    else:
                        log.warning(f"{ticker} 지표 계산 불완전 (데이터 부족)")
                else:
                    log.warning(f"{ticker} 개별 재다운로드 실패 (데이터 없음)")
            except Exception as e:
                log.warning(f"{ticker} 개별 재다운로드 오류: {e}")

    # ── 시장 폭: S&P500 종목 중 MA200 위 비율 ─────────────────────────────
    breadth_pct = float('nan')
    try:
        above = sum(1 for df in stock_sp.values() if float(df.iloc[-1]['Close']) > float(df.iloc[-1]['MA200']))
        breadth_pct = round(above / len(stock_sp) * 100, 1) if stock_sp else float('nan')
        log.info(f"시장 폭: {above}/{len(stock_sp)} = {breadth_pct:.1f}% above MA200")
    except Exception as e:
        log.warning(f"시장 폭 계산 실패: {e}")

    # ── 3-레이어 투표 → market_regime ────────────────────────────────────
    def _qqq_fallback():
        # QQQ 데이터 불명(NaN)이면 중립(SIDEWAYS) — 가짜 BEAR 방지.
        # (nan > nan == False라 예전엔 데이터 글리치가 무조건 BEAR로 떨어졌음)
        if pd.isna(qqq_price) or pd.isna(qqq_ma200):
            return 'SIDEWAYS'
        return 'BULL' if qqq_price > qqq_ma200 else 'BEAR'

    # Layer 1: ADX
    if not pd.isna(adx_val) and adx_val >= ADX_TREND_THRESHOLD:
        layer1_vote = 'BULL' if plus_di_val > minus_di_val else 'BEAR'
    elif not pd.isna(adx_val) and adx_val < ADX_SIDEWAYS_THRESHOLD:
        layer1_vote = 'SIDEWAYS'
    else:  # ADX 20–25 회색 지대: QQQ vs MA200 폴백
        layer1_vote = _qqq_fallback()

    # Layer 2: 시장 폭
    if pd.isna(breadth_pct):
        layer2_vote = _qqq_fallback()
    elif breadth_pct > BREADTH_BULL:
        layer2_vote = 'BULL'
    elif breadth_pct < BREADTH_BEAR:
        layer2_vote = 'BEAR'
    else:
        layer2_vote = 'SIDEWAYS'

    # Layer 3: VIX/RV
    if pd.isna(vix_rv_ratio):
        layer3_vote = _qqq_fallback()
    elif VIX_RV_LOW <= vix_rv_ratio <= VIX_RV_HIGH:
        layer3_vote = 'SIDEWAYS'
    else:
        layer3_vote = _qqq_fallback()

    # 다수결 (2-of-3)
    votes = [layer1_vote, layer2_vote, layer3_vote]
    if votes.count('SIDEWAYS') >= 2:
        market_regime = 'SIDEWAYS'
    elif votes.count('BULL') >= 2:
        market_regime = 'BULL'
    else:
        market_regime = 'BEAR'

    # 하위 호환성
    bull_regime = (market_regime == 'BULL')

    # 라벨 & DCA
    if market_regime == 'BULL':
        regime_label, dca_amount = "상승장 (QQQ > MA200)", DCA_BULL
    elif market_regime == 'BEAR':
        regime_label, dca_amount = "하락장 (QQQ < MA200)", DCA_BEAR
    else:
        regime_label, dca_amount = "횡보장 (Sideways)", DCA_SIDEWAYS

    log.info(
        f"시장 국면: {market_regime}  투표=({layer1_vote}/{layer2_vote}/{layer3_vote})"
        f"  ADX={adx_val:.1f}  DI+={plus_di_val:.1f}  DI-={minus_di_val:.1f}"
        f"  폭={breadth_pct:.1f}%  VIX/RV={vix_rv_ratio:.2f}"
    )

    def get_df(ticker: str) -> pd.DataFrame | None:
        """S&P500 → NDX100 → 기타 → 크립토 순서로 조회"""
        for store in (stock_sp, stock_ndx, stock_etc, stock_crypto):
            if ticker in store:
                return store[ticker]
        return None

    # ── 5. Phase 2: 포트폴리오 모니터 (전략별 로직 분리) ───────────────────
    port_rows:    list[dict] = []
    stop_updates: list[str] = []

    for ticker, pos in holdings.items():
        df = get_df(ticker)
        if df is None:
            port_rows.append({'ticker': ticker, 'signal': 'NO_DATA', 'strategy': 'N/A'})
            continue

        row   = df.iloc[-1]
        close = float(row['Close'])
        atr   = float(row['ATR'])
        rsi   = float(row['RSI'])
        ma20  = float(row['MA20'])
        ma50  = float(row['MA50'])
        vr    = vol_ratio_of(row)
        
        strategy_type = pos.get('strategy')
        if not strategy_type:
            log.warning(f"{ticker}에 strategy 태그 없음. 'A'(방패)로 간주합니다.")
            strategy_type = 'A'
            pos['strategy'] = 'A'

        buy_price = pos.get('buy_price', close)
        pnl_pct   = (close - buy_price) / buy_price * 100
        signal    = 'HOLD'

        # 전략 C: 트레일링 스톱 없음, VIX 기반 청산만 적용
        if strategy_type == 'C':
            if not pd.isna(vix_price) and vix_price < VIX_C_EXIT:
                signal = 'C_EXIT'
            port_rows.append({
                'ticker': ticker, 'strategy': 'C', 'close': close,
                'pnl_pct': round(pnl_pct, 1), 'rsi': round(rsi, 1), 'vol_ratio': vr,
                'trailing_stop': float('nan'), 'ma20': round(ma20, 2),
                'ma50': round(ma50, 2), 'signal': signal,
                'vix_entry': pos.get('vix_entry', float('nan')),
            })
            continue

        old_stop = pos.get('trailing_stop') or -999
        if ticker not in core_assets:
            if strategy_type == 'A':
                mult = A_ATR_TIGHT if rsi >= A_RSI_PARTIAL else A_ATR_MULT
            elif strategy_type == 'B':
                mult = B_ATR_MULT
            else:  # D
                mult = D_ATR_MULT

            new_stop = round(close - atr * mult, 2)
            if new_stop > old_stop:
                pos['trailing_stop'] = new_stop
                stop_updates.append(f"({pos['strategy']}) {ticker}  ${old_stop:.2f} → ${new_stop:.2f}")

        trailing_stop = pos.get('trailing_stop') or -999

        if ticker in core_assets:
            signal = 'CORE'
        elif close <= trailing_stop:
            signal = 'STOP'
        else:
            if strategy_type == 'A':
                if not pos.get('tp1_hit', False) and rsi >= A_RSI_PARTIAL:
                    signal = 'TP1'          # RSI 50 도달 → 50% 매도
                    pos['tp1_hit'] = True
                elif pos.get('tp1_hit', False) and close >= ma20:
                    signal = 'TP2'          # MA20 도달 → 나머지 전량 매도
            elif strategy_type in ('B', 'D'):
                if close < ma50:
                    signal = 'MA_CROSS'

        port_rows.append({
            'ticker': ticker, 'strategy': strategy_type, 'close': close,
            'pnl_pct': round(pnl_pct, 1), 'rsi': round(rsi, 1), 'vol_ratio': vr,
            'trailing_stop': trailing_stop, 'ma20': round(ma20, 2),
            'ma50': round(ma50, 2), 'signal': signal,
        })

    save_portfolio(holdings, portfolio_cash)
    if stop_updates:
        log.info("방어선 갱신:\n  " + "\n  ".join(stop_updates))

    # ── 6. Phase 1: 유니버스 스캔 ────────────────────────────────────────────
    entry_a, entry_b, entry_c, entry_d = [], [], [], []
    rules_a = {'rsi_cond': 'lt', 'rsi_val': A_RSI_BUY, 'ma_cond': 'lt', 'atr_mult': A_ATR_MULT}
    qqq_close = raw['QQQ']['Close'].dropna()
    if market_regime == 'BULL':
        if allow_entry_a:
            entry_a = find_candidates(stock_sp, holdings, rules_a, 'rsi', False)
        else:
            log.info(f"방패(A) 신규 진입 차단 — VIX 위험 구간 ({vix_price:.1f})")
        if allow_entry_b:
            entry_b = find_momentum_candidates(stock_ndx, holdings, qqq_close)
        else:
            log.info(f"창(B) 신규 진입 차단 — VIX 위험/공황 구간 ({vix_price:.1f})")
    elif market_regime == 'SIDEWAYS':
        if allow_entry_a:
            entry_a = find_candidates(stock_sp, holdings, rules_a, 'rsi', False)
        log.info(f"횡보장 — 방패(A) 허용 ({len(entry_a)}개), 창(B) 차단 (모멘텀 비효율)")
    else:
        log.info("하락장 — 신규 진입 전략 차단")

    # 전략 C: VIX > 30 공황 구간에서 지수 ETF 진입 제안 (bull/bear 무관)
    if vix_zone == 'PANIC':
        for ticker in C_TICKERS:
            existing = holdings.get(ticker, {})
            if existing.get('strategy') == 'C':
                continue  # 이미 C 포지션 보유 중
            try:
                c_close = float(raw[ticker]['Close'].dropna().iloc[-1])
                suggested_amount = portfolio_cash * C_POSITION_PCT / 100
                suggested_shares = max(1, int(suggested_amount / c_close))
                entry_c.append({
                    'ticker':           ticker,
                    'close':            round(c_close, 2),
                    'suggested_amount': round(suggested_amount, 0),
                    'suggested_shares': suggested_shares,
                    'exit_trigger':     f'VIX < {VIX_C_EXIT:.0f}',
                })
            except Exception:
                pass
        log.info(f"전략 C 후보 {len(entry_c)}개 (VIX {vix_price:.1f})")

    # 전략 D: ETH>MA50 레짐 + VIX≤30 (시장 국면 무관)
    if eth_regime:
        entry_d = find_crypto_candidates(eth_close, vix_price, stock_crypto, holdings)
        log.info(f"크립토(D) 후보 {len(entry_d)}개 (ETH ✅ MA50)")
    else:
        log.info(f"크립토(D) ETH 레짐 차단 (${eth_cur_p:.0f} ≤ MA50 ${eth_ma50_p:.0f})")

    log.info(
        f"방패 후보 {len(entry_a)}개  창 후보 {len(entry_b)}개  "
        f"지수 후보 {len(entry_c)}개  크립토(D) 후보 {len(entry_d)}개  | 포트폴리오 {len(port_rows)}개"
    )

    # ── 6a. 어닝스 캘린더 필터 ────────────────────────────────────────────────
    # 어닝스 ±N일 이내 종목은 갭 리스크로 RSI 신호가 거짓되기 쉬움 → 차단
    _earn_blocked_all: list[dict] = []
    if EARNINGS_FILTER_ENABLE and (entry_a or entry_b or entry_d):
        _earn_t0 = datetime.now()
        log.info(f"어닝스 캘린더 필터 시작 (±{EARNINGS_BLOCK_DAYS}일)")
        try:
            entry_a, _eb_a = filter_candidates_by_earnings(
                entry_a, days=EARNINGS_BLOCK_DAYS, logger=log,
            )
            entry_b, _eb_b = filter_candidates_by_earnings(
                entry_b, days=EARNINGS_BLOCK_DAYS, logger=log,
            )
            entry_d, _eb_d = filter_candidates_by_earnings(
                entry_d, days=EARNINGS_BLOCK_DAYS, logger=log,
            )
            _earn_blocked_all = _eb_a + _eb_b + _eb_d
        except Exception as _e:
            log.warning(f"어닝스 필터 오류 (스킵): {_e}")
        _earn_elapsed = (datetime.now() - _earn_t0).total_seconds()
        log.info(
            f"어닝스 캘린더 필터 완료 — {_earn_elapsed:.1f}s 소요 "
            f"| 차단 {len(_earn_blocked_all)}개"
        )

    # ── 6b. 뉴스 감성 필터 ────────────────────────────────────────────────────
    _skipped_all: list[dict] = []
    if NEWS_FILTER_ENABLE and (entry_a or entry_b or entry_d):
        _news_api_key = os.getenv('GEMINI_API_KEY')
        if _news_api_key:
            _news_t0 = datetime.now()
            log.info("뉴스 감성 필터 시작")
            _skipped_a: list[dict] = []
            _skipped_b: list[dict] = []
            _skipped_d: list[dict] = []
            try:
                _news_client = genai.Client(
                    api_key=_news_api_key, http_options={'timeout': 20_000}
                )
                _news_model = 'gemini-2.5-flash'
                entry_a, _skipped_a = filter_candidates_by_sentiment(
                    entry_a, _news_client, _news_model,
                    top_n=NEWS_FILTER_TOP_N, logger=log,
                )
                entry_b, _skipped_b = filter_candidates_by_sentiment(
                    entry_b, _news_client, _news_model,
                    top_n=NEWS_FILTER_TOP_N, logger=log,
                )
                entry_d, _skipped_d = filter_candidates_by_sentiment(
                    entry_d, _news_client, _news_model,
                    top_n=NEWS_FILTER_TOP_N, logger=log,
                )
            except Exception as _e:
                log.warning(f"뉴스 감성 필터 오류 (스킵): {_e}")
            _skipped_all = _skipped_a + _skipped_b + _skipped_d
            _news_elapsed = (datetime.now() - _news_t0).total_seconds()
            log.info(
                f"뉴스 감성 필터 완료 — {_news_elapsed:.1f}s 소요 "
                f"| SKIP {len(_skipped_all)}개 제거"
            )
        else:
            log.info("GEMINI_API_KEY 없음 — 뉴스 감성 필터 스킵")

    # ── 6c. 포지션 사이징 ─────────────────────────────────────────────────────
    for c in entry_a:
        amt = portfolio_cash * A_POSITION_PCT / 100
        c['suggested_amount'] = round(amt, 0)
        c['suggested_shares'] = max(1, int(amt / c['close'])) if c['close'] > 0 else 1
    for c in entry_b:
        amt = portfolio_cash * B_POSITION_PCT / 100
        c['suggested_amount'] = round(amt, 0)
        c['suggested_shares'] = max(1, int(amt / c['close'])) if c['close'] > 0 else 1
    for c in entry_d:
        amt = portfolio_cash * D_POSITION_PCT / 100
        c['suggested_amount'] = round(amt, 0)
        c['suggested_shares'] = max(1, int(amt / c['close'])) if c['close'] > 0 else 1

    # ── 6d. 종합 점수 & TOP PICK 선정 ─────────────────────────────────────────
    # 전략 A: RSI 낮을수록(40%) + 감성 높을수록(30%) + MA20 업사이드 클수록(30%) - 거래량 급등 패널티
    def _score_a(c: dict) -> float:
        rsi_s  = (A_RSI_BUY - c['rsi']) / A_RSI_BUY
        sent_s = (c.get('sentiment') or {}).get('score', 0.5)
        upside = max((c['ma20'] - c['close']) / c['close'], 0) * 5   # 5배 스케일
        vol_pen = 0.1 if c['vol_ratio'] >= 1.5 else 0.0
        return round(rsi_s * 0.4 + sent_s * 0.3 + upside * 0.3 - vol_pen, 4)

    # 전략 B/D: 6M 랭크(40%) + RS 초과수익(30%) + 감성(30%) - 거래량 급등 패널티
    def _score_bd(c: dict, rs_key: str) -> float:
        rank_s = c.get('rank_pct', 50) / 100
        rs_s   = min(max(c.get(rs_key, 0) / 20, 0), 1.0)
        sent_s = (c.get('sentiment') or {}).get('score', 0.5)
        vol_pen = 0.1 if c['vol_ratio'] >= 1.5 else 0.0
        return round(rank_s * 0.4 + rs_s * 0.3 + sent_s * 0.3 - vol_pen, 4)

    for c in entry_a:
        c['score'] = _score_a(c)
    for c in entry_b:
        c['score'] = _score_bd(c, 'rs_vs_qqq')
    for c in entry_d:
        c['score'] = _score_bd(c, 'rs_vs_eth')

    entry_a.sort(key=lambda x: x['score'], reverse=True)
    entry_b.sort(key=lambda x: x['score'], reverse=True)
    entry_d.sort(key=lambda x: x['score'], reverse=True)

    # ── 7. 결과 포맷 ─────────────────────────────────────────────────────────
    W   = 70
    SEP = "─" * W

    out = []
    out.append("=" * W)
    out.append(f"  📊 Market Scanner v4  |  {today}")
    out.append("=" * W)

    regime_icon = {"BULL": "📈", "BEAR": "📉", "SIDEWAYS": "↔️"}[market_regime]
    dca_note    = {"BULL": "(기본)", "BEAR": "⬆ 5배 (하락장)", "SIDEWAYS": "⬌ 중간 (횡보장)"}[market_regime]

    out.append(f"\n  {regime_icon} 시장 국면 : {regime_label}")
    out.append(f"     QQQ ${qqq_price:.2f}  /  MA200 ${qqq_ma200:.2f}")
    # 3-레이어 상세
    adx_str = f"{adx_val:.1f}" if not pd.isna(adx_val) else "N/A"
    di_str  = f"DI+ {plus_di_val:.1f} / DI- {minus_di_val:.1f}" if not pd.isna(plus_di_val) else "N/A"
    brd_str = f"{breadth_pct:.1f}%" if not pd.isna(breadth_pct) else "N/A"
    rv_str  = f"{vix_rv_ratio:.2f}" if not pd.isna(vix_rv_ratio) else "N/A"
    out.append(f"     ADX {adx_str} ({di_str})  |  폭 {brd_str}  |  VIX/RV {rv_str}")
    out.append(f"     투표: Layer1={layer1_vote}  Layer2={layer2_vote}  Layer3={layer3_vote}  → {market_regime}")
    out.append(f"  💰 QQQM DCA : ${dca_amount:.0f}/일  {dca_note}")
    if market_regime == 'BEAR':
        out.append("     ※ 신규 매수 전면 차단 — 현금 보존 모드")
    elif market_regime == 'SIDEWAYS':
        out.append("     ※ 횡보장 — 방패(A) 평균회귀 허용 / 창(B) 모멘텀 차단")

    VIX_ZONE_ICON = {'NORMAL': '🟢', 'SWEET': '✨', 'DANGER': '🔴', 'PANIC': '🟠'}
    out.append(f"  {VIX_ZONE_ICON[vix_zone]} VIX 필터  : {vix_label}")
    if vix_zone == 'DANGER':
        out.append("     ※ VIX 25–30 = 공포 확산 중 (백테스트 최악 구간) → A·B 신규 전면 차단")
    elif vix_zone == 'PANIC':
        out.append("     ※ VIX > 30 = 공황 클라이맥스 → 방패(A) 반등 매수 재개, 창(B) 차단 유지")
    HYG_ICON = '🟢' if hyg_ok else '🔴'
    out.append(f"  {HYG_ICON} HYG 크레딧: {hyg_label}")
    if hyg_upgrade:
        out.append("     ※ HYG < MA50 + VIX 20–25 → DANGER 상향: 신규 진입 전면 차단")
    elif not hyg_ok and vix_zone == 'NORMAL':
        out.append("     ※ HYG < MA50 (VIX 정상) → 포지션 사이즈 축소 권고")

    # ETH 레짐 (전략 D)
    if not pd.isna(eth_cur_p):
        eth_icon = '🟢' if eth_regime else '🔴'
        eth_label = (
            f"ETH ${eth_cur_p:.0f} > MA50 ${eth_ma50_p:.0f} → D전략 허용"
            if eth_regime else
            f"ETH ${eth_cur_p:.0f} ≤ MA50 ${eth_ma50_p:.0f} → D전략 차단"
        )
        out.append(f"  {eth_icon} ETH 레짐  : {eth_label}")

    SIGNAL_LABEL = {
        'STOP':     '🔴 [STOP]     방어선 붕괴 → 즉시 매도',
        'TP1':      '🟡 [TP1/A]    RSI ≥ 50   → 50% 익절',
        'TP2':      '🟢 [TP2/A]    MA20 도달  → 전량 익절',
        'MA_CROSS': '🔵 [EXIT/B·D] MA50 이탈  → 전량 익절',
        'C_EXIT':   '🏁 [EXIT/C]   VIX < 20   → 전량 익절 (공황 정상화)',
        'CORE':     '⭐ [CORE]     장기 보유',
        'HOLD':     '   [HOLD]',
        'NO_DATA':  '⚪ 데이터 없음',
    }

    out.append(f"\n{SEP}")
    out.append("  📂 포트폴리오 현황")
    out.append(SEP)
    out.append(f"  {'종목(전략)':<10} {'현재가':>8} {'수익률':>7} {'RSI':>6} {'거래량':>6}  {'스톱':>9}  신호")
    out.append(f"  {'─'*10} {'─'*8} {'─'*7} {'─'*6} {'─'*6}  {'─'*9}  {'─'*24}")

    port_rows.sort(key=lambda x: (x.get('strategy', 'Z'), x.get('ticker', '')))
    for p in port_rows:
        if p['signal'] == 'NO_DATA':
            out.append(f"  {p['ticker']:<10}  {'데이터 없음'}")
            continue

        ticker_strat = f"{p['ticker']}({p['strategy']})"
        pnl_sign = "+" if p['pnl_pct'] >= 0 else ""
        vr_flag  = "🔺" if p['vol_ratio'] >= 1.5 else "  "

        if p['strategy'] == 'C':
            vix_entry_str = f"진입VIX {p['vix_entry']:.1f}" if not pd.isna(p.get('vix_entry', float('nan'))) else ""
            out.append(
                f"  {ticker_strat:<10} ${p['close']:>7.2f} {pnl_sign}{p['pnl_pct']:>5.1f}%"
                f" {p['rsi']:>6.1f} {vr_flag}{p['vol_ratio']:>3.1f}x"
                f"  {'스톱없음':>9}  {SIGNAL_LABEL.get(p['signal'], p['signal'])}"
                + (f"  ({vix_entry_str})" if vix_entry_str else "")
            )
        else:
            out.append(
                f"  {ticker_strat:<10} ${p['close']:>7.2f} {pnl_sign}{p['pnl_pct']:>5.1f}%"
                f" {p['rsi']:>6.1f} {vr_flag}{p['vol_ratio']:>3.1f}x"
                f"  ${p['trailing_stop']:>8.2f}  {SIGNAL_LABEL.get(p['signal'], p['signal'])}"
            )

    if stop_updates:
        out.append(f"\n  ↑ 방어선 자동 갱신 (ATR 기반):")
        for u in stop_updates:
            out.append(f"      {u}")

    out.append(f"\n{SEP}")
    regime_header = {"BULL": "  🟢 신규 진입 후보", "BEAR": "  ⛔ 신규 진입 후보 (차단 중)", "SIDEWAYS": "  ↔️ 신규 진입 후보 (횡보장)"}[market_regime]
    out.append(regime_header)
    out.append(SEP)

    def candidate_table(candidates: list[dict], header: str, strategy: str = 'A') -> None:
        if not candidates:
            out.append(f"  {header}  조건 충족 종목 없음")
            return
        out.append(f"  {header}  ({len(candidates)}개)")
        if strategy == 'A':
            out.append(f"  {'':2} {'종목':<7} {'현재가':>8} {'RSI':>6} {'거래량':>6}  {'제안금액':>10} {'주수':>5}  {'손절':>9}  {'TP1(RSI50)':>10}  {'TP2(MA20)':<10}  {'점수':>6}  감성")
            out.append(f"  {'':2} {'─'*7} {'─'*8} {'─'*6} {'─'*6}  {'─'*10} {'─'*5}  {'─'*9}  {'─'*9}  {'─'*10}  {'─'*6}  {'─'*7}")
        elif strategy == 'D':
            out.append(f"  {'':2} {'종목':<7} {'현재가':>8} {'6M랭크':>7} {'RS vs ETH':>10} {'거래량':>6}  {'제안금액':>10} {'주수':>5}  {'손절':>9}  {'청산(MA50)':>10}  {'점수':>6}  감성")
            out.append(f"  {'':2} {'─'*7} {'─'*8} {'─'*7} {'─'*10} {'─'*6}  {'─'*10} {'─'*5}  {'─'*9}  {'─'*10}  {'─'*6}  {'─'*7}")
        else:  # B
            out.append(f"  {'':2} {'종목':<7} {'현재가':>8} {'6M랭크':>7} {'RS vs QQQ':>10} {'거래량':>6}  {'제안금액':>10} {'주수':>5}  {'손절':>9}  {'청산(MA50)':>10}  {'점수':>6}  감성")
            out.append(f"  {'':2} {'─'*7} {'─'*8} {'─'*7} {'─'*10} {'─'*6}  {'─'*10} {'─'*5}  {'─'*9}  {'─'*10}  {'─'*6}  {'─'*7}")
        for i, c in enumerate(candidates):
            vr_flag  = "🔺" if c['vol_ratio'] >= 1.5 else "  "
            sent     = c.get('sentiment') or {}
            sent_icon = '🔶' if sent.get('verdict') == 'REDUCE' else '🟢'
            sent_str = f"{sent_icon}{sent.get('score', 0.5):.2f}"
            amt_str  = f"${c.get('suggested_amount', 0):>9,.0f}" if c.get('suggested_amount') else f"{'─':>10}"
            shr_str  = f"{c.get('suggested_shares', 0):>4}주" if c.get('suggested_shares') else f"{'─':>5}"
            score_str = f"{c.get('score', 0):.3f}"
            top_mark  = "⭐" if i == 0 else "  "
            if strategy == 'A':
                out.append(
                    f"  {top_mark} {c['ticker']:<7} ${c['close']:>7.2f} {c['rsi']:>6.1f}"
                    f" {vr_flag}{c['vol_ratio']:>3.1f}x  {amt_str} {shr_str}  ${c['stop']:>8.2f}  ${c['ma20']:>8.2f}  {'MA20 도달':<10}  {score_str}  {sent_str}"
                )
            elif strategy == 'D':
                out.append(
                    f"  {top_mark} {c['ticker']:<7} ${c['close']:>7.2f} {c.get('rank_pct', 0):>6.1f}%"
                    f" {c.get('rs_vs_eth', 0):>+9.2f}%"
                    f" {vr_flag}{c['vol_ratio']:>3.1f}x  {amt_str} {shr_str}  ${c['stop']:>8.2f}  ${c['ma50']:>9.2f}  {score_str}  {sent_str}"
                )
            else:  # B
                out.append(
                    f"  {top_mark} {c['ticker']:<7} ${c['close']:>7.2f} {c.get('rank_pct', 0):>6.1f}%"
                    f" {c.get('rs_vs_qqq', 0):>+9.2f}%"
                    f" {vr_flag}{c['vol_ratio']:>3.1f}x  {amt_str} {shr_str}  ${c['stop']:>8.2f}  ${c['ma50']:>9.2f}  {score_str}  {sent_str}"
                )

    if market_regime == 'BULL':
        candidate_table(entry_a, f"[방패 A — S&P500 평균회귀]  RSI < {A_RSI_BUY}  Close < MA20  Close > MA200", 'A')
        out.append("")
        candidate_table(entry_b, f"[창 B — NDX100 모멘텀]  6M 상위 {int(B_RANK_TOP*100)}%  QQQ 아웃퍼폼  Close > MA20/MA200", 'B')
    elif market_regime == 'SIDEWAYS':
        candidate_table(entry_a, f"[방패 A — S&P500 평균회귀]  RSI < {A_RSI_BUY}  Close < MA20  Close > MA200 (횡보장 허용)", 'A')
        out.append("  [창 B — NDX100 모멘텀]  횡보장 차단 (모멘텀 비효율)")
    else:
        out.append("  (하락장: 모든 신규 매수 차단)")

    # 전략 D 섹션 (ETH 레짐 여부와 무관하게 항상 표시)
    out.append("")
    if eth_regime:
        d_header = (
            f"[크립토 D — ETH>MA50 ✅]  6M 상위 {int(D_RANK_TOP*100)}%  ETH 아웃퍼폼  Close>MA20  VIX≤30"
        )
        candidate_table(entry_d, d_header, 'D')
    else:
        out.append(f"  [크립토 D — ETH 레짐 ⛔]  차단 (ETH ${eth_cur_p:.0f} ≤ MA50 ${eth_ma50_p:.0f})")

    # TOP PICK 요약
    top_picks = []
    if entry_a:
        top_picks.append(f"방패(A) ⭐ {entry_a[0]['ticker']} (점수 {entry_a[0].get('score', 0):.3f})")
    if entry_b:
        top_picks.append(f"창(B)   ⭐ {entry_b[0]['ticker']} (점수 {entry_b[0].get('score', 0):.3f})")
    if entry_d:
        top_picks.append(f"크립토(D) ⭐ {entry_d[0]['ticker']} (점수 {entry_d[0].get('score', 0):.3f})")
    if top_picks:
        out.append("")
        out.append("  🏆 전략별 TOP PICK (RSI낮음 40% + 감성 30% + 업사이드 30% - 거래량급등 패널티)")
        for tp in top_picks:
            out.append(f"     {tp}")

    # 감성 범례 (REDUCE 또는 SKIP이 있을 때만)
    _has_reduce = any(
        c.get('sentiment', {}).get('verdict') == 'REDUCE'
        for c in entry_a + entry_b + entry_d
    )
    if _has_reduce or _skipped_all:
        out.append("")
        if _has_reduce:
            out.append("  🟢 PASS(중립/긍정)  🔶 REDUCE(단기 불확실성, 주의)")
        if _skipped_all:
            out.append(f"  ⛔ SKIP 제거: {', '.join(c['ticker'] for c in _skipped_all)}")

    # 전략 C 섹션 (VIX > 30 시에만 표시)
    if entry_c:
        out.append(f"\n{SEP}")
        out.append(f"  🚨 전략 C — VIX 공황 지수 매수  (VIX {vix_price:.1f} > 30)")
        out.append(SEP)
        out.append(f"  백테스트: SPY 96% 승률 / 평균 +11.5% / Sharpe 1.21  (VIX<20 복귀 시 청산, 평균 88일)")
        out.append(f"  {'종목':<6} {'현재가':>8} {'제안금액':>10} {'주수':>6}  청산 기준")
        out.append(f"  {'─'*6} {'─'*8} {'─'*10} {'─'*6}  {'─'*16}")
        for c in entry_c:
            out.append(
                f"  {c['ticker']:<6} ${c['close']:>7.2f} ${c['suggested_amount']:>9,.0f}"
                f" {c['suggested_shares']:>5}주  {c['exit_trigger']}"
            )
        out.append(f"  ※ portfolio.json에 strategy: 'C', vix_entry: {vix_price:.1f} 로 기록하세요")

    out.append("=" * W)
    scan_text = "\n".join(out)

    # ── signals.json 내보내기 ─────────────────────────────────────────────────
    _exit_signals = {'STOP', 'TP1', 'TP2', 'MA_CROSS', 'C_EXIT'}
    signals_data  = {
        'date': today,
        'regime': {
            'bull':          bull_regime,
            'market_regime': market_regime,
            'votes':         {'layer1_adx': layer1_vote, 'layer2_breadth': layer2_vote, 'layer3_vix_rv': layer3_vote},
            'adx':           round(adx_val, 1)        if not pd.isna(adx_val)       else None,
            'plus_di':       round(plus_di_val, 1)    if not pd.isna(plus_di_val)   else None,
            'minus_di':      round(minus_di_val, 1)   if not pd.isna(minus_di_val)  else None,
            'breadth_pct':   round(breadth_pct, 1)    if not pd.isna(breadth_pct)   else None,
            'vix_rv_ratio':  vix_rv_ratio             if not pd.isna(vix_rv_ratio)  else None,
            'vix':           round(vix_price, 2) if not pd.isna(vix_price) else None,
            'vix_zone':      vix_zone,
            'hyg_ok':        hyg_ok,
            'hyg_close':     round(hyg_close, 2) if not pd.isna(hyg_close) else None,
            'hyg_ma50':      round(hyg_ma50_val, 2) if not pd.isna(hyg_ma50_val) else None,
            'allow_entry_a': allow_entry_a,
            'allow_entry_b': allow_entry_b,
        },
        'portfolio_cash': portfolio_cash,
        'exits': [
            {
                'ticker':        p['ticker'],
                'signal':        p['signal'],
                'strategy':      p.get('strategy', 'A'),
                'shares':        holdings.get(p['ticker'], {}).get('shares', 0),
                'trailing_stop': p.get('trailing_stop'),
            }
            for p in port_rows if p.get('signal') in _exit_signals
        ],
        'entries': {
            'A': entry_a[:3],
            'B': entry_b[:3],
            'C': entry_c,
            'D': entry_d[:3],
        },
        'eth_regime': {
            'active':    eth_regime,
            'eth_price': round(eth_cur_p, 2) if not pd.isna(eth_cur_p) else None,
            'eth_ma50':  round(eth_ma50_p, 2) if not pd.isna(eth_ma50_p) else None,
        },
    }
    try:
        with open(BASE_DIR / 'signals.json', 'w', encoding='utf-8') as f:
            json.dump(signals_data, f, indent=2, ensure_ascii=False, default=str)
        log.info(
            f"signals.json 저장: 청산 {len(signals_data['exits'])}건 "
            f"| 진입 A {len(entry_a)}건 B {len(entry_b)}건 D {len(entry_d)}건"
        )
    except Exception as e:
        log.error(f"signals.json 저장 실패: {e}")

    print(scan_text)
    log.info(f"\n{scan_text}")

    # ── 8. Gemini AI 분석 ────────────────────────────────────────────────────
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key or not NEWS_FILTER_ENABLE:
        log.info("Gemini 비활성(NEWS_FILTER_ENABLE=False 또는 키 없음) — AI 분석 스킵")
        send_discord(scan_text)
        _run_risk_briefing()
        return

    print(f"\n{'='*70}")
    print("🤖 Gemini AI 분석 중...")
    print(f"{'='*70}")

    # 포트폴리오 총 자산 계산 (포지션 사이징용)
    holdings_value = sum(
        p['close'] * holdings[p['ticker']].get('shares', 0)
        for p in port_rows
        if p['signal'] != 'NO_DATA' and p['ticker'] in holdings
    )
    total_portfolio_value = holdings_value + portfolio_cash
    active_a = sum(1 for p in port_rows if p.get('strategy') == 'A' and p['signal'] not in ('STOP', 'TP2', 'NO_DATA'))
    active_b = sum(1 for p in port_rows if p.get('strategy') == 'B' and p['signal'] not in ('STOP', 'MA_CROSS', 'NO_DATA'))
    active_c = sum(1 for p in port_rows if p.get('strategy') == 'C' and p['signal'] not in ('C_EXIT', 'NO_DATA'))
    active_d = sum(1 for p in port_rows if p.get('strategy') == 'D' and p['signal'] not in ('STOP', 'MA_CROSS', 'NO_DATA'))

    my_persona      = os.getenv('MY_PERSONA', '')
    port_summary_a, port_summary_b, port_summary_c, port_summary_d = [], [], [], []
    for p in port_rows:
        if p['signal'] == 'NO_DATA': continue
        strategy_t = p.get('strategy')
        summary_line = (f"  {p['ticker']}: ${p.get('close', 0):.2f} PNL {p.get('pnl_pct', 0):+.1f}% "
                        f"RSI {p.get('rsi', '-')} Stop ${p.get('trailing_stop', 0):.2f} [{p['signal']}]")
        if strategy_t == 'C':
            port_summary_c.append(
                f"  {p['ticker']}: ${p.get('close', 0):.2f} PNL {p.get('pnl_pct', 0):+.1f}% "
                f"RSI {p.get('rsi', '-')} [{p['signal']}]"
            )
        elif strategy_t == 'D':
            port_summary_d.append(summary_line)
        elif strategy_t == 'B':
            port_summary_b.append(summary_line)
        else:
            port_summary_a.append(summary_line)

    entry_a_summary = "\n".join(
        f"  {c['ticker']}: 현재 ${c['close']:.2f} RSI {c['rsi']}"
        f" | 제안 ${c.get('suggested_amount', 0):,.0f} ({c.get('suggested_shares', 0)}주)"
        f" | 손절 ${c['stop']:.2f} | TP1(MA20) ${c['ma20']:.2f}"
        f" | 감성 {c['sentiment']['verdict'] if c.get('sentiment') else 'N/A'}"
        for c in entry_a[:15]
    ) or "  없음"
    entry_b_summary = "\n".join(
        f"  {c['ticker']}: 현재 ${c['close']:.2f} RSI {c['rsi']}"
        f" | 제안 ${c.get('suggested_amount', 0):,.0f} ({c.get('suggested_shares', 0)}주)"
        f" | 손절 ${c['stop']:.2f} | 청산(MA50) ${c['ma50']:.2f}"
        f" | 감성 {c['sentiment']['verdict'] if c.get('sentiment') else 'N/A'}"
        for c in entry_b[:15]
    ) or "  없음"
    entry_d_summary = "\n".join(
        f"  {c['ticker']}: 현재 ${c['close']:.2f} RSI {c['rsi']}"
        f" | 제안 ${c.get('suggested_amount', 0):,.0f} ({c.get('suggested_shares', 0)}주)"
        f" | 손절 ${c['stop']:.2f} | 청산(MA50) ${c['ma50']:.2f}"
        f" | ETH RS {c.get('rs_vs_eth', 0):+.1f}% | 감성 {c['sentiment']['verdict'] if c.get('sentiment') else 'N/A'}"
        for c in entry_d[:6]
    ) or "  없음 (ETH 레짐 차단 또는 조건 미충족)"
    _skipped_summary = ", ".join(
        f"{c['ticker']}({c.get('sentiment', {}).get('summary', '')})"
        for c in _skipped_all
    ) or "없음"

    # 진입 허용 여부에 따라 후보 섹션 구성
    entry_section = ""
    if allow_entry_a or allow_entry_b or entry_d:
        entry_section = f"""
[신규 진입 후보]
- 방패(A) 후보 (RSI 낮은 순):
{entry_a_summary}
- 창(B) 후보 (6M 랭크 순):
{entry_b_summary}
- 크립토(D) 후보 (ETH 레짐 {'✅' if eth_regime else '⛔'}, ETH ${eth_cur_p:.0f}):
{entry_d_summary}
뉴스 악재로 제거된 종목: {_skipped_summary}
"""
    else:
        entry_section = "[신규 진입] 현재 전면 차단 — 진입 후보 분석 생략"

    prompt = f"""{my_persona}
너는 내 퀀트 포트폴리오를 매일 점검해주는 분석가야.
아래 데이터를 보고 딱 3개 섹션으로 간결하게 답해줘. 군더더기 없이.

--- 시장 데이터 ---
시장 국면: {market_regime}  (투표: ADX={layer1_vote} / 폭={layer2_vote} / VIX-RV={layer3_vote})
QQQ: ${qqq_price:.2f} / MA200: ${qqq_ma200:.2f}
ADX(14): {adx_val:.1f}  DI+: {plus_di_val:.1f}  DI-: {minus_di_val:.1f}
S&P500 시장 폭: {breadth_pct:.1f}% (MA200 상회)
VIX: {vix_price:.1f}  실현변동성(30일): {realized_vol_pct:.1f}%  VIX/RV: {vix_rv_ratio:.2f}
VIX 구간: {vix_label}
HYG: ${hyg_close:.2f} / MA50: ${hyg_ma50_val:.2f} → {'신용 정상' if hyg_ok else '신용 악화'}
신규 진입: 방패(A) {'허용' if allow_entry_a else '차단'} / 창(B) {'허용' if allow_entry_b else '차단'}
가용 현금: ${portfolio_cash:,.0f} | 총 평가액: ${total_portfolio_value:,.0f}

--- 포트폴리오 ---
전략 A(방패/평균회귀):
{chr(10).join(port_summary_a) or '  없음'}
전략 B(창/모멘텀):
{chr(10).join(port_summary_b) or '  없음'}
전략 C(VIX 패닉 매수, 청산조건 VIX<20):
{chr(10).join(port_summary_c) or '  없음'}
전략 D(크립토 모멘텀, ETH 레짐 {'✅' if eth_regime else '⛔'}):
{chr(10).join(port_summary_d) or '  없음'}

청산 규칙:
- A: ATR Stop → RSI≥50(50% 익절+스톱 타이트닝) → MA20(전량 익절)
- B: ATR Stop or MA50 하향 이탈(전량 익절)
- C: VIX<20 복귀 시 전량 익절
- D: ATR Stop or MA50 하향 이탈(전량 익절)

{entry_section}

--- 출력 형식 (이 순서 그대로, 다른 말 붙이지 말 것) ---

## ✅ 오늘 할 일
신호가 발생한 종목만. 없으면 "없음".
- [종목] [신호] → [행동] (이유 한 줄)
예: CRH STOP → 전량 매도 (ATR 스톱 붕괴)

## 📡 시장 읽기
3–4줄. VIX/HYG/QQQ 흐름을 종합해서 지금 시장이 어느 국면인지, 앞으로 뭘 보면 되는지.
숫자 근거 포함. "VIX가 X까지 내려오면 진입 재개 가능" 같은 구체적 트리거 제시.

## 📰 뉴스 → 내 포지션 임팩트
Google Search로 보유 종목({', '.join(p['ticker'] for p in port_rows if p['signal'] != 'NO_DATA')}) 최근 뉴스를 검색해서,
각 뉴스가 내 포지션에 구체적으로 어떤 의미인지 연결해줘.

형식:
- [종목] [뉴스 핵심 한 줄] → 내 포지션 임팩트: [HOLD/매도 고려/스톱 조정 등 + 이유]
없는 종목은 생략. 최대 5개.

## 🔄 섹터 흐름
Google Search로 지금 시장에서 돈이 어디로 이동 중인지 검색해줘.
- 강세 섹터 / 약세 섹터 각 2개씩 (근거 포함)
- 내 포트폴리오가 그 흐름과 맞는지 한 줄 평가

## ⚠️ 포트폴리오 리스크
데이터 기반으로 분석 (검색 불필요):
- 현재 보유 종목 간 집중 리스크 (같은 방향으로 움직이는 종목 묶음)
- 가장 취약한 포지션 1개와 이유
- 지금 당장 헤지가 필요한지 여부

## 💡 한 줄 결론
오늘 가장 중요한 판단 한 문장.
"""

    analysis_text = None
    try:
        from google.genai import types as genai_types
        client = genai.Client(api_key=api_key, http_options={'timeout': 120_000})
        search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        gen_config  = genai_types.GenerateContentConfig(tools=[search_tool])
        models_to_try = ['gemini-2.5-flash', 'gemini-2.5-flash-lite']
        for model_name in models_to_try:
            try:
                resp = client.models.generate_content(
                    model=model_name, contents=prompt, config=gen_config
                )
                analysis_text = resp.text
                if not analysis_text:
                    raise ValueError("빈 응답 (resp.text is empty/None)")
                log.info(f"Gemini {model_name} 사용 (Search grounding 활성)")
                break
            except Exception as e:
                log.warning(f"Gemini {model_name} 실패: {e}")
        if not analysis_text:
             log.error("모든 Gemini 모델 호출 실패")
    except Exception as e:
        log.error(f"Gemini 클라이언트 오류: {e}")

    if analysis_text:
        print(analysis_text)
        log.info(f"AI 분석:\n{analysis_text}")

    # ── 9. VIX 30 돌파 긴급 알림 ─────────────────────────────────────────────
    if vix_zone == 'PANIC' and not pd.isna(vix_price):
        c_lines = "\n".join(
            f"  {c['ticker']:<5} ${c['close']:>7.2f}  제안 ${c['suggested_amount']:,.0f} ({c['suggested_shares']}주)"
            for c in entry_c
        ) or "  (이미 보유 중)"
        vix_alert = (
            f"## 🚨 VIX 30 돌파 — 전략 C 진입 시그널\n"
            f"```\n"
            f"VIX : {vix_price:.2f}  (30 상향 돌파)\n"
            f"\n"
            f"[백테스트 근거 — VIX < 20 복귀 시 청산 기준]\n"
            f"  SPY : 승률 96.4%  평균 +11.5%  Sharpe 1.21  평균 보유 88일\n"
            f"  QQQ : 승률 97.6%  평균 +13.6%  Sharpe 1.04  평균 보유 80일\n"
            f"  (1993~2026, 83회 이벤트 기준)\n"
            f"\n"
            f"[전략 C 진입 제안 — 가용 현금 ${portfolio_cash:,.0f}의 {C_POSITION_PCT:.0f}%]\n"
            f"{c_lines}\n"
            f"\n"
            f"[전략 A·B 상태]\n"
            f"  방패(A) 신규 진입: 허용 (VIX > 30 공황 반등)\n"
            f"  창(B)  신규 진입: 차단 유지 (VIX < 25 복귀 전)\n"
            f"```"
        )
        print("\n🚨 VIX 30 돌파 긴급 알림 전송 중...")
        send_discord(vix_alert)
        log.info(f"VIX 30 돌파 알림 전송 완료 (VIX {vix_price:.2f})")
    elif not pd.isna(vix_price) and vix_price >= 28:
        # VIX 28+ 워닝 — 30 임박 예고 알림
        vix_warning = (
            f"## ⚠️ VIX 30 임박 주의\n"
            f"```\n"
            f"VIX : {vix_price:.2f}  (30 돌파 시 방패(A) 매수 재개 예정)\n"
            f"현재 구간 : 위험 (25–30) — 신규 진입 전면 차단 중\n"
            f"```"
        )
        print(f"\n⚠️ VIX {vix_price:.2f} — 30 임박 예고 알림 전송 중...")
        send_discord(vix_warning)

    # ── 10. Discord 전송 ──────────────────────────────────────────────────────
    now = datetime.today().strftime('%Y-%m-%d %H:%M')
    discord_msg = f"## 📡 Scanner v4  [{now}]\n```\n{strip_ansi(scan_text)}\n```"
    if analysis_text:
        discord_msg += f"\n---\n{analysis_text}"

    # 긴급 시그널 있으면 @here 멘션 추가
    URGENT_SIGNALS = {'STOP', 'TP1', 'TP2', 'MA_CROSS', 'C_EXIT'}
    urgent_tickers = [
        f"{p['ticker']}[{p['signal']}]"
        for p in port_rows
        if p.get('signal') in URGENT_SIGNALS
    ]
    if urgent_tickers:
        discord_msg = f"@here 🚨 **즉시 확인 필요**: {', '.join(urgent_tickers)}\n" + discord_msg

    print("\n📨 Discord 전송 중...")
    send_discord(discord_msg)

    # 위험 브리핑 — 스캔 직후 자동 실행 (signals.json을 읽어 6지표 대시보드 발송)
    _run_risk_briefing()


if __name__ == '__main__':
    main()
