"""Plain-assert tests for risk_dashboard. Run: python3 test_risk_dashboard.py"""
from risk_dashboard import (
    GREEN, YELLOW, RED, UNKNOWN,
    grade_regime, grade_trend, grade_breadth, grade_credit, grade_vix,
    grade_yield_spread, grade_overall, diff_grades, normalize_yield,
)


def test_grade_regime():
    assert grade_regime("BULL") == GREEN
    assert grade_regime("SIDEWAYS") == YELLOW
    assert grade_regime("BEAR") == RED
    assert grade_regime(None) == UNKNOWN
    assert grade_regime("???") == UNKNOWN


def test_grade_trend():
    assert grade_trend(100.0, 90.0) == GREEN          # +11%, not extended
    assert grade_trend(110.0, 90.0) == YELLOW         # +22%, extended >15%
    assert grade_trend(85.0, 90.0) == RED             # below MA200
    assert grade_trend(90.0, 90.0) == RED             # equal counts as not above
    assert grade_trend(None, 90.0) == UNKNOWN
    assert grade_trend(100.0, None) == UNKNOWN
    assert grade_trend(100.0, 0.0) == UNKNOWN


def test_grade_breadth():
    assert grade_breadth(62.0) == GREEN
    assert grade_breadth(60.0) == GREEN
    assert grade_breadth(50.0) == YELLOW
    assert grade_breadth(40.0) == YELLOW
    assert grade_breadth(30.0) == RED
    assert grade_breadth(None) == UNKNOWN


def test_grade_credit():
    assert grade_credit(True) == GREEN
    assert grade_credit(False) == RED
    assert grade_credit(None) == UNKNOWN


def test_grade_vix():
    assert grade_vix("NORMAL") == GREEN
    assert grade_vix("SWEET") == YELLOW
    assert grade_vix("DANGER") == RED
    assert grade_vix("PANIC") == RED
    assert grade_vix(None) == UNKNOWN


def test_grade_yield_spread():
    assert grade_yield_spread(1.2, 0.5) == GREEN
    assert grade_yield_spread(0.3, 0.5) == YELLOW
    assert grade_yield_spread(0.0, 0.5) == YELLOW     # 0 is flat, not inverted
    assert grade_yield_spread(-0.4, 0.5) == RED       # inverted
    assert grade_yield_spread(None, 0.5) == UNKNOWN
    assert grade_yield_spread(0.5, 0.5) == GREEN     # at threshold = normal


def test_grade_overall():
    # 0 red, 0-1 yellow -> LOW
    assert grade_overall({"a": GREEN, "b": GREEN, "c": YELLOW}) == "LOW"
    assert grade_overall({"a": GREEN, "b": GREEN}) == "LOW"
    # 1 red OR >=2 yellow -> ELEVATED
    assert grade_overall({"a": RED, "b": GREEN}) == "ELEVATED"
    assert grade_overall({"a": YELLOW, "b": YELLOW, "c": GREEN}) == "ELEVATED"
    # >=2 red -> HIGH
    assert grade_overall({"a": RED, "b": RED, "c": GREEN}) == "HIGH"
    # UNKNOWN is ignored
    assert grade_overall({"a": GREEN, "b": UNKNOWN}) == "LOW"
    # all-UNKNOWN → LOW (documented design: UNKNOWN is ignored)
    assert grade_overall({"a": UNKNOWN, "b": UNKNOWN}) == "LOW"


def test_normalize_yield():
    assert normalize_yield(4.5) == 4.5      # normal scale, untouched
    assert normalize_yield(45.0) == 4.5     # x10 scale, divided down
    assert normalize_yield(25.0) == 2.5     # boundary: >=25 is divided


def test_diff_grades():
    prev = {"regime": GREEN, "credit": GREEN, "vix": YELLOW}
    cur = {"regime": GREEN, "credit": RED, "vix": YELLOW}
    assert diff_grades(prev, cur) == [("credit", GREEN, RED)]
    # nothing changed
    assert diff_grades(cur, cur) == []
    # a component absent from prev is not a "change"
    assert diff_grades({}, {"regime": GREEN}) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("All risk-dashboard tests passed.")
