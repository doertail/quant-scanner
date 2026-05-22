"""Pure, dependency-free helpers for the IPO size & clustering backtest.

Kept import-free (stdlib only) so it is unit-testable with the bare interpreter.
"""
from __future__ import annotations

from datetime import date


def cluster_intensity(universe: list[tuple], event_idx: int,
                      window_days: int) -> float:
    """Sum of deal_size_b for every universe IPO within +-window_days of the event.

    The event itself is included. `universe` entries are
    (ticker, ipo_date_iso, ai_related, deal_size_b, mktcap_b) tuples.
    """
    ref = date.fromisoformat(universe[event_idx][1])
    total = 0.0
    for entry in universe:
        d = date.fromisoformat(entry[1])
        if abs((d - ref).days) <= window_days:
            total += entry[3]
    return total


def median_split(events: list[dict], key: str) -> tuple[list[dict], list[dict]]:
    """Split events into (high, low) halves by the median of `key`.

    Sorted ascending, the lower half goes to `low`; for odd counts the extra
    element goes to `high`. Returns (high, low).
    """
    ordered = sorted(events, key=lambda e: e[key])
    mid = len(ordered) // 2
    return ordered[mid:], ordered[:mid]


def pearson(xs: list, ys: list) -> float | None:
    """Pearson correlation of xs vs ys. Pairs with a None on either side are
    dropped. Returns None if fewer than 2 valid pairs or zero variance.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    vx = sum((p[0] - mx) ** 2 for p in pairs)
    vy = sum((p[1] - my) ** 2 for p in pairs)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)
