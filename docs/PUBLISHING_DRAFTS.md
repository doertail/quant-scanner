# 게시용 Draft (Reddit / HN / X)

3가지 플랫폼에 맞는 톤으로 작성한 게시 초안. 그대로 쓰거나 본인 어조에 맞게 수정.

---

## Reddit — r/algotrading

**Title** (한 가지 골라서):
- `I open-sourced a multi-strategy quant scanner with reproducible backtests — and one finding broke my prior`
- `Built a 4-strategy RSI/momentum/VIX scanner. The macro regime ablation backtest gave me an uncomfortable result.`

**Body**:

After running my personal trading scanner for a year, I cleaned it up and published it as two repos:

- **quant-scanner** ([github.com/doertail/quant-scanner](https://github.com/doertail/quant-scanner)) — the full system. S&P 500 + Nasdaq-100 universe, 4 strategies (RSI mean-reversion, momentum ranking, VIX panic-buy, crypto momentum), 3-layer macro regime filter, earnings + news sentiment filters.
- **quant-skills** ([github.com/doertail/quant-skills](https://github.com/doertail/quant-skills)) — three pluggable Claude Skills extracted from the scanner: earnings-blocker, vix-regime, macro-regime-3layer.

What might be useful here:

1. **All claims are backed by reproducible backtest scripts** in `backtest/`. The headline numbers in the README all come from `backtest_<name>.py` you can `python` directly.

2. **Earnings filter ablation** — I added a "block signals within ±2 days of earnings" filter and measured it. On 50 large caps over 2 years, near-earnings signals had a 28.6% 5-day win rate vs 51.8% for everything else. Filter effect = +23.2 pp win rate. The 28 signals it removed had an average −2.08% / 5d return.

3. **VIX 25-30 band is uniquely toxic** — 19.6% win rate vs 50%+ in adjacent bands. Above 30, mean-reversion recovers because the market is post-capitulation.

4. **The uncomfortable finding**: I built a 3-layer macro regime classifier (ADX + breadth + VIX/RV) thinking BULL regimes would be the best time for long entries. The ablation backtest shows the opposite for mean-reversion: BEAR regimes have +2.26% / 5d at 65.6% win rate, BULL has +0.80% at 55.5%. Naive "trade only in BULL" filter would destroy edge. The skill description was updated to use it as a **strategy router** (which strategy to run), not a gate (whether to run anything).

I'd genuinely value pushback on the methodology, especially:
- Strategy C (96% win rate on VIX panic-buy of SPY/QQQ) is on n=16. Is it a real edge or sampling luck?
- The regime ablation might be biased by the 2018–2026 window (US bull market with sharp BEAR pockets). How would it behave 2000–2010?

Educational/research purposes only — not financial advice. Code is MIT-licensed.

---

## Hacker News

**Title**:
- `Show HN: Quant-scanner — multi-strategy stock scanner with reproducible backtests`

**Body** (1-2 short paragraphs, HN style):

I open-sourced a personal stock scanner I've been running for a year. Two repos: [quant-scanner](https://github.com/doertail/quant-scanner) for the full system (4 strategies, 3-layer macro regime, earnings + news filters, Alpaca paper trading), and [quant-skills](https://github.com/doertail/quant-skills) for three pluggable Claude Skills extracted from it.

The reason this might be interesting: every numerical claim in the README is backed by a runnable backtest in `backtest/`. The earnings filter alone moved win rate from 28.6% to 51.8% in ablation. The 3-layer macro regime ablation gave me a counterintuitive result — mean-reversion works *better* in BEAR than BULL regimes — and I documented the failure mode of the naive "only trade in BULL" filter rather than hiding it. Educational only, MIT.

---

## X / Twitter

**Thread** (one tweet per line, ~280 chars each):

1/  I open-sourced my multi-strategy quant scanner with reproducible backtests:

   github.com/doertail/quant-scanner

   Three pluggable Claude Skills extracted from it:
   github.com/doertail/quant-skills

   Findings, including the uncomfortable ones, in the thread.

2/  **Earnings filter ablation** (50 large caps × 2y, 252 signals):
   - Within ±2d of earnings: 28.6% win rate, −2.08% mean 5d
   - Outside: 51.8% win rate, +0.16% mean 5d
   - Filter effect: +23.2 pp win rate

   The filter cleanly removes a specific class of false signals.

3/  **VIX 25-30 is uniquely toxic** for mean-reversion entries:

   ≤15:   50.0% win
   15-20: 39.6%
   20-25: 55.1% (sweet spot)
   **25-30: 19.6% (worst by far, −3.55% mean)**
   30-40: 46.7% (post-capitulation reversal)

4/  **VIX panic-buy on SPY/QQQ** (VIX>30 → ETF → VIX<20 exit), 2015-2026:
   - SPY: 96% win rate, +11.4% per trade
   - QQQ: 98% win rate, +13.5% per trade
   - But: small n=16. Single outlier loss would change the story.

5/  **The uncomfortable finding** — 3-layer macro regime ablation:
   - BULL signals: 55.5% win, +0.80% / 5d
   - SIDEWAYS:     67.7% win, +2.07%
   - BEAR:         65.6% win, +2.26%

   Mean-reversion works *better* in BEAR. Naive "only trade in BULL" destroys edge.

6/  Honest about what doesn't work too:

   - Strategy C absolute CAGR (2.55%) < SPY+QQQ B&H (11.42%).
   - Strategy D underperforms BTC buy-and-hold on raw return, only better on Sharpe (lower MDD).

   Findings published as-is. Not financial advice. MIT.

---

## 게시 시 주의사항

1. **시점**: Reddit과 HN은 주중 평일 오전(미국 시간) 게시가 노출 좋음
2. **본인 톤으로 수정**: 위 draft는 영어. 본인 한국어 게시도 가능 (r/algotrading은 영어 우세)
3. **댓글 응대 준비**: "재현 시도해봤는데 안 되더라" 같은 댓글에 빠른 응답이 신뢰감
4. **첨부**: 핵심 표를 이미지 캡처해서 같이 올리면 클릭률 ↑ (수치 한눈에 보임)
5. **본인 SNS 계정**: 본 draft가 본인 어조와 맞지 않으면 수정 필수. 자연스러움이 가장 중요
