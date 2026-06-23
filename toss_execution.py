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
