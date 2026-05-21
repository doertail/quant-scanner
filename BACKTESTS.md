# Backtests

This document summarizes the backtest results that motivate the design choices in
`scanner_v4.py`. All backtests use **yfinance daily data**. Numbers are historical and
do not guarantee future performance.

All scripts are in `backtest/` and can be reproduced:
```bash
python backtest/backtest_<name>.py
```

---

## 1. Earnings Filter Effect (`backtest_earnings_ablation.py`)

**Question**: Does blocking entry signals within ±2 days of an earnings announcement
actually improve outcomes?

**Setup**: 50 S&P 500 large caps, 2024-01-01 → 2026-01-01.
Strategy A entry conditions (RSI<35 + Close<MA20 + Close>MA200) scanned daily.
Each signal classified as `near` (earnings ±2d) or `far` (otherwise), then forward
5-day and 20-day returns measured.

### Result

| Group | Signals | Mean 5d | Win 5d | Mean 20d | Win 20d |
|---|---|---|---|---|---|
| **Earnings ±2d** | 28 | **−2.08%** | **28.6%** | −2.89% | 32.1% |
| Other | 224 | +0.16% | 51.8% | +0.10% | 46.2% |
| **Filter effect** | — | **+2.23 pp** | **+23.2 pp** | +2.99 pp | +14.1 pp |

**Interpretation**: Signals fired within the earnings window have a sub-30% win rate
and average −2% over 5 days — i.e. they are predominantly false signals caused by
post-earnings gaps. The filter cleanly removes them.

---

## 2. VIX Regime Effect (`backtest_vix.py`)

**Question**: Do mean-reversion entries actually behave differently in different
VIX bands?

**Setup**: 30 S&P 500 stocks, 2015-01-01 → 2025-12-31. Strategy A trades grouped by
VIX level at entry.

### Result — Win rate and PnL by VIX band

| VIX band | Trades | Win rate | Mean PnL |
|---|---|---|---|
| ≤ 15 | 76 | 50.0% | +1.61% |
| 15–20 | 106 | 39.6% | +1.77% |
| **20–25 (SWEET)** | 89 | **55.1%** | **+3.06%** |
| **25–30 (DANGER)** | 51 | **19.6%** | **−3.55%** |
| 30–40 (PANIC reversal) | 30 | 46.7% | +2.65% |
| > 40 | 6 | 33.3% | −2.08% |

**Interpretation**: The 25–30 band is uniquely toxic — win rate collapses to 19.6%
with a −3.55% average loss. Above 30, mean-reversion works again because the market
is post-capitulation. The four-regime split (Normal / Sweet / Danger / Panic) used
by the `vix-regime` skill is calibrated on these bands.

---

## 3. VIX Panic Buy on Index ETFs (`backtest_strategy_c.py`)

**Question**: Does the "buy SPY/QQQ when VIX > 30" trade work?

**Setup**: Each time VIX crosses above 30, enter SPY (or QQQ) at next-day open, exit
when VIX falls back below 20. 2015–2026.

### Result

| ETF | Trades | Win rate | Mean return | Median | Avg hold | Holding-adjusted annualized |
|---|---|---|---|---|---|---|
| SPY | 16 | **96%** | +11.4% | +9.1% | 87 days | +33.1%/yr |
| QQQ | 16 | **98%** | +13.5% | +12.0% | 79 days | +43.0%/yr |
| (Baseline) Buy & Hold | — | — | — | — | — | +10.8%/yr |

**Interpretation**: Sample size is small (16 events in 11 years) but the consistency
is striking. The result motivates Strategy C: deploy capital into index ETFs at
high-VIX dislocations and exit on fear normalization.

⚠️ Small N — single outlier loss could materially alter the mean.

---

## 4. Crypto Momentum + BTC Regime Filter (`backtest_crypto_momentum.py`)

**Question**: Does a momentum strategy on crypto-related stocks (MSTR, BLOK, MARA,
RIOT, COIN, BITO) work? Does adding a "BTC > MA50" regime gate help?

**Setup**: 6 crypto-equity universe, 2018-01-02 → 2026-05-21. Entry: 6M return in
top 50% + 3M outperform BTC + Close > MA20 + VIX ≤ 30. Exit: ATR × 3 trailing or
MA50 cross-down.

### Result

| Strategy | CAGR | MDD | Sharpe | Sortino | Trades | Win rate |
|---|---|---|---|---|---|---|
| **Momentum + BTC>MA50** | +14.28% | −15.68% | 0.671 | 0.743 | 190 | 45.3% |
| Momentum (no regime) | +11.50% | −21.51% | 0.519 | 0.626 | 375 | 38.7% |
| BTC-USD Buy & Hold | +21.62% | **−81.40%** | 0.576 | 0.779 | — | — |
| QQQM Buy & Hold | +11.56% | −35.04% | 0.501 | 0.582 | — | — |

**BTC regime filter effect**: +2.78 pp CAGR, +5.83 pp MDD improvement, +0.153 Sharpe.

**Interpretation**: The strategy underperforms a simple BTC buy-and-hold on raw
CAGR (-7.34 pp) but with a vastly lower drawdown (-15.68% vs −81.40%), giving better
risk-adjusted returns. The BTC > MA50 gate is the dominant edge — without it, the
strategy is mediocre.

⚠️ This is the most controversial result: a passive BTC holder would have done better
in absolute terms over this window.

---

## 5. Full System — VIX Panic Exit Comparison (`backtest_v4.py`)

The Strategy C exit rule was tested independently against alternatives ($100K initial,
50% SPY + 50% QQQ split on each VIX>30 entry, full historical window). All entries use
the same trigger (VIX > 30 upcross); only the exit rule varies.

### Result

| Exit rule | Final | CAGR | MDD | Sharpe | Trades | Win rate | Avg P/L | Avg hold |
|---|---|---|---|---|---|---|---|---|
| **C: VIX<20 (default)** | $171K | +2.55% | −15.62% | −0.17 | 36 | **97.2%** | +8.98% | 92d |
| D: RSI≥70 | $163K | +2.32% | −19.67% | −0.21 | 38 | 86.8% | +7.78% | 128d |
| E: ATR×3 trail | $157K | +2.15% | −18.35% | −0.21 | 84 | 48.8% | +3.29% | 51d |
| H: VIX<20 OR +20% gain | $171K | +2.56% | −15.62% | −0.17 | 39 | 97.4% | +8.20% | 85d |
| **I: Panic-buy + hold forever** | **$500K** | **+7.83%** | −24.78% | **+0.42** | — | — | — | — |
| [Benchmark] SPY+QQQ B&H | **$1,006K** | **+11.42%** | −43.47% | **+0.52** | — | — | — | full |

### Macro context (5,380 trading days)
- Regime distribution: BULL 47.8% / SIDEWAYS 32.1% / BEAR 20.2%
- VIX distribution: NORMAL 67.2% / SWEET 16.5% / DANGER 8.0% / PANIC 8.3%
- Strategy A blocked: 14.6% of days. Strategy B blocked: 57.3% of days (sideways and bear).

### Honest interpretation

The "97% win rate" headline for Strategy C is real **on a per-trade basis** — but the
trade gives back its edge when capital sits idle waiting for the next VIX > 30 event.
Across a full window, simply holding SPY+QQQ produces ~4× the final capital.

Two takeaways:

1. **Strategy C is a tactical overlay, not a standalone replacement for buy-and-hold.**
   Its real role is *adding* panic-buy positions on top of a core long position — not
   replacing it.
2. **The single most profitable variant in this backtest was "I" — buy on VIX > 30,
   never sell.** Higher CAGR than the timed exits, with the trade-off of bigger
   drawdown. That contradicts the original "VIX < 20 exit" rationale and suggests the
   exit rule may be leaving alpha on the table.

These are uncomfortable findings, but they're more useful published than hidden.

---

## Caveats Common to All Backtests

- **Survivorship bias**: ticker lists used today; delisted names absent.
- **No transaction costs / slippage** (except strategy D which applies ±0.2%).
- **Lookback windows are tight in places** — Strategy C has only 16 events across 11
  years. The point estimates are striking but the confidence intervals are wide.
- **Past ≠ future**. These calibrations may stop working tomorrow.

For complete reproducibility, every script is self-contained and prints its own
config block.
