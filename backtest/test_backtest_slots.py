from backtest_slots import concurrency


def test_concurrency_is_holding_days_over_span():
    # 10건 × 100일 보유 / 1000일 = 평균 1자리를 항상 물고 있었다는 뜻
    assert concurrency({"n": 10, "avg_hold_days": 100}, 1000) == 1.0
    # 회전이 빠른 슬리브(많은 거래·짧은 보유)와 느린 슬리브가 같은 자리를 쓸 수 있다
    assert concurrency({"n": 100, "avg_hold_days": 10}, 1000) == 1.0
    assert concurrency({"n": 0, "avg_hold_days": 0}, 1000) == 0.0
    assert concurrency({"n": 10, "avg_hold_days": 100}, 0) == 0.0   # 0 나눗셈 안 터짐
