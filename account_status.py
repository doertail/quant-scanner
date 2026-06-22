"""
account_status.py — 토스증권 계좌 현황 조회 CLI (읽기 전용)

사용:
    python account_status.py            # 모든 계좌의 잔고/보유종목 요약 출력
    python account_status.py --raw      # API 원본 JSON 그대로 출력 (필드명 확인·디버깅용)
    python account_status.py --account 1   # accountSeq=1 계좌만

응답 필드명이 문서 추정값과 다를 수 있어, 포맷팅 중 예상치 못한 구조를 만나면
해당 부분의 원본 JSON으로 폴백 출력한다. --raw로 실제 구조를 먼저 확인하면 정확.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError


def _dig(d, *keys, default=None):
    """중첩 dict에서 안전하게 값 추출. 경로가 끊기면 default."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _fmt_money(pair):
    """{'krw': '..', 'usd': '..'} 또는 단일 금액을 사람이 읽기 좋게."""
    if isinstance(pair, dict):
        parts = []
        if pair.get("krw") not in (None, "", "0"):
            parts.append(f"₩{int(float(pair['krw'])):,}")
        if pair.get("usd") not in (None, "", "0"):
            parts.append(f"${float(pair['usd']):,.2f}")
        return " / ".join(parts) if parts else "-"
    if pair is None:
        return "-"
    return str(pair)


def _print_accounts(accounts: dict) -> list:
    result = accounts.get("result", accounts)
    if not isinstance(result, list):
        print("⚠️  계좌 목록 구조가 예상과 달라 원본 출력:")
        print(json.dumps(accounts, ensure_ascii=False, indent=2))
        return []
    print(f"\n📒 계좌 {len(result)}개")
    for acc in result:
        print(
            f"  • seq={acc.get('accountSeq')}  "
            f"번호={acc.get('accountNo', '?')}  "
            f"유형={acc.get('accountType', '?')}"
        )
    return result


def _print_buying_power(seq, bp_by_ccy: dict) -> None:
    """{'KRW': resp, 'USD': resp} 형태를 받아 예수금(현금 매수가능액) 출력."""
    parts = []
    for ccy in ("KRW", "USD"):
        resp = bp_by_ccy.get(ccy)
        if not resp:
            continue
        cash = _dig(resp, "result", "cashBuyingPower")
        if cash is None:
            continue
        try:
            amt = float(cash)
        except (TypeError, ValueError):
            parts.append(f"{ccy} {cash}")
            continue
        parts.append(f"₩{int(amt):,}" if ccy == "KRW" else f"${amt:,.2f}")
    print(f"  예수금   : {' / '.join(parts) if parts else '-'}")


def _print_holdings(seq, holdings: dict) -> None:
    r = holdings.get("result", holdings)
    print(f"\n💰 계좌 seq={seq} 잔고")

    try:
        total_buy = _dig(r, "totalPurchaseAmount")
        mkt = _dig(r, "marketValue", "amount")
        pl_amt = _dig(r, "profitLoss", "amount")
        pl_rate = _dig(r, "profitLoss", "rate")

        print(f"  매입금액 : {_fmt_money(total_buy)}")
        print(f"  평가금액 : {_fmt_money(mkt)}")
        if pl_amt is not None:
            rate_str = f" ({float(pl_rate) * 100:+.2f}%)" if pl_rate is not None else ""
            print(f"  평가손익 : {_fmt_money(pl_amt)}{rate_str}")

        items = r.get("items", [])
        if not isinstance(items, list):
            raise TypeError("items가 리스트가 아님")
        if not items:
            print("  보유종목 : 없음")
            return

        print(f"  보유종목 {len(items)}개:")
        for it in items:
            sym = it.get("symbol", "?")
            name = it.get("name", "")
            qty = it.get("quantity", "?")
            avg = it.get("averagePurchasePrice", "?")
            last = it.get("lastPrice", "?")
            cur = it.get("currency", "")
            it_pl_rate = _dig(it, "profitLoss", "rate")
            pl_str = f"  손익 {float(it_pl_rate) * 100:+.2f}%" if it_pl_rate is not None else ""
            print(
                f"    - {sym} {name} | {qty}주 | "
                f"평단 {avg} → 현재 {last} {cur}{pl_str}"
            )
    except (KeyError, TypeError, ValueError) as e:
        print(f"  ⚠️  요약 포맷 실패({e}) — 원본 JSON:")
        print(json.dumps(holdings, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="토스증권 계좌 현황 조회 (읽기 전용)")
    parser.add_argument("--raw", action="store_true", help="API 원본 JSON 그대로 출력")
    parser.add_argument("--account", help="특정 accountSeq만 조회")
    args = parser.parse_args()

    load_dotenv()

    try:
        client = TossClient()
    except TossAPIError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    try:
        accounts = client.get_accounts()
    except TossAPIError as e:
        print(f"❌ 계좌 조회 실패: {e}", file=sys.stderr)
        if e.body:
            print(f"   응답 본문: {e.body}", file=sys.stderr)
        return 1

    if args.raw:
        print("=== /api/v1/accounts ===")
        print(json.dumps(accounts, ensure_ascii=False, indent=2))
    acc_list = _print_accounts(accounts)

    result = accounts.get("result", acc_list)
    seqs = [a.get("accountSeq") for a in result] if isinstance(result, list) else []
    if args.account:
        seqs = [s for s in seqs if str(s) == str(args.account)]
        if not seqs:
            print(f"❌ accountSeq={args.account} 계좌를 찾을 수 없음", file=sys.stderr)
            return 1

    for seq in seqs:
        try:
            holdings = client.get_holdings(seq)
        except TossAPIError as e:
            print(f"❌ seq={seq} 보유종목 조회 실패: {e}", file=sys.stderr)
            if e.body:
                print(f"   응답 본문: {e.body}", file=sys.stderr)
            continue
        # 예수금(매수 가능 현금) — 통화별로 조회
        bp_by_ccy = {}
        for ccy in ("KRW", "USD"):
            try:
                bp_by_ccy[ccy] = client.get_buying_power(seq, ccy)
            except TossAPIError as e:
                print(f"⚠️  seq={seq} {ccy} 예수금 조회 실패: {e}", file=sys.stderr)

        if args.raw:
            print(f"\n=== /api/v1/holdings (seq={seq}) ===")
            print(json.dumps(holdings, ensure_ascii=False, indent=2))
            print(f"\n=== /api/v1/buying-power (seq={seq}) ===")
            print(json.dumps(bp_by_ccy, ensure_ascii=False, indent=2))
        else:
            _print_holdings(seq, holdings)
            _print_buying_power(seq, bp_by_ccy)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
