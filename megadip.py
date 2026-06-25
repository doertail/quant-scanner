"""
megadip.py — 메가캡 딥 매수 전략 모니터 (ABC와 별개, 자문용)

검증(backtest_megacap_dip): MSFT급 메가캡이 RSI<30(과매도)일 때 진입, RSI>50
(반등 완료) 또는 120일 후 청산하면 QQQ를 살짝 웃돈다(+1%p, 생존편향 감안 마진).

이 모듈은 ABC 스캐너를 건드리지 않는 **별도 자문 신호기**:
  · 관리 포지션(megadip.json) → 청산 신호 (RSI>50 또는 120일 → 매도 고려)
  · 메가캡 워치리스트 미보유 → 진입 후보 (RSI<30)
실제 매매는 사람이 토스앱/`toss_order.py`로. 신념 보유(TSLA 등)는 등록 안 함.

사용: python megadip.py            # 신호 + 디스코드
      python megadip.py --no-send  # 콘솔만
"""

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from indicators import compute_indicators

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
POSITIONS = BASE_DIR / "megadip.json"   # 관리 중인 딥 포지션 {ticker:{entry_date,entry_price}}

MEGA = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL", "CRM", "ADBE", "NFLX", "AMD"]
RSI_IN, RSI_OUT, MAX_HOLD = 30, 50, 120


def classify(rsi, managed: bool, days_held: int | None = None):
    """신호 분류 (순수). 반환 (코드, 설명)."""
    if managed:
        if rsi is None:
            return ("?", "RSI 조회 실패")
        if rsi > RSI_OUT:
            return ("EXIT", f"반등 완료(RSI {rsi:.0f}>{RSI_OUT}) → 매도 고려")
        if days_held is not None and days_held >= MAX_HOLD:
            return ("EXIT", f"보유 {days_held}일 ≥ {MAX_HOLD} → 매도 고려")
        return ("HOLD", f"반등 대기 (RSI {rsi:.0f})")
    if rsi is None:
        return ("-", "")
    return ("ENTRY", f"과매도(RSI {rsi:.0f}<{RSI_IN}) → 딥 진입 후보") if rsi < RSI_IN else ("WATCH", "")


def _days_since(d: str | None) -> int | None:
    if not d:
        return None
    try:
        return (date.today() - date.fromisoformat(d)).days
    except ValueError:
        return None


def fetch_rsi(tickers):
    out = {}
    try:
        raw = yf.download(tickers, period="6mo", group_by="ticker",
                          threads=True, progress=False)
        for t in tickers:
            try:
                d = compute_indicators(raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna())
                out[t] = float(d["RSI"].iloc[-1])
            except Exception:
                out[t] = None
    except Exception:
        out = {t: None for t in tickers}
    return out


def section_lines(positions: dict, rsi: dict) -> list:
    """메가캡 딥 섹션 본문 라인(헤더/펜스 없음) — daily_report에서 재사용."""
    L = ["🎯 메가캡 딥"]
    held_lines = []
    for t, info in (positions or {}).items():
        code, desc = classify(rsi.get(t), managed=True, days_held=_days_since(info.get("entry_date")))
        mark = "🔵" if code == "EXIT" else "⚪"
        held_lines.append(f"  {mark} {t} [{code}] {desc}")
    if held_lines:
        L.append("  관리:")
        L += held_lines
    else:
        L.append("  관리: 없음")
    cands = [f"{t}(RSI {rsi[t]:.0f})" for t in MEGA
             if t not in (positions or {}) and classify(rsi.get(t), managed=False)[0] == "ENTRY"]
    L.append(f"  진입후보(RSI<30): {', '.join(cands) if cands else '없음'}")
    return L


def build_report(positions: dict, rsi: dict) -> str:
    L = [f"🎯 **메가캡 딥 전략 {datetime.today().strftime('%Y-%m-%d')}**", "```"]
    L += section_lines(positions, rsi)[1:]   # 헤더 라인 중복 제거
    L.append("```")
    L.append("※ ABC와 별개 자문 신호. 실제 매매는 수동. 생존편향 감안 기대 ≈ QQQ+소폭.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="메가캡 딥 전략 모니터")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    try:
        positions = json.loads(POSITIONS.read_text(encoding="utf-8")) if POSITIONS.exists() else {}
    except ValueError:
        positions = {}
    rsi = fetch_rsi(MEGA)
    report = build_report(positions, rsi)
    print(report)
    if not args.no_send:
        from notify import send_discord
        send_discord(report)
        print("📤 디스코드 전송 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
