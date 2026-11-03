import megadip as m


def test_managed_exit_on_rsi_recovery():
    code, _ = m.classify(55, managed=True)
    assert code == "EXIT"


def test_managed_hold_when_still_oversold():
    code, _ = m.classify(29, managed=True)
    assert code == "HOLD"


def test_managed_exit_on_max_hold():
    code, _ = m.classify(40, managed=True, days_held=130)
    assert code == "EXIT"


def test_unmanaged_entry_when_oversold():
    code, _ = m.classify(28, managed=False)
    assert code == "ENTRY"


def test_unmanaged_watch_when_normal():
    code, _ = m.classify(45, managed=False)
    assert code == "WATCH"


def test_none_rsi_safe():
    assert m.classify(None, managed=True)[0] == "?"
    assert m.classify(None, managed=False)[0] == "-"


def test_days_held_counts_trading_days():
    # 금(10/30) → 다음 주 월(11/2): 달력 3일, 거래일 1일
    from datetime import date
    import numpy as np
    assert int(np.busday_count(date(2026, 10, 30), date(2026, 11, 2))) == 1
    assert m._days_since(date.today().isoformat()) == 0


def test_held_tickers_are_not_entry_candidates():
    lines = m.section_lines({}, {"GOOGL": 21.0, "META": 25.0}, held={"GOOGL": {}})
    cand = lines[-1]
    assert "META" in cand and "GOOGL" not in cand
