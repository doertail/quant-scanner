"""Plain-assert tests for ipo_size_metrics. Run: python3 backtest/test_ipo_size_metrics.py"""
from ipo_size_metrics import cluster_intensity, median_split, pearson

# universe tuple shape: (ticker, ipo_date, ai_related, deal_size_b, mktcap_b)
_UNIV = [
    ("A", "2020-01-01", False, 1.0, 10.0),
    ("B", "2020-02-01", False, 2.0, 20.0),
    ("C", "2020-03-15", False, 4.0, 40.0),
    ("D", "2021-06-01", False, 8.0, 80.0),
]


def test_cluster_intensity_includes_self_and_window():
    # A on 2020-01-01: within +-90d are A, B (31d), C (74d); D is far. 1+2+4=7
    assert cluster_intensity(_UNIV, 0, 90) == 7.0


def test_cluster_intensity_isolated_event():
    # D on 2021-06-01 has no other event within +-90d -> just itself
    assert cluster_intensity(_UNIV, 3, 90) == 8.0


def test_cluster_intensity_narrow_window():
    # A with a 40-day window: only B is 31d away, C is 74d away -> A + B = 3
    assert cluster_intensity(_UNIV, 0, 40) == 3.0


def test_median_split_even():
    events = [{"v": 3}, {"v": 1}, {"v": 4}, {"v": 2}]
    high, low = median_split(events, "v")
    assert sorted(e["v"] for e in low) == [1, 2]
    assert sorted(e["v"] for e in high) == [3, 4]


def test_median_split_odd_high_gets_extra():
    events = [{"v": 1}, {"v": 2}, {"v": 3}]
    high, low = median_split(events, "v")
    assert sorted(e["v"] for e in low) == [1]
    assert sorted(e["v"] for e in high) == [2, 3]


def test_pearson_perfect_positive():
    assert abs(pearson([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    assert abs(pearson([1, 2, 3], [6, 4, 2]) - (-1.0)) < 1e-9


def test_pearson_drops_none_pairs():
    # None pairs dropped; remaining (1,2),(3,6) are perfectly correlated
    assert abs(pearson([1, None, 3], [2, 9, 6]) - 1.0) < 1e-9
    # None on the ys side takes the same path: remaining (2,4),(3,6)
    assert abs(pearson([1, 2, 3], [None, 4, 6]) - 1.0) < 1e-9


def test_pearson_too_few_pairs():
    assert pearson([1], [2]) is None
    assert pearson([1, None], [None, 2]) is None


def test_pearson_zero_variance():
    assert pearson([5, 5, 5], [1, 2, 3]) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All size-metrics tests passed.")
