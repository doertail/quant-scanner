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
