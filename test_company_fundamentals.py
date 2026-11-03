import company_fundamentals as cf
from datetime import date


def test_trailing_year_uses_fiscal_year_and_comparable_ytd():
    rows = [
        {"form": "10-K", "start": "2025-02-01", "end": "2026-01-31", "val": 100},
        {"form": "10-Q", "start": "2026-02-01", "end": "2026-07-31", "val": 70},
        {"form": "10-Q", "start": "2025-02-01", "end": "2025-07-31", "val": 50},
    ]
    got = cf.trailing_metric(rows)
    assert got["value"] == 120
    assert round(got["ytd_growth"], 2) == 0.4


def test_missing_comparable_period_is_not_invented():
    rows = [
        {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "val": 100},
        {"form": "10-Q", "start": "2026-01-01", "end": "2026-06-30", "val": 70},
    ]
    assert cf.trailing_metric(rows) is None


def test_no_quarter_after_annual_uses_annual_only():
    rows = [{"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "val": 100}]
    assert cf.trailing_metric(rows) == {"value": 100, "end": "2025-12-31", "annual_only": True}


def test_stale_share_count_cannot_value_current_company():
    assert not cf.recent_period("2023-11-08", 180, date(2026, 9, 26))
    assert cf.recent_period("2026-06-30", 180, date(2026, 9, 26))
