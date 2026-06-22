"""
toss_sync.py — 토스 실계좌 → portfolio.json 동기화 (드리프트 해결)

스캐너 Phase 2가 보는 portfolio.json을 토스 실보유/예수금으로 맞춘다.
스캐너 고유 상태(strategy / trailing_stop / tp1_hit / vix_entry)는 보존하면서
실제 보유수량(shares)과 평단(buy_price)만 토스 기준으로 교정한다.

규칙:
  · 미국 종목 → 스캐너 전략 관리 대상 (holdings)
      - 기존에 있던 티커: 상태 보존, shares/buy_price만 실계좌로 갱신
      - 신규 티커: strategy="Core"(감시만, 자동매도 없음)로 추가 → 리포트에 표시
      - 실계좌에 없는 티커: 제거 (더 이상 미보유) → 리포트에 표시
  · 국내 종목(예: 472150) → external_holdings (감시용, 스캐너 전략 제외)
  · cash → 토스 USD 예수금(매수가능금액). KRW 예수금은 참고로 리포트만.

사용:
    python toss_sync.py                 # 미리보기 (dry-run, 기록 안 함)
    python toss_sync.py --apply         # portfolio.json 실제 갱신 (.bak 백업)
    python toss_sync.py --apply --discord   # 갱신 + 디스코드 리포트
"""

import argparse
import json
import shutil
import sys

from dotenv import load_dotenv

from config import PORTFOLIO_FILE
from toss_client import TossClient, TossAPIError

# 스캐너 전략 관리 대상에서 보존할 상태 필드
_PRESERVE = ("strategy", "trailing_stop", "tp1_hit", "vix_entry", "auto_invest")


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_raw_portfolio() -> dict:
    try:
        with open(PORTFOLIO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"holdings": {}, "cash": 0.0}


def build_sync(old: dict, toss_holdings: dict, usd_cash: float):
    """기존 portfolio + 토스 보유 → 새 portfolio + 변경 리포트 반환."""
    old_holdings = old.get("holdings", {})
    items = (toss_holdings.get("result") or {}).get("items", [])

    new_holdings: dict = {}
    external: dict = {}
    added, updated, removed = [], [], []

    toss_us_symbols = set()
    for it in items:
        sym = it.get("symbol")
        country = it.get("marketCountry")
        shares = _f(it.get("quantity"))
        avg = _f(it.get("averagePurchasePrice"))
        name = it.get("name", "")

        if country != "US":
            # 국내 등 → 외부 보유 (감시만)
            external[sym] = {
                "name": name,
                "shares": shares,
                "buy_price": avg,
                "currency": it.get("currency"),
                "market_country": country,
            }
            continue

        toss_us_symbols.add(sym)
        if sym in old_holdings:
            rec = dict(old_holdings[sym])  # 상태 보존
            old_shares = _f(rec.get("shares"))
            old_buy = _f(rec.get("buy_price"))
            rec["shares"] = shares
            rec["buy_price"] = round(avg, 4)
            new_holdings[sym] = rec
            if abs(old_shares - shares) > 1e-6 or abs(old_buy - avg) > 1e-4:
                updated.append(
                    f"{sym}: {old_shares}주@{old_buy} → {shares}주@{round(avg,2)}"
                )
        else:
            new_holdings[sym] = {
                "shares": shares,
                "buy_price": round(avg, 4),
                "trailing_stop": None,
                "tp1_hit": False,
                "strategy": "Core",  # 안전 기본값 — 감시만, 자동매도 없음
            }
            added.append(f"{sym} {name} ({shares}주@{round(avg,2)}) → strategy=Core")

    for sym in old_holdings:
        if sym not in toss_us_symbols:
            removed.append(f"{sym} ({_f(old_holdings[sym].get('shares'))}주)")

    new_port = dict(old)
    new_port["holdings"] = new_holdings
    new_port["cash"] = round(usd_cash, 2)
    if external:
        new_port["external_holdings"] = external
    elif "external_holdings" in new_port:
        del new_port["external_holdings"]

    report = {
        "added": added,
        "updated": updated,
        "removed": removed,
        "external": external,
        "old_cash": old.get("cash"),
        "new_cash": round(usd_cash, 2),
    }
    return new_port, report


def format_report(report: dict, applied: bool) -> str:
    L = []
    head = "✅ 동기화 적용됨" if applied else "👀 동기화 미리보기 (dry-run)"
    L.append(f"**🔄 토스 → portfolio.json {head}**")
    L.append("")
    if report["added"]:
        L.append(f"➕ 신규 ({len(report['added'])}) — Core로 추가, 전략 재태깅 권장:")
        L += [f"   • {x}" for x in report["added"]]
    if report["removed"]:
        L.append(f"➖ 제거 ({len(report['removed'])}) — 실계좌에 없음:")
        L += [f"   • {x}" for x in report["removed"]]
    if report["updated"]:
        L.append(f"🔧 수량·평단 교정 ({len(report['updated'])}):")
        L += [f"   • {x}" for x in report["updated"]]
    if report["external"]:
        L.append(f"🌐 외부 보유 (감시만, {len(report['external'])}):")
        for sym, v in report["external"].items():
            L.append(f"   • {sym} {v['name']} | {v['shares']}주 | {v.get('currency')}")
    oc, nc = report["old_cash"], report["new_cash"]
    L.append("")
    L.append(f"💵 현금(USD): {oc} → {nc}")
    if not (report["added"] or report["removed"] or report["updated"]):
        L.append("변경 없음 — 이미 동기화 상태.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="토스 실계좌 → portfolio.json 동기화")
    ap.add_argument("--apply", action="store_true", help="실제 portfolio.json 갱신 (기본은 미리보기)")
    ap.add_argument("--discord", action="store_true", help="리포트를 디스코드로 전송")
    args = ap.parse_args()

    load_dotenv()
    try:
        client = TossClient()
        seq = client.get_accounts()["result"][0]["accountSeq"]
        toss_holdings = client.get_holdings(seq)
        usd_cash = _f((client.get_buying_power(seq, "USD").get("result") or {}).get("cashBuyingPower"))
        krw_cash = _f((client.get_buying_power(seq, "KRW").get("result") or {}).get("cashBuyingPower"))
    except (TossAPIError, KeyError, IndexError) as e:
        print(f"❌ 토스 조회 실패: {e}", file=sys.stderr)
        return 1

    old = load_raw_portfolio()
    new_port, report = build_sync(old, toss_holdings, usd_cash)

    msg = format_report(report, applied=args.apply)
    print(msg)
    if krw_cash:
        print(f"(참고: KRW 예수금 ₩{int(krw_cash):,} — USD 전략엔 미반영)")

    if args.apply:
        bak = str(PORTFOLIO_FILE) + ".bak"
        try:
            shutil.copyfile(PORTFOLIO_FILE, bak)
            print(f"\n📦 백업: {bak}")
        except FileNotFoundError:
            pass
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(new_port, f, indent=2, ensure_ascii=False)
        print(f"💾 저장: {PORTFOLIO_FILE}")
    else:
        print("\n(미리보기였음 — 실제 반영하려면 --apply)")

    if args.discord:
        from notify import send_discord
        send_discord(msg)
        print("📤 디스코드 전송 완료")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
