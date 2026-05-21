"""
Execution Layer v1 — Alpaca Paper Trading
─────────────────────────────────────────────────────────────────────
scanner_v4.py가 생성한 signals.json을 읽어 Alpaca Paper API로 주문을 실행합니다.

실행 순서:
  1. 킬스위치 확인 (kill_switch.flag)
  2. Alpaca Paper 계좌 연결 및 일일 손실 한도 확인 (-3%)
  3. 청산 신호 실행 (STOP, TP1, TP2, MA_CROSS, C_EXIT)
  4. 신규 진입 실행 (전략 A 1개, B 1개, C 전체)
  5. portfolio.json ↔ Alpaca 실제 포지션 동기화
  6. Discord 실행 보고

안전장치:
  - 킬스위치 : touch kill_switch.flag → 모든 주문 즉시 중단
  - 일일 손실 : 총자산 대비 -3% 초과 시 신규 진입 전면 차단
  - 포지션 캡  : 전략 A ≤ 10개, B ≤ 10개
  - 가격 이상  : scanner 가격 vs Alpaca 현재가 ±20% 초과 시 차단
  - 최소 수량  : qty < 1 주문 자동 차단

사용법:
  python execution_layer.py           # scanner_v4.py 실행 후 바로 실행
  touch kill_switch.flag              # 긴급 전면 중단
  rm kill_switch.flag                 # 킬스위치 해제
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(BASE_DIR / 'execution.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SIGNALS_FILE   = BASE_DIR / 'signals.json'
PORTFOLIO_FILE = BASE_DIR / 'portfolio.json'
KILL_SWITCH    = BASE_DIR / 'kill_switch.flag'
EXECUTION_LOG  = BASE_DIR / 'execution_history.json'

MAX_LOSS_PCT    = 3.0   # 일일 최대 손실 한도 (%)
MAX_POS_A       = 10    # 전략 A 최대 포지션 수
MAX_POS_B       = 10    # 전략 B 최대 포지션 수
PRICE_DRIFT_PCT = 20.0  # scanner 가격 vs 현재가 허용 차이 (%)
POSITION_PCT_A  = 10.0  # 전략 A 종목당 배분 (% of 총자산)
POSITION_PCT_B  = 10.0  # 전략 B 종목당 배분 (% of 총자산)
RISK_PCT        = 1.0   # 종목당 최대 리스크 (% of 총자산, ATR 기반)


# ─── 파일 I/O ─────────────────────────────────────────────────────────────────

def load_portfolio() -> tuple[dict, float]:
    try:
        with open(PORTFOLIO_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('holdings', {}), float(data.get('cash', 0))
    except FileNotFoundError:
        return {}, 0.0


def save_portfolio(holdings: dict, cash: float) -> None:
    with open(PORTFOLIO_FILE, 'w', encoding='utf-8') as f:
        json.dump({'holdings': holdings, 'cash': cash}, f, indent=2, ensure_ascii=False)


def load_signals() -> dict:
    try:
        with open(SIGNALS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        log.error('signals.json 없음 — scanner_v4.py를 먼저 실행하세요')
        sys.exit(1)


def load_execution_history() -> list:
    try:
        with open(EXECUTION_LOG, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def append_execution(record: dict) -> None:
    history = load_execution_history()
    history.append(record)
    with open(EXECUTION_LOG, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False, default=str)


# ─── Discord ──────────────────────────────────────────────────────────────────

def send_discord(message: str) -> None:
    url = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not url:
        return
    try:
        chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            requests.post(url, json={'content': chunk, 'username': 'Execution v1'}, timeout=10)
    except Exception as e:
        log.error(f'Discord 전송 실패: {e}')


# ─── Alpaca ───────────────────────────────────────────────────────────────────

def get_alpaca_client():
    """Alpaca Paper Trading 클라이언트 반환"""
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        log.error('alpaca-py 미설치: pip install alpaca-py')
        sys.exit(1)

    api_key    = os.getenv('ALPACA_API_KEY', '')
    secret_key = os.getenv('ALPACA_SECRET_KEY', '')
    if not api_key or not secret_key:
        log.error('.env에 ALPACA_API_KEY / ALPACA_SECRET_KEY 없음')
        sys.exit(1)

    return TradingClient(api_key, secret_key, paper=True)


def get_current_price(ticker: str) -> float | None:
    """Alpaca Data API로 최신 bid/ask 중간값 조회"""
    try:
        from alpaca.data import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        api_key    = os.getenv('ALPACA_API_KEY', '')
        secret_key = os.getenv('ALPACA_SECRET_KEY', '')
        data_client = StockHistoricalDataClient(api_key, secret_key)
        quote = data_client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=ticker)
        )
        q = quote[ticker]
        return float((q.ask_price + q.bid_price) / 2)
    except Exception as e:
        log.warning(f'{ticker} Alpaca 가격 조회 실패: {e}')
        return None


def submit_order(client, ticker: str, qty: int, side: str) -> dict | None:
    """시장가 주문 제출"""
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(order_data)
        log.info(f'주문 제출: {side.upper()} {ticker} {qty}주 | ID: {order.id}')
        return {'id': str(order.id), 'ticker': ticker, 'qty': qty, 'side': side}
    except Exception as e:
        log.error(f'{ticker} 주문 실패: {e}')
        return None


def sync_portfolio_from_alpaca(client) -> None:
    """Alpaca 실제 포지션 → portfolio.json 동기화"""
    try:
        positions = client.get_all_positions()
        account   = client.get_account()
        holdings, _ = load_portfolio()

        alpaca_tickers = {p.symbol for p in positions}
        local_tickers  = set(holdings.keys())
        CORE_ASSETS    = {'QQQM', 'TLT', 'TSLA'}

        # Alpaca에 없는데 로컬에 있는 포지션 → 청산된 것으로 처리 (CORE 제외)
        for ticker in local_tickers - alpaca_tickers - CORE_ASSETS:
            log.info(f'[Sync] {ticker} Alpaca에서 청산됨 → 로컬 제거')
            del holdings[ticker]

        # Alpaca 포지션으로 수량 업데이트
        for pos in positions:
            if pos.symbol in holdings:
                holdings[pos.symbol]['shares'] = float(pos.qty)

        cash = float(account.cash)
        save_portfolio(holdings, cash)
        log.info(f'[Sync] 완료: 포지션 {len(positions)}개, 현금 ${cash:,.2f}')
    except Exception as e:
        log.error(f'Alpaca 동기화 실패: {e}')


# ─── 안전장치 ─────────────────────────────────────────────────────────────────

def check_kill_switch() -> None:
    if KILL_SWITCH.exists():
        msg = f'🛑 킬스위치 활성: {KILL_SWITCH.name} 파일 존재 — 모든 주문 중단'
        log.warning(msg)
        send_discord(f'## 🛑 Execution Layer 강제 중단\n```\n{msg}\n```')
        sys.exit(0)


def check_daily_loss(client, start_value: float) -> bool:
    """일일 손실 한도 초과 여부 확인. True = 정상 진행 가능"""
    try:
        account       = client.get_account()
        current_value = float(account.portfolio_value)
        loss_pct      = (current_value - start_value) / start_value * 100
        if loss_pct < -MAX_LOSS_PCT:
            log.warning(
                f'일일 손실 한도 초과: {loss_pct:.2f}% (한도 -{MAX_LOSS_PCT}%) → 신규 진입 전면 차단'
            )
            return False
        return True
    except Exception as e:
        log.warning(f'일일 손실 확인 실패 (안전을 위해 차단): {e}')
        return False


def check_price_sanity(current_price: float, scanner_price: float, ticker: str) -> bool:
    """scanner 기준 가격 대비 현재가 이상 여부 확인"""
    if scanner_price <= 0:
        return True
    drift = abs(current_price - scanner_price) / scanner_price * 100
    if drift > PRICE_DRIFT_PCT:
        log.warning(
            f'{ticker} 가격 이상: scanner ${scanner_price:.2f} vs 현재 ${current_price:.2f} '
            f'({drift:.1f}% 차이) → 주문 차단'
        )
        return False
    return True


def count_positions_by_strategy(holdings: dict) -> dict[str, int]:
    counts = {'A': 0, 'B': 0, 'C': 0}
    for pos in holdings.values():
        s = pos.get('strategy', 'A')
        if s in counts:
            counts[s] += 1
    return counts


def calc_shares(total_value: float, current_price: float, atr_val: float, strategy: str) -> int:
    """종목당 배분 금액과 ATR 리스크 한도 중 작은 쪽으로 주수 계산"""
    pct           = POSITION_PCT_A if strategy == 'A' else POSITION_PCT_B
    allocation    = total_value * pct / 100
    risk_limit    = total_value * RISK_PCT / 100
    alloc_shares  = int(allocation / current_price) if current_price > 0 else 0
    atr_shares    = int(risk_limit / atr_val)       if atr_val > 0       else alloc_shares
    return max(min(alloc_shares, atr_shares), 1)


# ─── 보고 ─────────────────────────────────────────────────────────────────────

def send_report(
    executed: list,
    errors: list,
    regime: dict,
    start_value: float,
    blocked_reason: str = '',
) -> None:
    now = datetime.today().strftime('%Y-%m-%d %H:%M')
    lines = [f'## 🤖 Execution Layer v1  [{now}]', '```']
    lines.append(
        f"레짐: VIX {regime.get('vix', 'N/A')} ({regime.get('vix_zone', '?')}) | "
        f"HYG {'정상' if regime.get('hyg_ok', True) else '⚠️ 악화'}"
    )
    if blocked_reason:
        lines.append(f'⛔ {blocked_reason}')

    exits_done   = [e for e in executed if e['type'] == 'exit']
    entries_done = [e for e in executed if e['type'] == 'entry']

    if exits_done:
        lines.append(f'\n청산 ({len(exits_done)}건):')
        for e in exits_done:
            lines.append(f"  SELL {e['ticker']} {e['qty']}주  [{e.get('signal', '')}]")

    if entries_done:
        lines.append(f'\n진입 ({len(entries_done)}건):')
        for e in entries_done:
            lines.append(f"  BUY  {e['ticker']} {e['qty']}주  [전략 {e.get('strategy', '?')}]")

    if not exits_done and not entries_done:
        lines.append('\n실행 없음')

    if errors:
        lines.append(f'\n⚠️ 오류 ({len(errors)}건):')
        for err in errors:
            lines.append(f'  {err}')

    lines.append('```')
    send_discord('\n'.join(lines))


# ─── 메인 ────────────────────────────────────────────────────────────────────

def main() -> None:
    today = datetime.today().strftime('%Y-%m-%d %H:%M')
    log.info('=' * 60)
    log.info(f'Execution Layer v1  |  {today}')
    log.info('=' * 60)

    # ── 0. 킬스위치 ───────────────────────────────────────────────────────────
    check_kill_switch()

    # ── 1. 시그널 로드 ────────────────────────────────────────────────────────
    signals          = load_signals()
    sig_date         = signals.get('date', '')
    holdings, portfolio_cash = load_portfolio()

    if sig_date != datetime.today().strftime('%Y-%m-%d'):
        log.warning(f'signals.json 날짜 불일치: {sig_date} ≠ 오늘 — 오래된 신호일 수 있음')

    regime  = signals.get('regime', {})
    exits   = signals.get('exits', [])
    entries = signals.get('entries', {})

    log.info(
        f"시그널 로드: {sig_date} | "
        f"VIX {regime.get('vix', 'N/A')} ({regime.get('vix_zone', '?')}) | "
        f"HYG {'정상' if regime.get('hyg_ok', True) else '⚠️ 악화'} | "
        f"청산 {len(exits)}건 | 진입 A {len(entries.get('A', []))}건 B {len(entries.get('B', []))}건"
    )

    # ── 2. Alpaca 연결 ────────────────────────────────────────────────────────
    client            = get_alpaca_client()
    account           = client.get_account()
    total_value_start = float(account.portfolio_value)
    log.info(f'Alpaca Paper: 총자산 ${total_value_start:,.2f} | 현금 ${float(account.cash):,.2f}')

    executed: list[dict] = []
    errors:   list[str]  = []

    # ── 3. 청산 실행 (STOP, TP1, TP2, MA_CROSS, C_EXIT) ─────────────────────
    log.info(f'--- 청산 처리 ({len(exits)}건) ---')
    for ex in exits:
        check_kill_switch()

        ticker   = ex['ticker']
        signal   = ex['signal']
        strategy = ex.get('strategy', 'A')
        shares   = float(holdings.get(ticker, {}).get('shares', 0))

        if shares <= 0:
            log.warning(f'{ticker} 보유 수량 없음 — 스킵')
            continue

        # TP1: 50% 분할 매도, 나머지: 전량
        sell_qty = max(1, int(shares * 0.5)) if signal == 'TP1' else int(shares)

        if sell_qty < 1:
            log.warning(f'{ticker} 매도 수량 0 — 스킵')
            continue

        log.info(f'청산: [{signal}] {ticker} {sell_qty}주 (전략 {strategy})')
        result = submit_order(client, ticker, sell_qty, 'sell')
        if result:
            executed.append({'type': 'exit', 'signal': signal, **result})
            if signal == 'TP1':
                holdings[ticker]['shares']   = shares - sell_qty
                holdings[ticker]['tp1_hit']  = True
            else:
                holdings.pop(ticker, None)
        else:
            errors.append(f'청산 실패: {ticker} [{signal}]')

    # ── 4. 일일 손실 확인 (진입 전) ──────────────────────────────────────────
    if not check_daily_loss(client, total_value_start):
        save_portfolio(holdings, portfolio_cash)
        send_report(executed, errors, regime, total_value_start, blocked_reason='일일 손실 한도 초과')
        sync_portfolio_from_alpaca(client)
        return

    # ── 5. 신규 진입 실행 ─────────────────────────────────────────────────────
    if not regime.get('allow_entry_a') and not regime.get('allow_entry_b'):
        log.info('레짐 필터 — 신규 진입 전면 차단')
    else:
        pos_counts  = count_positions_by_strategy(holdings)
        account_now = client.get_account()
        total_value = float(account_now.portfolio_value)
        log.info(f"--- 신규 진입 (현재 포지션: A={pos_counts['A']}, B={pos_counts['B']}) ---")

        # 전략 A: RSI 최저 상위 1개만
        if regime.get('allow_entry_a') and pos_counts['A'] < MAX_POS_A:
            for cand in entries.get('A', [])[:1]:
                check_kill_switch()
                ticker        = cand['ticker']
                scanner_price = float(cand['close'])
                # ATR 역산: stop = close - ATR * 3.0
                atr_val       = (scanner_price - float(cand.get('stop', scanner_price * 0.97))) / 3.0

                if ticker in holdings:
                    log.info(f'{ticker} 이미 보유 — 스킵')
                    continue

                cur_price = get_current_price(ticker)
                if cur_price is None:
                    errors.append(f'가격 조회 실패: {ticker}')
                    continue
                if not check_price_sanity(cur_price, scanner_price, ticker):
                    errors.append(f'가격 이상: {ticker}')
                    continue

                qty = calc_shares(total_value, cur_price, atr_val, 'A')
                log.info(f'진입(A): {ticker} {qty}주 @ ~${cur_price:.2f}  RSI {cand["rsi"]}')
                result = submit_order(client, ticker, qty, 'buy')
                if result:
                    executed.append({'type': 'entry', 'strategy': 'A', **result})
                    holdings[ticker] = {
                        'shares':        qty,
                        'buy_price':     cur_price,
                        'trailing_stop': float(cand.get('stop', 0)),
                        'tp1_hit':       False,
                        'strategy':      'A',
                    }
                else:
                    errors.append(f'진입 실패: {ticker} A')

        # 전략 B: RSI 최고 상위 1개만
        if regime.get('allow_entry_b') and pos_counts['B'] < MAX_POS_B:
            for cand in entries.get('B', [])[:1]:
                check_kill_switch()
                ticker        = cand['ticker']
                scanner_price = float(cand['close'])
                atr_val       = (scanner_price - float(cand.get('stop', scanner_price * 0.97))) / 3.0

                if ticker in holdings:
                    log.info(f'{ticker} 이미 보유 — 스킵')
                    continue

                cur_price = get_current_price(ticker)
                if cur_price is None:
                    errors.append(f'가격 조회 실패: {ticker}')
                    continue
                if not check_price_sanity(cur_price, scanner_price, ticker):
                    errors.append(f'가격 이상: {ticker}')
                    continue

                qty = calc_shares(total_value, cur_price, atr_val, 'B')
                log.info(f'진입(B): {ticker} {qty}주 @ ~${cur_price:.2f}  RSI {cand["rsi"]}')
                result = submit_order(client, ticker, qty, 'buy')
                if result:
                    executed.append({'type': 'entry', 'strategy': 'B', **result})
                    holdings[ticker] = {
                        'shares':        qty,
                        'buy_price':     cur_price,
                        'trailing_stop': float(cand.get('stop', 0)),
                        'tp1_hit':       False,
                        'strategy':      'B',
                    }
                else:
                    errors.append(f'진입 실패: {ticker} B')

        # 전략 C (VIX 공황): 목록 전체 진입
        for cand in entries.get('C', []):
            check_kill_switch()
            ticker = cand['ticker']
            if ticker in holdings and holdings[ticker].get('strategy') == 'C':
                log.info(f'{ticker} 이미 C 전략 보유 — 스킵')
                continue

            suggested = int(cand.get('suggested_shares', 1))
            cur_price = get_current_price(ticker)
            if cur_price is None:
                errors.append(f'가격 조회 실패: {ticker}')
                continue

            log.info(f'진입(C): {ticker} {suggested}주 @ ~${cur_price:.2f} (VIX 공황)')
            result = submit_order(client, ticker, suggested, 'buy')
            if result:
                executed.append({'type': 'entry', 'strategy': 'C', **result})
                holdings[ticker] = {
                    'shares':        suggested,
                    'buy_price':     cur_price,
                    'trailing_stop': 0.0,
                    'tp1_hit':       False,
                    'strategy':      'C',
                    'vix_entry':     regime.get('vix'),
                }
            else:
                errors.append(f'진입 실패: {ticker} C')

    # ── 6. portfolio.json 저장 ────────────────────────────────────────────────
    save_portfolio(holdings, portfolio_cash)

    # ── 7. Alpaca 실제 계좌와 동기화 ─────────────────────────────────────────
    sync_portfolio_from_alpaca(client)

    # ── 8. 실행 이력 저장 ─────────────────────────────────────────────────────
    for rec in executed:
        append_execution({'date': today, **rec})

    # ── 9. Discord 보고 ───────────────────────────────────────────────────────
    send_report(executed, errors, regime, total_value_start)
    log.info(f'완료: 실행 {len(executed)}건, 오류 {len(errors)}건')


if __name__ == '__main__':
    main()
