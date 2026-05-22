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

---

## 6. Macro Regime Filter Effect (`backtest_macro_regime_ablation.py`)

**Question**: Does gating Strategy A entries on the 3-layer macro regime (BULL / SIDEWAYS / BEAR)
improve outcomes? Are BULL signals actually better than BEAR signals?

**Setup**: 30 S&P 500 large caps, 2018–2026 (8 years). Daily 3-layer regime computed from
QQQ ADX(14) + 50-stock breadth proxy + VIX/realized-vol ratio. Strategy A signals grouped
by the regime on their entry date.

### Result — UNEXPECTED

| Regime | Signals | Mean 5d | Win 5d | Mean 20d | Win 20d |
|---|---|---|---|---|---|
| BULL | 499 | +0.80% | 55.5% | +1.57% | 59.9% |
| **SIDEWAYS** | 164 | **+2.07%** | **67.7%** | +3.15% | 64.0% |
| **BEAR** | 180 | **+2.26%** | **65.6%** | **+4.01%** | 60.0% |
| BULL vs others | — | **−1.38 pp** | **−10.7 pp** | **−2.03 pp** | — |

### Interpretation (uncomfortable)

**Strategy A signals perform *worse* in BULL regimes and *best* in BEAR regimes.**
This is the opposite of what a naive "only trade in bull markets" rule would suggest.

Why this makes sense in hindsight:
- Mean-reversion strategies are fundamentally "buy fear when others are scared."
- In BULL regimes, dips are shallow and brief — RSI rarely falls below 35 without
  immediately bouncing on weak signal quality.
- In BEAR/SIDEWAYS regimes, RSI<35 events are deeper and the snap-back is more violent.

Implications:
- **Using `macro-regime-3layer` to *block* Strategy A in non-BULL regimes is the
  wrong application.** It would systematically remove the best signals.
- The regime classifier is still useful, but as a **strategy router**, not a gate:
  - BULL → favor momentum (Strategy B)
  - SIDEWAYS / BEAR → favor mean-reversion (Strategy A)
- The current scanner does the right thing for the wrong reason: it allows A in
  SIDEWAYS and blocks B in non-BULL. The ablation confirms this directionally.

**Bottom line**: The 3-layer regime classifier *is* a discriminating signal — but its
correct use is strategy selection, not as a global "deploy long" gate. The
`macro-regime-3layer` skill description has been updated to reflect this.

---

## 7. IPO Drift — Price Action After Large IPOs (`backtest_ipo_drift.py`)

**Question**: After a large IPO, does the stock decline (Part A), and does the
broad market (SPY/QQQ) weaken (Part B)? Is the effect stronger for mega-cap AI IPOs?

**Setup**: 28 large IPOs from 2018–2025 hardcoded with their listing dates, 6 of
them AI-related (SNOW, PLTR, C3.ai, ARM, Astera Labs, CoreWeave). Forward returns
measured at 5/20/60/120/180/252 trading days. Part A enters the IPO stock at its
day-0 close (absolute + SPY-excess). Part B measures SPY/QQQ forward returns from
each IPO day-0 against an unconditional baseline (mean forward return over every
trading day 2018–2025).

### Result

**Part A — IPO stock itself**

| Group | Horizon | N | Mean abs | Win% | Mean excess vs SPY |
|---|---|---|---|---|---|
| 전체 | 5d | 28 | +8.06% | 67.9% | +8.34% |
| 전체 | 20d | 28 | +7.69% | 67.9% | +6.83% |
| 전체 | 60d | 28 | +27.06% | 64.3% | +21.69% |
| 전체 | 120d | 28 | +6.60% | 46.4% | −2.57% |
| 전체 | 180d | 28 | +20.37% | 53.6% | +7.31% |
| 전체 | 252d | 28 | +20.21% | 53.6% | +7.38% |
| AI | 5d | 6 | +7.37% | 66.7% | +9.85% |
| AI | 20d | 6 | +11.03% | 66.7% | +11.77% |
| AI | 60d | 6 | +89.74% | 83.3% | +83.12% |
| AI | 120d | 6 | +65.81% | 50.0% | +51.62% |
| AI | 180d | 6 | +63.82% | 66.7% | +42.55% |
| AI | 252d | 6 | +57.57% | 83.3% | +34.31% |

**Part B — market trend (SPY shown; QQQ figures noted in interpretation)**

| Group | Horizon | N | SPY mean | SPY baseline | SPY diff |
|---|---|---|---|---|---|
| 전체 | 5d | 28 | −0.28% | +0.26% | −0.54% |
| 전체 | 20d | 28 | +0.86% | +1.03% | −0.17% |
| 전체 | 60d | 28 | +5.37% | +3.04% | +2.33% |
| 전체 | 120d | 28 | +9.17% | +6.25% | +2.92% |
| 전체 | 180d | 28 | +13.06% | +9.55% | +3.51% |
| 전체 | 252d | 28 | +12.82% | +13.78% | −0.95% |
| AI | 5d | 6 | −2.48% | +0.26% | −2.74% |
| AI | 20d | 6 | −0.74% | +1.03% | −1.77% |
| AI | 60d | 6 | +6.62% | +3.04% | +3.57% |
| AI | 120d | 6 | +14.19% | +6.25% | +7.94% |
| AI | 180d | 6 | +21.27% | +9.55% | +11.72% |
| AI | 252d | 6 | +23.26% | +13.78% | +9.48% |

**Interpretation**: Part A contradicts the hypothesis that large IPO stocks decline
after listing — the full universe shows positive mean absolute returns at every
horizon (win rates of 46–68%), with median returns turning negative only at 120d
(−7.41%), suggesting the mean is pulled up by a handful of large winners. The AI
subset is even more extreme: the 60d mean of +89.74% (driven by post-listing
explosions in names like ARM and CoreWeave) vastly outperforms the broader group,
although the 120d win rate drops to 50%, indicating high variance rather than
consistent alpha.

Part B also contradicts the "IPO day marks a market top" hypothesis at medium-to-long
horizons — SPY diff is *positive* from 60d through 180d for both groups, meaning
markets on average *outperformed* their baseline after large IPOs. The only support
for weakness is at the very short end (5d SPY diff −0.54% for all; −2.74% for AI),
but this is too small and variable to trade. QQQ shows the same pattern (5d diff
−0.23% all-group, −2.93% AI; turning clearly positive from 60d onward).

In sum: neither the individual IPO drift-down nor the market-weakness hypothesis
is supported by this data over the 2018–2025 window.

⚠️ **AI subset N=6** — confidence intervals are very wide; treat the AI rows as
directional only, not conclusive. Forward windows of clustered IPOs (e.g. Sep–Dec
2020) overlap, so observations are not independent — no p-values are reported.
Survivorship bias: delisted large IPOs (WeWork, DIDI) are absent from the universe.

---

## 8. IPO Size & Clustering — Crowding-Out Test (`backtest_ipo_size_cluster.py`)

**Question**: Section 7 showed the market does not weaken after a large IPO on
average. This goes further: do *larger* IPOs (by deal size or market cap), or
*clustered* IPO waves, correlate with weaker SPY/QQQ forward returns — the
"crowding-out" idea that funding IPOs drains other stocks?

**Setup**: The 28-IPO universe from section 7, each hardcoded with an approximate
deal size and IPO-day market cap. Cluster intensity = sum of universe deal sizes
within ±90 days of each event. Events are median-split into HIGH/LOW buckets
(14 each) on each of the three variables; SPY/QQQ forward returns at
5/20/60/120/180/252 trading days are compared against the unconditional
2018–2025 baseline.

### Result

**Median-split — HIGH bucket "SPY weaker than LOW" horizon count (out of 6)**

| Variable | HIGH-weaker horizons |
|---|---|
| Deal size | 2/6 |
| Market cap | 2/6 |
| Cluster intensity | 1/6 |

**Correlation (Pearson r, SPY forward returns)**

| Variable | 5d | 20d | 60d | 120d | 180d | 252d |
|---|---|---|---|---|---|---|
| Deal size | +0.17 | −0.07 | −0.24 | −0.15 | −0.08 | −0.20 |
| Market cap | −0.06 | +0.11 | −0.30 | +0.04 | +0.12 | +0.06 |
| Cluster | +0.43 | +0.44 | +0.16 | +0.17 | +0.13 | −0.01 |

**Largest market-cap quartile (top 7)** — SPY return vs baseline:
- 60d: SPY +3.49% (baseline 대비 +0.45pp)
- 120d: SPY +8.71% (baseline 대비 +2.46pp)
- 252d: SPY +15.09% (baseline 대비 +1.31pp)

**Interpretation**: The crowding-out hypothesis — that larger IPOs or denser IPO
waves drain capital from existing stocks and weaken forward market returns — is not
supported by this data. HIGH-bucket events (large deal size, large market cap, or
high cluster intensity) produced SPY returns that were *weaker* than LOW-bucket
events in only 1–2 out of 6 horizons, far from the consistent 6/6 pattern the
hypothesis would require. Pearson correlations are similarly mixed: deal size shows
weak negative correlations at 60d and 252d (r = −0.24, −0.20), while market cap and
cluster intensity show near-zero or even positive correlations across most horizons
(the one exception is market cap at 60d, r = −0.30 — the largest single correlation
in the table, but isolated to that horizon).
The largest-cap quartile (ABNB, SNOW, UBER, RIVN, ARM, DASH, COIN) produced SPY
returns *above* the unconditional baseline at all three medium-to-long horizons.
In short, the data contradicts the crowding-out narrative for this universe and time
window — if anything, very large IPOs coincided with modestly stronger market
conditions, consistent with the section 7 finding that IPO days are not market tops.

⚠️ **Approximate size figures** — deal size and market cap are rounded public
estimates; the four direct listings (SPOT, COIN, PLTR, RBLX) raised no primary
proceeds, so their deal size is a first-day float-value proxy. Median split gives
N=14 per bucket — wide confidence intervals. The cluster-intensity split has a
5-way tie at the median (LYFT, UBER, PINS, ZM, CRWD all = 13.2, the 2019 IPO wave);
two go to LOW and three to HIGH by universe insertion order, so the cluster HIGH/LOW
comparison partly separates members of the same wave — read the cluster row with
that in mind. Overlapping forward windows mean observations are not independent, so
no p-values are reported. Anthropic, OpenAI, and SpaceX are private, not in the
universe, and not backtested.

---

## Caveats Common to All Backtests

- **Survivorship bias**: ticker lists used today; delisted names absent.
- **No transaction costs / slippage** (except strategy D which applies ±0.2%).
- **Lookback windows are tight in places** — Strategy C has only 16 events across 11
  years. The point estimates are striking but the confidence intervals are wide.
- **Past ≠ future**. These calibrations may stop working tomorrow.

For complete reproducibility, every script is self-contained and prints its own
config block.
