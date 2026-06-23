"""
forward_test.py — 페이퍼 포트폴리오 forward 검증 (토스 실시간 시세 기반)

실제 돈 없이 전략을 '앞으로' 검증한다. scanner_v4가 만든 signals.json과
토스 실시간 시세로, 가상의 페이퍼 포트폴리오를 굴리며 일별 평가액을 누적한다.
이것이 생존편향·룩어헤드가 없는 진짜 검증 — 백테스트 거품과 무관.

toss_execution의 결정 로직(process_exits/process_entries)을 그대로 재사용한다(DRY).
주문 결정은 동일하되, 토스 실계좌가 아니라 페이퍼 북에 체결을 시뮬레이션한다.

파일:
  forward_paper.json   페이퍼 북 {cash, holdings{ticker:{shares,buy_price,strategy,...}}}
  forward_equity.json  일별 [{date, value, cash, n_pos, trades}] — forward 성과 곡선

사용:
  python forward_test.py                 # 하루치 진행 (scanner 먼저 실행 가정)
  python forward_test.py --cash 10000    # 최초 시작 자본 설정
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import toss_execution as te
from toss_client import TossClient, TossAPIError
from execution_layer import calc_shares

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

PAPER_FILE = BASE_DIR / 'forward_paper.json'
EQUITY_FILE = BASE_DIR / 'forward_equity.json'
DEFAULT_CASH = 10000.0


def load_paper(default_cash: float) -> dict:
    if PAPER_FILE.exists():
        return json.loads(PAPER_FILE.read_text(encoding='utf-8'))
    return {'cash': default_cash, 'holdings': {}}


def apply_fills(paper: dict, intents: list, prices: dict) -> int:
    """페이퍼 북에 체결 시뮬레이션. 매수=현금차감+보유추가, 매도=현금증가+보유감소.
    체결가는 prices[ticker]. 반환: 체결 건수."""
    n = 0
    for it in intents:
        t, qty = it['ticker'], it['qty']
        px = prices.get(t)
        if not px or qty <= 0:
            continue
        h = paper['holdings']
        if it['side'] == 'buy':
            cost = qty * px
            if cost > paper['cash']:
                continue
            paper['cash'] -= cost
            h[t] = {'shares': qty, 'buy_price': px, 'strategy': it.get('strategy', 'A'),
                    'trailing_stop': None, 'tp1_hit': False}
            n += 1
        else:  # sell
            if t not in h:
                continue
            sell = min(qty, h[t]['shares'])
            paper['cash'] += sell * px
            h[t]['shares'] -= sell
            if h[t]['shares'] <= 1e-9:
                del h[t]
            n += 1
    return n


def mark_to_market(paper: dict, prices: dict) -> float:
    """페이퍼 북 총평가액 = 현금 + Σ(보유수량 × 현재가)."""
    val = paper['cash']
    for t, pos in paper['holdings'].items():
        px = prices.get(t)
        if px:
            val += pos['shares'] * px
    return round(val, 2)


def append_equity(record: dict) -> None:
    hist = json.loads(EQUITY_FILE.read_text(encoding='utf-8')) if EQUITY_FILE.exists() else []
    hist.append(record)
    EQUITY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='페이퍼 forward 검증')
    ap.add_argument('--cash', type=float, default=DEFAULT_CASH, help='최초 시작 자본(파일 없을 때)')
    args = ap.parse_args()

    try:
        signals = json.loads(te.SIGNALS_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        print('signals.json 없음 — scanner_v4.py 먼저 실행'); return 1

    paper = load_paper(args.cash)
    held = paper['holdings']

    try:
        client = TossClient()
        price_cache: dict = {}

        def price_fn(t):
            if t not in price_cache:
                try:
                    res = client.get_prices(t).get('result') or []
                    price_cache[t] = float(res[0]['lastPrice']) if res else None
                except (TossAPIError, ValueError, TypeError, KeyError, IndexError):
                    price_cache[t] = None
            return price_cache[t]

        # 보유 종목 현재가 확보 (마킹·청산용)
        for t in list(held.keys()):
            price_fn(t)

        # held를 process_exits/entries가 기대하는 {ticker:{shares,last}} 형태로
        held_view = {t: {'shares': held[t]['shares'], 'last': price_fn(t) or held[t].get('buy_price', 0)}
                     for t in held}

        regime = signals.get('regime', {})
        exits = te.process_exits(signals.get('exits', []), held_view, held)
        paper_value = mark_to_market(paper, price_cache)
        entries = te.process_entries(signals.get('entries', {}), regime, held_view,
                                     paper_value, paper['cash'], price_fn)
        intents = exits + entries

        # 체결가 맵 (의도 종목 가격 확보)
        for it in intents:
            price_fn(it['ticker'])
        n = apply_fills(paper, intents, price_cache)
        value = mark_to_market(paper, price_cache)
    except (TossAPIError, KeyError, IndexError) as e:
        print(f'토스 조회 실패 — 중단: {e}'); return 1

    PAPER_FILE.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding='utf-8')
    today = datetime.today().strftime('%Y-%m-%d')
    append_equity({'date': today, 'value': value, 'cash': round(paper['cash'], 2),
                   'n_pos': len(paper['holdings']), 'trades': n})

    print(f"[forward {today}] 페이퍼 평가액 ${value:,.2f} | 현금 ${paper['cash']:,.2f} "
          f"| 보유 {len(paper['holdings'])}종목 | 오늘 체결 {n}건")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
