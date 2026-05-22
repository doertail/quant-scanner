"""Pure, dependency-free grading logic for the risk briefing pipeline.

Kept import-free so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations

GREEN, YELLOW, RED, UNKNOWN = "🟢", "🟡", "🔴", "⚪"


def grade_regime(market_regime: str | None) -> str:
    """BULL → green, SIDEWAYS → yellow, BEAR → red, anything else → unknown."""
    return {"BULL": GREEN, "SIDEWAYS": YELLOW, "BEAR": RED}.get(
        market_regime, UNKNOWN)


def grade_trend(qqq_close: float | None, ma200: float | None,
                ext_threshold: float = 0.15) -> str:
    """Red below MA200, yellow if extended > ext_threshold above it, else green."""
    if qqq_close is None or ma200 is None or ma200 <= 0:
        return UNKNOWN
    if qqq_close <= ma200:
        return RED
    if qqq_close / ma200 - 1 > ext_threshold:
        return YELLOW
    return GREEN


def grade_breadth(breadth_pct: float | None) -> str:
    """>=60 green, 40-60 yellow, <40 red."""
    if breadth_pct is None:
        return UNKNOWN
    if breadth_pct >= 60:
        return GREEN
    if breadth_pct >= 40:
        return YELLOW
    return RED


def grade_credit(hyg_ok: bool | None) -> str:
    """HYG above its MA50 → green, below → red."""
    if hyg_ok is None:
        return UNKNOWN
    return GREEN if hyg_ok else RED


def grade_vix(vix_zone: str | None) -> str:
    """NORMAL green, SWEET yellow, DANGER/PANIC red."""
    return {"NORMAL": GREEN, "SWEET": YELLOW,
            "DANGER": RED, "PANIC": RED}.get(vix_zone, UNKNOWN)


def grade_yield_spread(spread: float | None, flat_threshold: float) -> str:
    """Inverted (spread < 0) red, flat (0 to flat_threshold) yellow, else green."""
    if spread is None:
        return UNKNOWN
    if spread < 0:
        return RED
    if spread < flat_threshold:
        return YELLOW
    return GREEN


def grade_overall(grades: dict[str, str]) -> str:
    """Combine component grades into LOW / ELEVATED / HIGH. UNKNOWN is ignored."""
    reds = sum(1 for g in grades.values() if g == RED)
    yellows = sum(1 for g in grades.values() if g == YELLOW)
    if reds >= 2:
        return "HIGH"
    if reds == 1 or yellows >= 2:
        return "ELEVATED"
    return "LOW"


def diff_grades(prev: dict[str, str],
                cur: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return (name, old, new) for components whose grade changed since prev.

    Components absent from prev are not 'changes' and are skipped.
    """
    out: list[tuple[str, str, str]] = []
    for name, g in cur.items():
        if name in prev and prev[name] != g:
            out.append((name, prev[name], g))
    return out
