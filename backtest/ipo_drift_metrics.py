"""Pure, dependency-free metrics for the IPO drift backtest.

Kept import-free so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations


def forward_return(closes: list[float], idx: int, horizon: int) -> float | None:
    """Return (closes[idx+horizon] - closes[idx]) / closes[idx], or None if out of range / bad entry."""
    end = idx + horizon
    if idx < 0 or end >= len(closes):
        return None
    entry = closes[idx]
    if entry is None or entry <= 0:
        return None
    exit_px = closes[end]
    if exit_px is None:
        return None
    return (exit_px - entry) / entry


def summarize(values: list[float | None]) -> dict:
    """Aggregate a list of returns. None values are dropped.

    Returns {n, mean, median, win_rate}; all-None/empty -> Nones with n=0.
    """
    clean = [v for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    mean = sum(clean) / n
    ordered = sorted(clean)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    win_rate = sum(1 for v in clean if v > 0) / n
    return {"n": n, "mean": mean, "median": median, "win_rate": win_rate}
