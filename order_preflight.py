"""
order_preflight.py — 주문 직전 점검 (DRY-RUN, 실제 주문 없음)

종목·매매방향·수량을 주면 주문 전에 확인해야 할 모든 정보를 한 번에 조회해
GO / NO-GO 리포트를 출력한다. **POST /orders 는 호출하지 않는다 (조회 전용).**

점검 항목:
  종목정보 · 현재가 · 호가(best bid/ask) · 상/하한가 · 매수유의사항 ·
  장 운영시간(현재 개장 여부) · 매매수수료 · 환율(미국주식) ·
  매수: 예수금(매수가능금액) / 매도: 판매가능수량

사용:
    python order_preflight.py 005930 --side buy  --qty 10
    python order_preflight.py TSLA   --side sell --qty 1.5
    python order_preflight.py AAPL   --side buy  --qty 2 --account 1 --raw
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError


def _dig(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _try(label, fn, errors):
    """API 호출을 감싸 실패해도 진행. 실패 시 errors에 기록하고 None 반환."""
    try:
        return fn()
    except TossAPIError as e:
        errors.append(f"{label}: {e}")
        return None


def _market_open(cal: dict) -> str:
    """현재 개장 여부. today + previousBusinessDay 세션 모두 확인
    (미국장은 KST 자정을 넘기므로 활성 세션이 전일자에 들어있을 수 있음)."""
    now = datetime.now(timezone.utc)
    for key in ("today", "previousBusinessDay", "nextBusinessDay"):
        reg = (_dig(cal, "result", key, "regularMarket")
               or _dig(cal, "result", key, "integrated", "regularMarket"))
        if not reg:
            continue
        start, end = reg.get("startTime"), reg.get("endTime")
        if not (start and end):
            continue
        try:
            if datetime.fromisoformat(start) <= now <= datetime.fromisoformat(end):
                return f"🟢 정규장 OPEN (~{end[11:16]})"
        except ValueError:
            continue
    return "🔴 정규장 CLOSED"


def main() -> int:
    ap = argparse.ArgumentParser(description="주문 직전 점검 (실제 주문 없음)")
    ap.add_argument("symbol", help="종목 심볼 (국내 6자리 / 미국 티커)")
    ap.add_argument("--side", choices=["buy", "sell"], default="buy", help="매매 방향")
    ap.add_argument("--qty", type=float, required=True, help="주문 수량")
    ap.add_argument("--account", help="accountSeq (생략 시 첫 계좌)")
    ap.add_argument("--raw", action="store_true", help="조회 원본 JSON 출력")
    args = ap.parse_args()

    load_dotenv()
    try:
        client = TossClient()
    except TossAPIError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    # 계좌 결정
    accounts = _try("계좌목록", client.get_accounts, [])
    acc_list = _dig(accounts, "result", default=[]) if accounts else []
    if args.account:
        seq = args.account
    elif acc_list:
        seq = acc_list[0].get("accountSeq")
    else:
        print("❌ 계좌를 찾을 수 없음", file=sys.stderr)
        return 1

    sym = args.symbol
    errors: list = []
    raw: dict = {}

    stock = _try("종목정보", lambda: client.get_stocks(sym), errors)
    price = _try("현재가", lambda: client.get_prices(sym), errors)
    book = _try("호가", lambda: client.get_orderbook(sym), errors)
    limits = _try("상하한가", lambda: client.get_price_limits(sym), errors)
    warns = _try("유의사항", lambda: client.get_stock_warnings(sym), errors)
    commissions = _try("수수료", lambda: client.get_commissions(seq), errors)

    raw.update(stock=stock, price=price, orderbook=book, limits=limits,
               warnings=warns, commissions=commissions)

    # 종목 메타
    info = (_dig(stock, "result", default=[None]) or [None])[0] or {}
    name = info.get("name", sym)
    currency = info.get("currency") or _dig(price, "result", default=[{}])[0].get("currency", "")
    country = "KR" if currency == "KRW" else "US"

    cal = _try("장운영", lambda: client.get_market_calendar(country), errors)
    raw["calendar"] = cal

    fx = None
    if currency == "USD":
        fx = _try("환율", lambda: client.get_exchange_rate("USD", "KRW"), errors)
        raw["exchange_rate"] = fx

    # 기준가: 매수=최우선매도호가, 매도=최우선매수호가, 폴백=현재가
    last = _f(_dig(price, "result", default=[{}])[0].get("lastPrice"))
    asks = _dig(book, "result", "asks", default=[])
    bids = _dig(book, "result", "bids", default=[])
    best_ask = _f(asks[0]["price"]) if asks else None
    best_bid = _f(bids[0]["price"]) if bids else None
    ref = (best_ask if args.side == "buy" else best_bid) or last

    # 수수료율
    comm_rate = None
    for c in _dig(commissions, "result", default=[]) or []:
        if c.get("marketCountry") == country:
            comm_rate = _f(c.get("commissionRate"))
            break

    # 측면별 가용성
    available = None
    avail_label = ""
    if args.side == "buy":
        bp = _try("예수금", lambda: client.get_buying_power(seq, currency), errors)
        raw["buying_power"] = bp
        available = _f(_dig(bp, "result", "cashBuyingPower"))
        avail_label = "매수가능금액"
    else:
        sq = _try("판매가능수량", lambda: client.get_sellable_quantity(seq, sym), errors)
        raw["sellable_quantity"] = sq
        available = _f(_dig(sq, "result", "sellableQuantity"))
        avail_label = "판매가능수량"

    if args.raw:
        print(json.dumps(raw, ensure_ascii=False, indent=2))

    # ── 리포트 ────────────────────────────────────────────────────────────
    cur_sym = "₩" if currency == "KRW" else "$"
    notional = ref * args.qty if ref else None
    comm_est = notional * comm_rate / 100 if (notional and comm_rate is not None) else None

    print(f"\n{'='*52}")
    print(f" 주문 직전 점검  [{args.side.upper()}]  {sym} {name}")
    print(f" 계좌 seq={seq} · {country} · {currency}")
    print(f"{'='*52}")
    print(f" 현재가      : {cur_sym}{last:,.4f}" if last else " 현재가      : -")
    print(f" 최우선 매도호가(ask): {cur_sym}{best_ask:,.4f}" if best_ask else " 최우선 매도호가: -")
    print(f" 최우선 매수호가(bid): {cur_sym}{best_bid:,.4f}" if best_bid else " 최우선 매수호가: -")
    ul = _dig(limits, "result", "upperLimitPrice")
    ll = _dig(limits, "result", "lowerLimitPrice")
    if ul or ll:
        print(f" 상/하한가   : {ul} / {ll}")
    print(f" 장 상태     : {_market_open(cal) if cal else '조회 실패'}")

    # 유의사항
    wlist = _dig(warns, "result", default=[]) or []
    if wlist:
        types = ", ".join(w.get("warningType", "?") for w in wlist)
        print(f" ⚠️ 유의사항  : {types}  (주문 전 반드시 확인)")
    else:
        print(f" 유의사항    : 없음")

    print(f"{'-'*52}")
    print(f" 주문 수량   : {args.qty}")
    print(f" 기준가      : {cur_sym}{ref:,.4f}  ({'매도호가' if args.side=='buy' else '매수호가'} 기준)" if ref else " 기준가: - (산정 불가)")
    if notional is not None:
        print(f" 예상 주문금액: {cur_sym}{notional:,.2f}")
        if currency == "USD":
            rate = _f(_dig(fx, "result", "rate"))
            if rate:
                print(f"   └ 원화환산 : ₩{notional*rate:,.0f}  (USD/KRW {rate:,.2f})")
    if comm_rate is not None:
        print(f" 수수료율    : {comm_rate}%" + (f"  → 예상수수료 {cur_sym}{comm_est:,.2f}" if comm_est is not None else ""))
    if available is not None:
        print(f" {avail_label}: {cur_sym if args.side=='buy' else ''}{available:,.4f}")

    # ── GO / NO-GO ────────────────────────────────────────────────────────
    print(f"{'-'*52}")
    blockers = []
    if args.side == "buy":
        need = (notional or 0) + (comm_est or 0)
        if available is None:
            blockers.append("매수가능금액 조회 실패")
        elif need > available:
            blockers.append(f"잔액 부족: 필요 {cur_sym}{need:,.2f} > 가능 {cur_sym}{available:,.2f}")
    else:
        if available is None:
            blockers.append("판매가능수량 조회 실패")
        elif args.qty > available:
            blockers.append(f"수량 부족: 주문 {args.qty} > 보유 {available}")
    if wlist:
        blockers.append("매수 유의사항 존재")
    if cal and "OPEN" not in _market_open(cal):
        blockers.append("정규장 미개장(시간외/예약 여부 확인 필요)")

    if blockers:
        print(" 🔴 NO-GO — 다음 항목 확인 필요:")
        for b in blockers:
            print(f"    • {b}")
    else:
        print(" 🟢 GO — 주문 조건 충족 (실제 주문은 3단계에서 수행)")

    if errors:
        print(f"{'-'*52}")
        print(" 조회 경고:")
        for e in errors:
            print(f"    • {e}")
    print(f"{'='*52}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
