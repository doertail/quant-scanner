# 토스 자동매매 실행 레이어 (Phase 1: 드라이런) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** scanner의 signals.json을 읽어 토스 실계좌 데이터(보유·예수금·시세)와 대조하고, 안전장치를 거쳐 **의도한 주문을 로그·디스코드로만 알리는** 드라이런 실행기를 만든다. 실제 주문(POST /orders)은 절대 호출하지 않는다.

**Architecture:** 결정 로직은 순수 함수로 분리해 단위 테스트하고, 토스 I/O·디스코드는 얇은 셸에서 호출한다. 안전장치 순수 함수(`check_price_sanity`/`count_positions_by_strategy`/`calc_shares`)는 기존 `execution_layer.py`에서 import(DRY). `--live` 플래그는 존재하되 Phase 1에서는 즉시 거부.

**Tech Stack:** Python 3, pytest, requests, python-dotenv, 기존 `toss_client.TossClient`.

---

### Task 1: 스캐폴딩 + 순수 결정 함수 + 테스트 토대

**Files:**
- Create: `toss_execution.py`
- Create: `test_toss_execution.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `test_toss_execution.py`

```python
import toss_execution as te


def test_decide_exit_qty_tp1_is_half():
    assert te.decide_exit_qty('TP1', 10.0) == 5.0

def test_decide_exit_qty_full_otherwise():
    assert te.decide_exit_qty('STOP', 10.0) == 10.0
    assert te.decide_exit_qty('TP2', 7.5) == 7.5

def test_is_core_true_only_for_core_strategy():
    assert te.is_core({'strategy': 'Core'}) is True
    assert te.is_core({'strategy': 'A'}) is False
    assert te.is_core({}) is False
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: FAIL (module/함수 없음)

- [ ] **Step 3: 최소 구현** — `toss_execution.py`

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add toss_execution.py test_toss_execution.py
git commit -m "feat(toss-exec): 드라이런 실행기 스캐폴딩 + 순수 결정 함수"
```

---

### Task 2: 주문 의도(OrderIntent) 생성 + 이력 기록

**Files:**
- Modify: `toss_execution.py`
- Modify: `test_toss_execution.py`

- [ ] **Step 1: 실패하는 테스트 추가** — `test_toss_execution.py`

```python
def test_make_intent_fields():
    it = te.make_intent('buy', 'DOW', 13, 30.5, strategy='A', signal=None)
    assert it['side'] == 'buy' and it['ticker'] == 'DOW'
    assert it['qty'] == 13 and it['ref_price'] == 30.5
    assert it['strategy'] == 'A' and it['mode'] == 'DRY'

def test_format_intent_line_readable():
    it = te.make_intent('sell', 'HSY', 2, 168.6, strategy='A', signal='STOP')
    line = te.format_intent(it)
    assert 'SELL' in line and 'HSY' in line and '2' in line and 'STOP' in line
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: FAIL (make_intent/format_intent 없음)

- [ ] **Step 3: 구현 추가** — `toss_execution.py` (decide/is_core 아래)

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add toss_execution.py test_toss_execution.py
git commit -m "feat(toss-exec): 주문 의도 생성/포맷/이력 기록"
```

---

### Task 3: 토스 계좌 스냅샷 (보유·예수금·held 맵)

**Files:**
- Modify: `toss_execution.py`
- Modify: `test_toss_execution.py`

순수 변환 함수 `parse_account_snapshot(holdings_resp, usd_cash)`를 테스트한다 (토스 API 응답 dict → 사용 가능한 형태). 실제 API 호출은 셸에서.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
SAMPLE_HOLDINGS = {
    "result": {
        "marketValue": {"amount": {"krw": "1464250", "usd": "5503.13"}},
        "items": [
            {"symbol": "TSLA", "marketCountry": "US", "quantity": "1.76",
             "currency": "USD", "lastPrice": "396.4"},
            {"symbol": "472150", "marketCountry": "KR", "quantity": "50",
             "currency": "KRW", "lastPrice": "29285"},
        ],
    }
}

def test_parse_account_snapshot_us_only_held():
    snap = te.parse_account_snapshot(SAMPLE_HOLDINGS, usd_cash=4254.67)
    assert snap['usd_cash'] == 4254.67
    assert snap['account_value_usd'] == 5503.13 + 4254.67
    # 미국 보유만 held에 (국내 472150 제외)
    assert 'TSLA' in snap['held'] and '472150' not in snap['held']
    assert snap['held']['TSLA']['shares'] == 1.76
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: FAIL (parse_account_snapshot 없음)

- [ ] **Step 3: 구현 추가**

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add toss_execution.py test_toss_execution.py
git commit -m "feat(toss-exec): 토스 계좌 스냅샷 파싱/조회"
```

---

### Task 4: 청산(exits) 처리 — 드라이런

실제 보유(US) + Core 아님인 종목만 매도 의도 생성.

**Files:**
- Modify: `toss_execution.py`
- Modify: `test_toss_execution.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
def test_process_exits_skips_core_and_unheld():
    held = {'HSY': {'shares': 2.0, 'last': 168.6}, 'TSLA': {'shares': 1.76, 'last': 396.4}}
    port = {'HSY': {'strategy': 'A'}, 'TSLA': {'strategy': 'Core'}}
    exits = [
        {'ticker': 'HSY', 'signal': 'STOP', 'strategy': 'A'},     # 매도 대상
        {'ticker': 'TSLA', 'signal': 'STOP', 'strategy': 'Core'}, # Core → 스킵
        {'ticker': 'NVDA', 'signal': 'STOP', 'strategy': 'B'},    # 미보유 → 스킵
    ]
    intents = te.process_exits(exits, held, port)
    assert len(intents) == 1
    assert intents[0]['ticker'] == 'HSY' and intents[0]['qty'] == 2.0
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: FAIL (process_exits 없음)

- [ ] **Step 3: 구현 추가**

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add toss_execution.py test_toss_execution.py
git commit -m "feat(toss-exec): 청산 처리(드라이런) — Core/미보유 제외"
```

---

### Task 5: 진입(entries) 처리 — 드라이런 (A/B 각 1건, C 전체)

가격 sanity·포지션 캡·예수금 검사 후 매수 의도 생성. 현재가는 주입(테스트 용이).

**Files:**
- Modify: `toss_execution.py`
- Modify: `test_toss_execution.py`

- [ ] **Step 1: 실패하는 테스트 추가**

```python
def test_process_entries_caps_and_cash():
    # price_fn: 스캐너가와 동일 현재가 반환 (sanity 통과)
    price_fn = lambda t: {'DOW': 30.0, 'AAPL': 190.0}.get(t)
    regime = {'allow_entry_a': True, 'allow_entry_b': True, 'vix': 16.5}
    entries = {
        'A': [{'ticker': 'DOW', 'close': 30.0, 'stop': 26.7, 'rsi': 28.7}],
        'B': [{'ticker': 'AAPL', 'close': 190.0, 'stop': 175.0, 'rsi': 70}],
        'C': [],
    }
    held = {}
    intents = te.process_entries(entries, regime, held, account_value=100000.0,
                                 usd_cash=100000.0, price_fn=price_fn)
    tickers = {i['ticker'] for i in intents}
    assert tickers == {'DOW', 'AAPL'}
    assert all(i['side'] == 'buy' and i['qty'] >= 1 for i in intents)

def test_process_entries_blocks_when_cash_too_low():
    price_fn = lambda t: 30.0
    regime = {'allow_entry_a': True, 'allow_entry_b': False}
    entries = {'A': [{'ticker': 'DOW', 'close': 30.0, 'stop': 26.7, 'rsi': 28.7}], 'B': [], 'C': []}
    intents = te.process_entries(entries, regime, {}, account_value=100000.0,
                                 usd_cash=5.0, price_fn=price_fn)
    assert intents == []   # 예수금 $5 < 1주 가격 → 진입 불가
```

- [ ] **Step 2: 실패 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: FAIL (process_entries 없음)

- [ ] **Step 3: 구현 추가**

```python
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
```

참고: held엔 전략 태그가 없어 포지션 캡 카운트는 portfolio.json 기준으로 main에서 별도 적용(아래 Task 6). 본 함수는 예수금·sanity·중복보유만 책임.

- [ ] **Step 4: 통과 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add toss_execution.py test_toss_execution.py
git commit -m "feat(toss-exec): 진입 처리(드라이런) — sanity/예수금/중복 검사"
```

---

### Task 6: main 셸 — 킬스위치·장운영·--live 거부·디스코드 리포트

**Files:**
- Modify: `toss_execution.py`

- [ ] **Step 1: 구현 추가** (테스트는 수동 통합 실행으로 대체 — I/O 위주)

```python
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
    """미국 정규장 개장 여부(best-effort). 실패 시 안전하게 False."""
    try:
        from datetime import timezone
        cal = client.get_market_calendar('US')
        reg = ((cal.get('result') or {}).get('today') or {}).get('regularMarket') or {}
        s, e = reg.get('startTime'), reg.get('endTime')
        if not (s and e):
            return False
        now = datetime.now(timezone.utc)
        return datetime.fromisoformat(s) <= now <= datetime.fromisoformat(e)
    except Exception as ex:
        log.warning(f'장운영 조회 실패(보수적으로 미개장 처리): {ex}')
        return False


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
```

- [ ] **Step 2: 기존 테스트 회귀 확인**

Run: `./venv/bin/python -m pytest test_toss_execution.py -q`
Expected: PASS (9 passed) — main 추가로 깨지지 않음

- [ ] **Step 3: 문법/임포트 확인**

Run: `./venv/bin/python -c "import toss_execution"`
Expected: 에러 없음

- [ ] **Step 4: 실제 드라이런 1회 (실주문 없음 — 안전)**

Run: `./venv/bin/python toss_execution.py`
Expected: 콘솔/디스코드에 "DRY-RUN" 리포트, "의도 N건 (실제 주문 없음)" 로그. 토스 계좌엔 아무 변화 없음.

- [ ] **Step 5: --live 거부 확인**

Run: `./venv/bin/python toss_execution.py --live`
Expected: "❌ --live는 Phase 2까지 비활성" 출력 후 종료(코드 2)

- [ ] **Step 6: 커밋**

```bash
git add toss_execution.py
git commit -m "feat(toss-exec): main 셸 — 킬스위치/장운영/리포트/--live 거부"
```

---

### Task 7: .gitignore + 문서화

**Files:**
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: .gitignore 추가**

`.gitignore`에 아래 두 줄 추가:
```
toss_execution_history.json
toss_execution.log
```

- [ ] **Step 2: CLAUDE.md 핵심 파일 표에 추가**

토스 섹션 파일 표에 행 추가:
```
| `toss_execution.py` | 토스 자동매매 실행기 (Phase 1 드라이런) — signals.json+실계좌 대조, 안전장치 후 의도 주문을 로그/디스코드. 실주문 미구현(--live 거부) |
```

- [ ] **Step 3: 커밋**

```bash
git add .gitignore CLAUDE.md
git commit -m "docs(toss-exec): gitignore 이력/로그 + CLAUDE.md 문서화"
```

---

## 완료 기준 (Phase 1)

- `pytest test_toss_execution.py` 전부 통과
- `python toss_execution.py` 가 실제 계좌 변경 없이 의도 주문을 정확히 리포트
- Core 종목 매도 안 함 / 미보유 매도 안 함 / 예수금 부족 시 진입 스킵 / 킬스위치 동작 / 장 마감 시 경고
- `--live` 는 거부됨 (실주문 경로 미존재)

## Phase 2 (별도 계획 — 이 계획 범위 밖)

- `TossClient.create_order()` 구현 (POST /api/v1/orders 스키마 확인 필요)
- `--live` 활성화 + 소액 실거래 1회 수동 검증
- 일일 손실 한도에 전일 기준선 영속화 (현재 구조는 once-daily라 의미 약함)
