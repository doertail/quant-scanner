"""
daily_report.py — 일일 기술적 요약 리포트 → 디스코드 (LLM 없음, 비용 0)

scanner_v4의 signals.json + portfolio.json + forward_equity.json(로컬)을 읽어
한눈에 보는 요약을 디스코드로 보낸다. 펀더멘털·서술 없음 — 기술적 전략에 맞춘
"레짐 + 보유신호 + 액션 + forward vs QQQ"만. 보유 손익은 토스 실시간가로 best-effort.

사용: python daily_report.py            # 리포트 생성 + 디스코드 전송
      python daily_report.py --no-send  # 콘솔만 (전송 안 함)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SIGNALS = BASE_DIR / "signals.json"
PORTFOLIO = BASE_DIR / "portfolio.json"
FORWARD = BASE_DIR / "forward_equity.json"
RESERVE_USD = 1250  # VIX 공포 예비탄 (참고 표시용)


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def build_report(signals: dict, holdings: dict, fwd_last: dict | None,
                 prices: dict | None = None) -> str:
    prices = prices or {}
    r = signals.get("regime", {})
    date = signals.get("date", datetime.today().strftime("%Y-%m-%d"))
    L = [f"📊 **일일 리포트 {date}**", "```"]

    # 레짐
    hyg = "정상" if r.get("hyg_ok", True) else "⚠️악화"
    a = "O" if r.get("allow_entry_a") else "X"
    b = "O" if r.get("allow_entry_b") else "X"
    vix = r.get("vix")
    vix_s = f"{vix:.1f}" if isinstance(vix, (int, float)) else "?"
    L.append(f"🌐 레짐 {r.get('market_regime','?')} | VIX {vix_s}({r.get('vix_zone','?')}) "
             f"| HYG {hyg} | 진입 A:{a} B:{b}")

    # 보유
    L.append("")
    L.append(f"💼 보유 {len(holdings)}종목")
    for t, p in holdings.items():
        strat = p.get("strategy", "?")
        px = prices.get(t)
        if px and p.get("buy_price"):
            pnl = (px - p["buy_price"]) / p["buy_price"] * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            L.append(f"  {emoji} {t:6} [{strat}] {pnl:+.1f}%")
        else:
            L.append(f"  •  {t:6} [{strat}] {p.get('shares','?')}주")
    cash = signals.get("portfolio_cash")
    if cash is not None:
        L.append(f"  💵 현금 ${cash:,.0f} (예비탄 ~${RESERVE_USD:,} 포함)")

    # 액션
    L.append("")
    L.append("⚡ 액션")
    exits = signals.get("exits", []) or []
    if exits:
        for e in exits:
            L.append(f"  🔴 매도 {e.get('ticker')} [{e.get('signal')}]")
    else:
        L.append("  매도 신호 없음")
    ent = signals.get("entries", {}) or {}
    cands = []
    for strat in ("A", "B", "C", "D"):
        for c in (ent.get(strat) or [])[:3]:
            cands.append(f"{c.get('ticker')}({strat}{' RSI'+str(round(c['rsi'])) if c.get('rsi') is not None else ''})")
    if cands:
        L.append(f"  🟢 진입후보: {', '.join(cands[:6])}")
    else:
        L.append("  진입 후보 없음")

    # forward
    if fwd_last:
        L.append("")
        edge = fwd_last.get("edge_vs_qqq")
        v, q = fwd_last.get("value"), fwd_last.get("qqq")
        if v is not None and q is not None:
            sign = "✅앞섬" if (edge or 0) >= 0 else "🔻뒤짐"
            L.append(f"📈 forward: 전략 ${v:,.0f} vs QQQ ${q:,.0f} (격차 {edge:+.2f} {sign})")
    L.append("```")
    return "\n".join(L)


def fetch_prices(tickers):
    """보유 종목 현재가 best-effort (토스). 실패해도 리포트는 나감."""
    out = {}
    try:
        from toss_client import TossClient, TossAPIError
        c = TossClient()
        for t in tickers:
            try:
                res = c.get_prices(t).get("result") or []
                if res:
                    out[t] = float(res[0]["lastPrice"])
            except (TossAPIError, ValueError, KeyError, IndexError):
                pass
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="일일 기술적 리포트 → 디스코드")
    ap.add_argument("--no-send", action="store_true", help="콘솔만 (전송 안 함)")
    ap.add_argument("--no-prices", action="store_true", help="시세 조회 생략(빠름)")
    args = ap.parse_args()

    signals = _load(SIGNALS, {})
    if not signals:
        print("signals.json 없음 — scanner_v4.py 먼저 실행", file=sys.stderr)
        return 1
    holdings = _load(PORTFOLIO, {}).get("holdings", {})
    fwd = _load(FORWARD, [])
    fwd_last = fwd[-1] if fwd else None

    prices = {} if args.no_prices else fetch_prices(list(holdings.keys()))
    report = build_report(signals, holdings, fwd_last, prices)
    print(report)

    if not args.no_send:
        from notify import send_discord
        send_discord(report)
        print("📤 디스코드 전송 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
