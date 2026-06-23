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
