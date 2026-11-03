import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_tactical_vs_dca as bt


def _px(qqq):
    idx = pd.date_range("2020-01-31", periods=len(qqq), freq="ME")
    cols = {t: [100.0] * len(qqq) for t in bt.TICKERS}
    cols["QQQ"] = qqq
    return pd.DataFrame(cols, index=idx)


def test_dca_constant_price_no_tax():
    px = _px([100.0] * 24)
    val, after, _, sells = bt.simulate("QQQ 적립", px, 12, 23)
    assert sells == 0 and abs(val - 12 * bt.MONTHLY * (1 - bt.COST)) < 1e-6 and val == after


def test_rebalance_gains_are_taxed(monkeypatch):
    # GTAA5: SPY만 매달 +5% → 매달 20%로 되돌리며 이익 실현 → 공제 0이면 세금이 빠져야 한다
    px = _px([100.0] * 36)
    px["SPY"] = [100.0 * 1.05 ** k for k in range(36)]
    monkeypatch.setattr(bt, "EXEMPT", 0.0)
    taxed, _, _, sells = bt.simulate("GTAA5", px, 12, 35)
    monkeypatch.setattr(bt, "TAX", 0.0)
    untaxed, _, _, _ = bt.simulate("GTAA5", px, 12, 35)
    assert sells > 0 and taxed < untaxed
