"""
backtest_improve.py — 수익 개선 아이디어 검증 (전체 기간 2005-2026, 엄밀판)

backtest_v4 엔진 재사용. 데이터/지표는 1회 준비하되, **국면(regime)은 설정마다
재계산**한다 — VIX 밴드(VIX_PANIC/DANGER) 같은 파라미터가 regime_df 에 미리
구워지므로, 재계산하지 않으면 몽키패치가 무효가 되기 때문.

원칙:
  · DCA(qqqm)는 eq_hist(메인 자산)에 포함되지 않는 별도 외부적립 트랙 → 전략 수익
    개선 레버가 아니므로 검증 대상에서 제외.
  · "좋은 개선" = ΔSharpe>0 이면서 MDD 크게 안 나빠짐. ΔCAGR만 크고 MDD도 커지면 레버리지.
  · B 관련 레버(집중↑/손절↑/사이징↑)의 수익 증가분은 NDX100 생존편향이 섞여 과대평가.
  · 백테스트의 C·D는 둘 다 SPY/QQQ 지수 패닉매수 → 생존편향 없음(깨끗).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import yfinance as yf

import backtest_v4 as bt

BASE = dict(
    RISK_PER_TRADE=bt.RISK_PER_TRADE, MAX_CAP_PER_STOCK=bt.MAX_CAP_PER_STOCK,
    A_MAX_POS=bt.A_MAX_POS, B_MAX_POS=bt.B_MAX_POS, B_RANK_TOP=bt.B_RANK_TOP,
    B_ATR_MULT=bt.B_ATR_MULT, A_RSI_BUY=bt.A_RSI_BUY, A_RSI_PARTIAL=bt.A_RSI_PARTIAL,
    A_ATR_MULT=bt.A_ATR_MULT, C_POSITION_PCT=bt.C_POSITION_PCT, VIX_C_EXIT=bt.VIX_C_EXIT,
    D_POSITION_PCT=bt.D_POSITION_PCT, VIX_PANIC=bt.VIX_PANIC, VIX_DANGER_LOW=bt.VIX_DANGER_LOW,
)

# (라벨, 편향, {전역 오버라이드})  — 16개 아이디어 + 2개 조합
IDEAS = [
    ("베이스라인",            "—",  {}),
    ("1.사이징↑2%",          "⚠️", dict(RISK_PER_TRADE=0.02, MAX_CAP_PER_STOCK=0.20)),
    ("2.C비중↑40%",          "✅", dict(C_POSITION_PCT=40.0)),
    ("3.C장기보유<15",        "✅", dict(VIX_C_EXIT=15.0)),
    ("4.B집중top10%",        "❌", dict(B_RANK_TOP=0.10)),
    ("5.포지션수↑15",         "⚠️", dict(A_MAX_POS=15, B_MAX_POS=15)),
    ("6.패닉임계↓27",         "✅", dict(VIX_PANIC=27.0)),          # C 진입 잦게 + A 조기재개
    ("7.위험존완화28",         "⚠️", dict(VIX_DANGER_LOW=28.0)),     # A/B 차단구간 축소
    ("8.B완화top40%",        "⚠️", dict(B_RANK_TOP=0.40)),
    ("9.B손절넓게x4",         "⚠️", dict(B_ATR_MULT=4.0)),
    ("10.A익절늦게R60",       "✅", dict(A_RSI_PARTIAL=60.0)),
    ("11.A손절넓게x4",        "⚠️", dict(A_ATR_MULT=4.0)),
    ("12.D비중↑40%",         "✅", dict(D_POSITION_PCT=40.0)),
    ("13.종목캡↑25%",         "⚠️", dict(MAX_CAP_PER_STOCK=0.25)),
    ("14.A엄격R30",          "✅", dict(A_RSI_BUY=30.0)),
    ("15.C거의안팔기<8",       "✅", dict(VIX_C_EXIT=8.0)),
    ("16.A익절일찍R45",       "✅", dict(A_RSI_PARTIAL=45.0)),
    ("조합:깨끗(2,3,6,12)",   "✅", dict(C_POSITION_PCT=40.0, VIX_C_EXIT=15.0,
                                       VIX_PANIC=27.0, D_POSITION_PCT=40.0)),
    ("조합:공격(전부)",       "⚠️", dict(RISK_PER_TRADE=0.02, MAX_CAP_PER_STOCK=0.25,
                                       A_MAX_POS=15, B_MAX_POS=15, B_RANK_TOP=0.10,
                                       B_ATR_MULT=4.0, C_POSITION_PCT=40.0, VIX_C_EXIT=8.0,
                                       VIX_PANIC=27.0, D_POSITION_PCT=40.0, A_RSI_PARTIAL=60.0)),
]


def apply(ov):
    for k, v in BASE.items():
        setattr(bt, k, ov.get(k, v))


def setup():
    bt.log.info("데이터/지표 준비 (1회)...")
    sp500 = bt.get_sp500_tickers()
    ndx100 = bt.get_nasdaq100_tickers()
    base = list(set(sp500 + ndx100 + ["QQQ", "SPY", "HYG", "QQQM"]))
    raw = yf.download(base, start=bt.START, end=bt.END,
                      group_by="ticker", threads=True, progress=False)
    vix_raw = yf.download("^VIX", start=bt.START, end=bt.END,
                          progress=False, multi_level_index=False)
    stock_a = bt.build_stock_data(raw, sp500)
    stock_b = bt.build_stock_data(raw, ndx100)
    qqq_ohlc = raw["QQQ"][["High", "Low", "Close"]].dropna()
    hyg_cl = raw["HYG"]["Close"].dropna()
    spy_df = raw["SPY"][["Close"]].dropna()
    qqq_df = raw["QQQ"][["Close"]].dropna()
    qqqm_df = raw["QQQM"][["Close"]].dropna()
    vix_cl = vix_raw["Close"].dropna()
    etf_ind = {}
    for t in ["SPY", "QQQ"]:
        d = raw[t][["Open", "High", "Low", "Close", "Volume"]].copy().dropna(subset=["Close"])
        etf_ind[t] = bt.compute_indicators(d)
    all_dates = sorted(set(d for sd in (stock_a, stock_b)
                           for df in sd.values() for d in df.index))
    breadth = bt.precompute_breadth(stock_a)
    mom_rank, mom_rs = bt.precompute_momentum_ranks(stock_b, qqq_df)
    return dict(stock_a=stock_a, stock_b=stock_b, qqqm_df=qqqm_df, spy_df=spy_df, qqq_df=qqq_df,
                etf_ind=etf_ind, mom_rank=mom_rank, mom_rs=mom_rs, all_dates=all_dates,
                qqq_ohlc=qqq_ohlc, vix_cl=vix_cl, hyg_cl=hyg_cl, breadth=breadth)


def run_cfg(ctx, dates):
    # 국면을 현재 전역값으로 재계산 (VIX 밴드 패치 반영)
    regime = bt.build_regime_series(ctx["qqq_ohlc"], ctx["breadth"], ctx["vix_cl"],
                                    ctx["hyg_cl"], ctx["all_dates"])
    return bt.run_backtest(ctx["stock_a"], ctx["stock_b"], dates, regime, ctx["qqqm_df"],
                           etf_data={"SPY": ctx["spy_df"], "QQQ": ctx["qqq_df"]},
                           etf_indicators=ctx["etf_ind"],
                           mom_rank_df=ctx["mom_rank"], mom_rs_df=ctx["mom_rs"])["main"]


def main():
    ctx = setup()
    full = [d for d in ctx["all_dates"]
            if pd.Timestamp("2005-01-01") <= d <= pd.Timestamp("2026-12-31")]

    rows = []
    for label, clean, ov in IDEAS:
        apply(ov)
        rows.append((label, clean, run_cfg(ctx, full)))
    apply({})

    base = rows[0][2]
    W = 104
    print("\n" + "=" * W)
    print("  💡 수익 개선 아이디어 16종 백테스트 (전체 2005-2026)  vs 베이스라인".center(W - 10))
    print("=" * W)
    print(f"{'아이디어':<20}{'편향':<5}{'CAGR':>8}{'MDD':>9}{'Sharpe':>8}{'Calmar':>8}{'최종$':>14}{'ΔCAGR':>8}{'ΔShp':>7}")
    print("-" * W)
    for label, clean, m in rows:
        dc, ds = m["cagr"] - base["cagr"], m["sharpe"] - base["sharpe"]
        flag = ""
        if label != "베이스라인":
            if ds > 0.02 and m["mdd"] >= base["mdd"] - 1.0:
                flag = "  ⭐개선"
            elif dc > 0 and m["mdd"] < base["mdd"] - 3.0:
                flag = "  (레버리지)"
        print(f"{label:<20}{clean:<5}{m['cagr']:>7.2f}%{m['mdd']:>8.1f}%{m['sharpe']:>8.2f}{m['calmar']:>8.2f}${m['final']:>12,.0f}{dc:>+7.2f}%{ds:>+7.2f}{flag}")
    print("-" * W)
    print("\n* ⭐개선 = Sharpe 향상 & MDD 거의 유지.  (레버리지) = 수익↑지만 MDD도 크게↑(위험조정 개선 아님).")
    print("* ⚠️/❌는 NDX100(B) 생존편향이 수익 증가분에 섞여 과대평가됐을 수 있음. C·D는 지수라 깨끗.")


if __name__ == "__main__":
    main()
