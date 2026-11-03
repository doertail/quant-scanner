"""Compare panic-buy schedules for a long-held Nasdaq-100 core position.

Each VIX > 30 episode receives the same $1 of *additional* cash. The regular
QQQM purchase is common to all schedules and therefore excluded. Signals use
the prior close and execute at the next adjusted QQQ open. QQQ is used for its
longer history; QQQM tracks the same index. Unused cash stays at 0% yield.

Run: python backtest/backtest_qqqm_panic_layers.py
"""

from dataclasses import dataclass

import pandas as pd
import yfinance as yf


START = "2005-01-01"
END = "2026-09-27"
BUY_COST = 0.001
HORIZONS = (252, 756)


@dataclass
class Result:
    final_value: float
    buys: int
    invested: float
    max_drawdown: float


def panic_episodes(vix: pd.Series) -> list[pd.Timestamp]:
    """One entry per VIX > 30 episode, rearmed only after VIX < 20."""
    entries = []
    armed = True
    for date, value in vix.items():
        if armed and value > 30:
            entries.append(date)
            armed = False
        elif value < 20:
            armed = True
    return entries


def compare(close: pd.Series, open_price: pd.Series, vix: pd.Series) -> None:
    data = pd.concat({"close": close, "open": open_price, "vix": vix}, axis=1).dropna()
    close, open_price, vix = data["close"], data["open"], data["vix"]
    dates = data.index
    entries = panic_episodes(vix)
    print(f"QQQ adjusted data: {dates[0].date()} to {dates[-1].date()}; VIX episodes: {len(entries)}")
    print("Extra $1 per episode; next-open fills; 0.1% buy cost; unused cash earns 0%; no sales")

    for horizon in HORIZONS:
        rows = []
        for entry in entries:
            first_i = dates.get_loc(entry) + 1
            end_i = first_i + horizon - 1
            if end_i >= len(dates):
                continue
            end_price = float(close.iloc[end_i])
            immediate = (1 - BUY_COST) / float(open_price.iloc[first_i]) * end_price
            for strategy in ("price_5_10", "price_10_20", "vix_30_40_50"):
                # First tranche is always triggered by the entry VIX close.
                result = simulate_with_entry(close, open_price, vix, entry, horizon, strategy)
                path = (1 - BUY_COST) / float(open_price.iloc[first_i]) * close.iloc[first_i:end_i + 1]
                immediate_drawdown = float((path / pd.concat([pd.Series([1.0]), path]).cummax().iloc[1:] - 1).min())
                rows.append((entry, strategy, immediate, result.final_value,
                             result.buys, result.invested, immediate_drawdown, result.max_drawdown))

        frame = pd.DataFrame(rows, columns=["entry", "strategy", "immediate", "final", "buys", "invested", "now_mdd", "layer_mdd"])
        print(f"\n{horizon // 252}-year horizon: {frame.entry.nunique()} complete episodes")
        for strategy, group in frame.groupby("strategy", sort=False):
            relative = group.final / group.immediate - 1
            counts = group.buys.value_counts().to_dict()
            print(
                f"{strategy:15} beat_now={100 * (relative > 0).mean():5.1f}% "
                f"median_delta={100 * relative.median():+6.2f}% "
                f"mean_delta={100 * relative.mean():+6.2f}% "
                f"mean_invested={100 * group.invested.mean():5.1f}% "
                f"median_mdd={100 * group.layer_mdd.median():5.1f}% "
                f"vs_now={100 * group.now_mdd.median():5.1f}% "
                f"buy_counts={counts}"
            )


def simulate_with_entry(close, open_price, vix, signal_date, horizon, strategy):
    """Seed the initial VIX order, then process each later close once."""
    signal_i = close.index.get_loc(signal_date)
    first_i = signal_i + 1
    end_i = first_i + horizon - 1
    cash = 2 / 3
    first_price = float(open_price.iloc[first_i])
    shares = (1 / 3) * (1 - BUY_COST) / first_price
    buys = 1
    next_layer = 1
    pending = False
    panic_active = True
    peak = 1.0
    max_drawdown = 0.0
    for i in range(first_i, end_i + 1):
        if pending:
            amount = min(cash, 1 / 3)
            shares += amount * (1 - BUY_COST) / float(open_price.iloc[i])
            cash -= amount
            buys += 1
            next_layer += 1
            pending = False
        if float(vix.iloc[i]) < 20:
            panic_active = False
        value = cash + shares * float(close.iloc[i])
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
        if i >= end_i or next_layer >= 3 or not panic_active:
            continue
        if strategy.startswith("price_"):
            drops = (0.05, 0.10) if strategy == "price_5_10" else (0.10, 0.20)
            pending = float(close.iloc[i]) <= first_price * (1 - drops[next_layer - 1])
        else:
            pending = float(vix.iloc[i]) > (40 if next_layer == 1 else 50)
    return Result(cash + shares * float(close.iloc[end_i]), buys, 1 - cash, max_drawdown)


if __name__ == "__main__":
    raw = yf.download(["QQQ", "^VIX"], start=START, end=END,
                      auto_adjust=True, progress=False, threads=False)
    compare(raw["Close"]["QQQ"], raw["Open"]["QQQ"], raw["Close"]["^VIX"])
