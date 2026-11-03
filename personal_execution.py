"""
personal_execution.py — 내 전략 자동매매 드라이런 (주문 없음)

규칙 (BACKTESTS.md §13, docs/personal-investment-system.md):
  1. 공포 사다리: 전일 VIX가 30/35/40을 넘을 때마다, 공포 시작 시점 대기자금의 1/3씩
     TLT 매도 → QQQM 매수. VIX < 20이면 공포 종료, 안 쓴 몫은 다음 공포까지 보관.
  2. 비중 상한: ETF 아닌 단일 종목이 USD 계좌의 10%를 넘으면 넘는 만큼 매도.
  3. 대기 지정가(standing_orders.json): 만료일까지 매일 다시 걸 주문.
  4. 적립 여력: 예수금이 QQQM 적립 10거래일분 밑이면 경고.
의도 주문을 디스코드·이력에 남길 뿐 POST /orders는 호출하지 않는다.

사용: python personal_execution.py [--no-send]
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from notify import send_discord
from personal_policy import DAILY_QQQM_USD

PANIC_STATE = BASE_DIR / "panic_state.json"
STANDING = BASE_DIR / "standing_orders.json"
HISTORY = BASE_DIR / "personal_execution_history.json"
KILL_SWITCH = BASE_DIR / "kill_switch.flag"

LADDER = (30.0, 35.0, 40.0)
REARM_BELOW = 20.0
POT_TICKER, CORE_TICKER = "TLT", "QQQM"
ETFS = {"QQQM", "SPYM", "TLT", "SGOV", "QQQ", "SPY"}
MAX_WEIGHT = 0.10
RUNWAY_WARN_DAYS = 10


def panic_step(state: dict, vix: float, pot_usd: float) -> tuple[dict, list]:
    """공포 사다리 한 단계 (순수). state = {active, done, tranche}. 반환 (새 state, 의도 목록)."""
    s = {"active": False, "done": 0, "tranche": 0.0, **state}
    intents = []
    if not s["active"] and vix > LADDER[0]:
        s.update(active=True, done=0, tranche=round(pot_usd / len(LADDER), 2))
    if s["active"]:
        while s["done"] < len(LADDER) and vix > LADDER[s["done"]]:
            amt = round(min(s["tranche"], pot_usd), 2)
            pot_usd -= amt
            s["done"] += 1
            if amt > 0:
                why = f"공포 {s['done']}차 (VIX {vix:.1f} > {LADDER[s['done'] - 1]:.0f})"
                intents += [{"side": "SELL", "ticker": POT_TICKER, "usd": amt, "why": why},
                            {"side": "BUY", "ticker": CORE_TICKER, "usd": amt, "why": why}]
        if vix < REARM_BELOW:
            s.update(active=False, done=0, tranche=0.0)  # 남은 몫은 TLT로 그대로 보관
    return s, intents


def weight_intents(held: dict, account_usd: float) -> list:
    """비중 상한 초과분 매도 의도 (순수). held = {ticker: {shares, last}}."""
    out = []
    for t, h in held.items():
        value = h["shares"] * h["last"]
        if t in ETFS or account_usd <= 0 or value <= account_usd * MAX_WEIGHT:
            continue
        excess = value - account_usd * MAX_WEIGHT
        out.append({"side": "SELL", "ticker": t, "usd": round(excess, 2),
                    "why": f"비중 {value / account_usd:.0%} > {MAX_WEIGHT:.0%} — 초과분만"})
    return out


def standing_intents(orders: list, held: dict, today: date) -> list:
    """만료 전이고 아직 보유 중인 대기 지정가 (순수)."""
    out = []
    for o in orders:
        if date.fromisoformat(o["until"]) < today:
            continue
        have = held.get(o["symbol"], {}).get("shares", 0)
        qty = min(o["qty"], have) if o["side"] == "SELL" else o["qty"]
        if qty > 0:
            out.append({"side": o["side"], "ticker": o["symbol"], "qty": qty, "limit": o["limit"],
                        "why": f"대기 지정가 (~{o['until']})"})
    return out


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def fetch_snapshot(client) -> dict:
    """토스 실계좌 → {usd_cash, account_value_usd, held{ticker:{shares,last}}} (미국 종목만)."""
    seq = client.get_accounts()["result"][0]["accountSeq"]
    r = client.get_holdings(seq).get("result", {}) or {}
    usd_cash = _f((client.get_buying_power(seq, "USD").get("result") or {}).get("cashBuyingPower"))
    mkt_usd = _f((((r.get("marketValue") or {}).get("amount")) or {}).get("usd"))
    held = {it["symbol"]: {"shares": _f(it.get("quantity")), "last": _f(it.get("lastPrice"))}
            for it in r.get("items", []) or [] if it.get("marketCountry") == "US"}
    return {"usd_cash": usd_cash, "account_value_usd": round(mkt_usd + usd_cash, 2), "held": held}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _vix_prev_close() -> float | None:
    import yfinance as yf
    try:
        return float(yf.Ticker("^VIX").history(period="5d")["Close"].dropna().iloc[-1])
    except Exception:
        return None


def fmt(it: dict) -> str:
    size = f"{it['qty']}주 @ ${it['limit']}" if "qty" in it else f"${it['usd']:,.2f}"
    return f"{it['side']:4} {it['ticker']:5} {size}  ← {it['why']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    if KILL_SWITCH.exists():
        print("🛑 킬스위치 활성 — 중단"); return 0

    from toss_client import TossClient, TossAPIError
    try:
        snap = fetch_snapshot(TossClient())
    except (TossAPIError, KeyError, IndexError) as e:
        print(f"❌ 토스 조회 실패: {e}", file=sys.stderr); return 1
    held, cash, acct = snap["held"], snap["usd_cash"], snap["account_value_usd"]

    vix = _vix_prev_close()
    intents, notes = [], []
    if vix is None:
        notes.append("⚠️ VIX 조회 실패 — 공포 사다리 판단 건너뜀")
    else:
        pot = held.get(POT_TICKER, {"shares": 0, "last": 0})
        state, panic = panic_step(_load(PANIC_STATE, {}), vix, pot["shares"] * pot["last"])
        PANIC_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        intents += panic
        if state["active"]:
            notes.append(f"공포 진행 중 — {state['done']}/{len(LADDER)}차 완료, 1회분 ${state['tranche']:,.0f}")
    intents += weight_intents(held, acct)
    intents += standing_intents(_load(STANDING, []), held, date.today())
    runway = cash / DAILY_QQQM_USD
    if runway < RUNWAY_WARN_DAYS:
        notes.append(f"💸 예수금 ${cash:,.0f} = 적립 {runway:.0f}거래일분 — 입금 필요")

    now = datetime.today().strftime("%Y-%m-%d %H:%M")
    L = [f"🧪 **내 전략 자동매매 DRY-RUN** {now}  (주문 안 함)",
         f"VIX {vix if vix is None else round(vix, 1)} · 계좌 ${acct:,.0f} · 예수금 ${cash:,.0f} (적립 {runway:.0f}일분)",
         "```", *(fmt(i) for i in intents or []), *([] if intents else ["오늘 할 주문 없음"]), "```", *notes]
    msg = "\n".join(L)
    print(msg)

    hist = _load(HISTORY, [])
    hist.append({"ts": now, "vix": vix, "intents": intents, "notes": notes})
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_send:
        send_discord(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
