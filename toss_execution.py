"""
toss_execution.py — 토스 자동매매 실행기 (Phase 1: 드라이런)

scanner_v4.py의 signals.json을 읽어 토스 실계좌(보유/예수금/시세)와 대조하고
안전장치를 거쳐 '의도한 주문'을 로그·디스코드로 알린다.
실제 주문(POST /orders)은 호출하지 않는다. --live는 Phase 2까지 거부.

사용: python toss_execution.py            # 드라이런
      touch kill_switch.flag              # 긴급 중단
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError
# DRY: 검증된 순수 안전장치/사이징 함수 재사용
from execution_layer import check_price_sanity, count_positions_by_strategy, calc_shares

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.FileHandler(BASE_DIR / 'toss_execution.log', encoding='utf-8'),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SIGNALS_FILE   = BASE_DIR / 'signals.json'
PORTFOLIO_FILE = BASE_DIR / 'portfolio.json'
KILL_SWITCH    = BASE_DIR / 'kill_switch.flag'
EXEC_LOG       = BASE_DIR / 'toss_execution_history.json'

MAX_POS_A = 10
MAX_POS_B = 10


def decide_exit_qty(signal: str, shares: float) -> float:
    """TP1은 50% 분할, 그 외는 전량."""
    return round(shares * 0.5, 6) if signal == 'TP1' else shares


def is_core(pos: dict) -> bool:
    """Core 전략 = 자동 매도 대상 아님."""
    return pos.get('strategy') == 'Core'


def make_intent(side, ticker, qty, ref_price, strategy=None, signal=None) -> dict:
    return {
        'mode': 'DRY', 'side': side, 'ticker': ticker, 'qty': qty,
        'ref_price': ref_price, 'strategy': strategy, 'signal': signal,
        'ts': datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
    }


def format_intent(it: dict) -> str:
    tag = f"[{it['signal']}]" if it.get('signal') else f"[전략 {it.get('strategy', '?')}]"
    return f"{it['side'].upper():4} {it['ticker']:6} {it['qty']}주 @ ~{it['ref_price']} {tag}"


def append_history(record: dict) -> None:
    try:
        hist = json.loads(EXEC_LOG.read_text(encoding='utf-8')) if EXEC_LOG.exists() else []
    except (ValueError, OSError):
        hist = []
    hist.append(record)
    EXEC_LOG.write_text(json.dumps(hist, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def parse_account_snapshot(holdings_resp: dict, usd_cash: float) -> dict:
    """토스 holdings 응답 → {usd_cash, account_value_usd, held{ticker:{shares,last}}}.
    미국 종목만 held에 담는다(스캐너는 US 유니버스)."""
    r = holdings_resp.get('result', {}) or {}
    mkt_usd = _f((((r.get('marketValue') or {}).get('amount')) or {}).get('usd'))
    held = {}
    for it in r.get('items', []) or []:
        if it.get('marketCountry') != 'US':
            continue
        held[it['symbol']] = {'shares': _f(it.get('quantity')),
                              'last': _f(it.get('lastPrice'))}
    return {'usd_cash': usd_cash,
            'account_value_usd': round(mkt_usd + usd_cash, 2),
            'held': held}


def fetch_snapshot(client: TossClient) -> dict:
    """실제 토스 호출로 스냅샷 구성 (셸)."""
    seq = client.get_accounts()['result'][0]['accountSeq']
    holdings = client.get_holdings(seq)
    usd_cash = _f((client.get_buying_power(seq, 'USD').get('result') or {}).get('cashBuyingPower'))
    snap = parse_account_snapshot(holdings, usd_cash)
    snap['seq'] = seq
    return snap


def process_exits(exits: list, held: dict, portfolio: dict) -> list:
    """매도 의도 리스트 반환 (드라이런). 실제 보유 + Core 아님만."""
    intents = []
    for ex in exits or []:
        t = ex['ticker']
        if t not in held or held[t]['shares'] <= 0:
            log.info(f'{t} 미보유 — 청산 스킵')
            continue
        if is_core(portfolio.get(t, {})):
            log.info(f'{t} Core — 자동 매도 안 함')
            continue
        qty = decide_exit_qty(ex['signal'], held[t]['shares'])
        if qty <= 0:
            continue
        intents.append(make_intent('sell', t, qty, held[t]['last'],
                                   strategy=ex.get('strategy'), signal=ex['signal']))
    return intents


def _entry_one(cand, strategy, held, account_value, usd_cash, price_fn, intents):
    t = cand['ticker']
    if t in held:
        log.info(f'{t} 이미 보유 — 진입 스킵')
        return usd_cash
    scanner_price = float(cand['close'])
    atr_val = (scanner_price - float(cand.get('stop', scanner_price * 0.97))) / 3.0
    cur = price_fn(t)
    if cur is None:
        log.warning(f'{t} 현재가 조회 실패 — 스킵')
        return usd_cash
    if not check_price_sanity(cur, scanner_price, t):
        return usd_cash
    qty = calc_shares(account_value, cur, atr_val, strategy)
    cost = qty * cur
    if qty < 1 or cost > usd_cash:
        log.info(f'{t} 예수금 부족/수량 0 (필요 ${cost:,.2f} > 가용 ${usd_cash:,.2f}) — 스킵')
        return usd_cash
    intents.append(make_intent('buy', t, qty, cur, strategy=strategy))
    return usd_cash - cost


def process_entries(entries, regime, held, account_value, usd_cash, price_fn) -> list:
    """매수 의도 리스트(드라이런). A/B 각 1건, C 전체. 예수금 차감 반영."""
    intents = []
    if regime.get('allow_entry_a'):
        for cand in (entries.get('A') or [])[:1]:
            usd_cash = _entry_one(cand, 'A', held, account_value, usd_cash, price_fn, intents)
    if regime.get('allow_entry_b'):
        for cand in (entries.get('B') or [])[:1]:
            usd_cash = _entry_one(cand, 'B', held, account_value, usd_cash, price_fn, intents)
    for cand in (entries.get('C') or []):
        usd_cash = _entry_one(cand, 'C', held, account_value, usd_cash, price_fn, intents)
    return intents


def check_kill_switch():
    if KILL_SWITCH.exists():
        log.warning('🛑 킬스위치 활성 — 전면 중단')
        send_discord('## 🛑 toss_execution 강제 중단 (kill_switch.flag)')
        sys.exit(0)


def send_discord(message: str):
    import requests
    url = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not url:
        return
    try:
        for c in [message[i:i+1900] for i in range(0, len(message), 1900)]:
            requests.post(url, json={'content': c, 'username': 'TossExec(DRY)'}, timeout=10)
    except Exception as e:
        log.error(f'Discord 실패: {e}')


def market_open_us(client: TossClient) -> bool:
    """미국 정규장 개장 여부 — today+previousBusinessDay 확인(KST 자정 버그 수정)."""
    return client.is_market_open('US')


def report(intents: list, snap: dict, market_is_open: bool):
    now = datetime.today().strftime('%Y-%m-%d %H:%M')
    lines = [f'## 🧪 토스 실행기 DRY-RUN  [{now}]', '```']
    lines.append(f"계좌평가 ${snap['account_value_usd']:,.2f} | 예수금 ${snap['usd_cash']:,.2f} "
                 f"| 장 {'OPEN' if market_is_open else 'CLOSED'}")
    sells = [i for i in intents if i['side'] == 'sell']
    buys  = [i for i in intents if i['side'] == 'buy']
    lines.append(f"\n[의도된 매도 {len(sells)}]")
    lines += ['  ' + format_intent(i) for i in sells] or ['  없음']
    lines.append(f"\n[의도된 매수 {len(buys)}]")
    lines += ['  ' + format_intent(i) for i in buys] or ['  없음']
    if not market_is_open:
        lines.append('\n⚠️ 정규장 미개장 — 실거래였다면 보류됨')
    lines.append('```')
    send_discord('\n'.join(lines))
    for line in lines:
        log.info(line)


def main():
    ap = argparse.ArgumentParser(description='토스 자동매매 실행기 (드라이런)')
    ap.add_argument('--live', action='store_true', help='[Phase 2] 실제 주문 — 현재 미구현')
    args = ap.parse_args()
    if args.live:
        log.error('❌ --live는 Phase 2(create_order 구현)까지 비활성. 드라이런만 가능.')
        sys.exit(2)

    check_kill_switch()
    try:
        signals = json.loads(SIGNALS_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        log.error('signals.json 없음 — scanner_v4.py 먼저 실행'); sys.exit(1)
    if signals.get('date') != datetime.today().strftime('%Y-%m-%d'):
        log.warning(f"signals.json 날짜 불일치({signals.get('date')}) — 오래된 신호일 수 있음")

    try:
        portfolio = json.loads(PORTFOLIO_FILE.read_text(encoding='utf-8')).get('holdings', {})
    except (FileNotFoundError, ValueError):
        portfolio = {}

    try:
        client = TossClient()
        snap = fetch_snapshot(client)
        mkt_open = market_open_us(client)
        price_map = {}

        def price_fn(t):
            if t not in price_map:
                try:
                    res = client.get_prices(t).get('result') or [{}]
                    price_map[t] = float(res[0].get('lastPrice')) if res else None
                except (TossAPIError, ValueError, TypeError):
                    price_map[t] = None
            return price_map[t]

        regime  = signals.get('regime', {})
        exits   = process_exits(signals.get('exits', []), snap['held'], portfolio)
        # 포지션 캡: portfolio.json의 전략별 카운트로 차단
        counts = count_positions_by_strategy(portfolio)
        if counts['A'] >= MAX_POS_A:
            regime = {**regime, 'allow_entry_a': False}
        if counts['B'] >= MAX_POS_B:
            regime = {**regime, 'allow_entry_b': False}
        entries = process_entries(signals.get('entries', {}), regime, snap['held'],
                                  snap['account_value_usd'], snap['usd_cash'], price_fn)
        intents = exits + entries
    except (TossAPIError, KeyError, IndexError) as e:
        log.error(f'토스 조회 실패 — 중단: {e}'); sys.exit(1)

    for it in intents:
        append_history({'date': datetime.today().strftime('%Y-%m-%d'), **it})
    report(intents, snap, mkt_open)
    log.info(f'드라이런 완료: 의도 {len(intents)}건 (실제 주문 없음)')


if __name__ == '__main__':
    main()
