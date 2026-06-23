import toss_execution as te


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


def test_decide_exit_qty_tp1_is_half():
    assert te.decide_exit_qty('TP1', 10.0) == 5.0

def test_decide_exit_qty_full_otherwise():
    assert te.decide_exit_qty('STOP', 10.0) == 10.0
    assert te.decide_exit_qty('TP2', 7.5) == 7.5

def test_is_core_true_only_for_core_strategy():
    assert te.is_core({'strategy': 'Core'}) is True
    assert te.is_core({'strategy': 'A'}) is False
    assert te.is_core({}) is False

def test_make_intent_fields():
    it = te.make_intent('buy', 'DOW', 13, 30.5, strategy='A', signal=None)
    assert it['side'] == 'buy' and it['ticker'] == 'DOW'
    assert it['qty'] == 13 and it['ref_price'] == 30.5
    assert it['strategy'] == 'A' and it['mode'] == 'DRY'

def test_format_intent_line_readable():
    it = te.make_intent('sell', 'HSY', 2, 168.6, strategy='A', signal='STOP')
    line = te.format_intent(it)
    assert 'SELL' in line and 'HSY' in line and '2' in line and 'STOP' in line

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
