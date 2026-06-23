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
