import forward_test as ft


def test_apply_fills_buy_deducts_cash_and_adds_holding():
    paper = {'cash': 1000.0, 'holdings': {}}
    intents = [{'side': 'buy', 'ticker': 'DOW', 'qty': 10, 'strategy': 'A'}]
    n = ft.apply_fills(paper, intents, {'DOW': 30.0})
    assert n == 1
    assert paper['cash'] == 700.0
    assert paper['holdings']['DOW']['shares'] == 10
    assert paper['holdings']['DOW']['strategy'] == 'A'


def test_apply_fills_buy_blocked_when_cash_insufficient():
    paper = {'cash': 50.0, 'holdings': {}}
    intents = [{'side': 'buy', 'ticker': 'DOW', 'qty': 10, 'strategy': 'A'}]
    n = ft.apply_fills(paper, intents, {'DOW': 30.0})
    assert n == 0 and paper['holdings'] == {} and paper['cash'] == 50.0


def test_apply_fills_sell_adds_cash_and_removes_when_zero():
    paper = {'cash': 0.0, 'holdings': {'HSY': {'shares': 2.0, 'buy_price': 197.0, 'strategy': 'A'}}}
    intents = [{'side': 'sell', 'ticker': 'HSY', 'qty': 2.0}]
    n = ft.apply_fills(paper, intents, {'HSY': 170.0})
    assert n == 1 and paper['cash'] == 340.0 and 'HSY' not in paper['holdings']


def test_mark_to_market_sums_cash_and_positions():
    paper = {'cash': 100.0, 'holdings': {'AAA': {'shares': 2.0}, 'BBB': {'shares': 3.0}}}
    val = ft.mark_to_market(paper, {'AAA': 10.0, 'BBB': 5.0})
    assert val == 100.0 + 20.0 + 15.0


def test_benchmark_value():
    paper = {'benchmark': {'qqq_shares': 2.0}}
    assert ft.benchmark_value(paper, 100.0) == 200.0
    assert ft.benchmark_value({}, 100.0) is None
    assert ft.benchmark_value(paper, None) is None
