"""Plain-assert tests for ipo_drift_metrics. Run: python3 backtest/test_ipo_drift_metrics.py"""
from ipo_drift_metrics import forward_return, summarize


def test_forward_return_basic():
    closes = [100.0, 110.0, 121.0, 90.0]
    assert forward_return(closes, 0, 1) == 0.10
    assert abs(forward_return(closes, 0, 2) - 0.21) < 1e-9
    assert forward_return(closes, 0, 3) == -0.10


def test_forward_return_out_of_range():
    closes = [100.0, 110.0]
    assert forward_return(closes, 0, 5) is None
    assert forward_return(closes, 1, 1) is None


def test_forward_return_negative_idx():
    assert forward_return([100.0, 110.0], -1, 1) is None


def test_forward_return_zero_entry():
    assert forward_return([0.0, 50.0], 0, 1) is None


def test_summarize_basic():
    s = summarize([0.10, -0.05, 0.20, -0.10])
    assert s["n"] == 4
    assert abs(s["mean"] - 0.0375) < 1e-9
    assert abs(s["median"] - 0.025) < 1e-9
    assert s["win_rate"] == 0.5


def test_summarize_odd_median():
    s = summarize([0.10, -0.05, 0.20])
    assert abs(s["median"] - 0.10) < 1e-9


def test_summarize_empty():
    s = summarize([])
    assert s == {"n": 0, "mean": None, "median": None, "win_rate": None}


def test_summarize_ignores_none():
    s = summarize([0.10, None, -0.10, None])
    assert s["n"] == 2
    assert s["win_rate"] == 0.5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All metrics tests passed.")
