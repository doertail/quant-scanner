"""
toss_order.py — 토스 단일 주문 CLI (실거래, 가드레일)

⚠️ 실제 돈이 움직인다. 되돌릴 수 없다.

기본 동작 = DRY 프리뷰 (주문 바디·현재가·평가금액·장운영 표시, 전송 안 함).
실제 전송은 --yes 가 있을 때만. 킬스위치(kill_switch.flag) 활성 시 차단.

사용:
  python toss_order.py PPL SELL 12              # 드라이 프리뷰 (전송 안 함)
  python toss_order.py PPL SELL 12 --yes        # 실제 시장가 매도
  python toss_order.py 005930 BUY 10 --type LIMIT --price 70000 --yes
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
KILL_SWITCH = BASE_DIR / "kill_switch.flag"


def _market_for(symbol: str) -> str:
    return "KR" if symbol.isdigit() else "US"


def _is_open(client: TossClient, country: str) -> bool:
    try:
        cal = client.get_market_calendar(country)
        reg = ((cal.get("result") or {}).get("today") or {}).get("regularMarket") or {}
        s, e = reg.get("startTime"), reg.get("endTime")
        if not (s and e):
            return False
        now = datetime.now(timezone.utc)
        return datetime.fromisoformat(s) <= now <= datetime.fromisoformat(e)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="토스 단일 주문 (실거래, 기본 드라이)")
    ap.add_argument("symbol")
    ap.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    ap.add_argument("qty", nargs="?", help="수량 (또는 --amount 사용)")
    ap.add_argument("--amount", help="금액 기반 주문 (수량 대신)")
    ap.add_argument("--type", default="MARKET", choices=["MARKET", "LIMIT"])
    ap.add_argument("--price", help="LIMIT 가격")
    ap.add_argument("--account", help="accountSeq (생략 시 첫 계좌)")
    ap.add_argument("--yes", action="store_true", help="실제 전송 (없으면 드라이 프리뷰)")
    args = ap.parse_args()

    if KILL_SWITCH.exists():
        print("🛑 킬스위치 활성 — 주문 차단"); return 2

    side = args.side.upper()
    sym = args.symbol
    country = _market_for(sym)

    try:
        client = TossClient()
        seq = args.account or client.get_accounts()["result"][0]["accountSeq"]
        body = TossClient.build_order_body(
            sym, side, quantity=args.qty, order_amount=args.amount,
            order_type=args.type, price=args.price,
        )
        # 현재가/장운영 (프리뷰용)
        px = None
        try:
            res = client.get_prices(sym).get("result") or []
            px = float(res[0]["lastPrice"]) if res else None
        except (TossAPIError, ValueError, KeyError, IndexError):
            pass
        mkt_open = _is_open(client, country)
    except (TossAPIError, KeyError, IndexError) as e:
        print(f"❌ 준비 실패: {e}", file=sys.stderr); return 1

    est = f"{country} 현재가 {px} | 예상금액 ~{float(args.qty)*px:,.2f}" if (px and args.qty) else f"현재가 {px}"
    print("\n" + "=" * 50)
    print(f" 주문 {'전송' if args.yes else 'DRY 프리뷰'}  계좌 seq={seq}")
    print("=" * 50)
    print(f" 바디: {json.dumps(body, ensure_ascii=False)}")
    print(f" {est}")
    print(f" 장 상태: {'🟢 OPEN' if mkt_open else '🔴 CLOSED (시장가 거부/예약될 수 있음)'}")
    print("=" * 50)

    if not args.yes:
        print(" (DRY — 전송 안 함. 실제 주문하려면 --yes 추가)")
        return 0

    if not mkt_open:
        print(" ⚠️ 정규장 미개장 — 시장가 주문이 거부되거나 예약될 수 있음. 그래도 전송 시도.")

    try:
        result = client.create_order(
            seq, sym, side, quantity=args.qty, order_amount=args.amount,
            order_type=args.type, price=args.price,
        )
        print(f" ✅ 주문 전송됨:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
        return 0
    except TossAPIError as e:
        print(f" ❌ 주문 실패: {e}", file=sys.stderr)
        if e.body:
            print(f"    응답: {e.body}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
