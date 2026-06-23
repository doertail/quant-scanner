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

def test_make_intent_fields():
    it = te.make_intent('buy', 'DOW', 13, 30.5, strategy='A', signal=None)
    assert it['side'] == 'buy' and it['ticker'] == 'DOW'
    assert it['qty'] == 13 and it['ref_price'] == 30.5
    assert it['strategy'] == 'A' and it['mode'] == 'DRY'

def test_format_intent_line_readable():
    it = te.make_intent('sell', 'HSY', 2, 168.6, strategy='A', signal='STOP')
    line = te.format_intent(it)
    assert 'SELL' in line and 'HSY' in line and '2' in line and 'STOP' in line
