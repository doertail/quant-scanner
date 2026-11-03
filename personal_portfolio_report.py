"""Read-only account view for the user's investment policy.

Uses live Toss holdings and buying power. Does not place orders, change the
scanner portfolio, or treat the scanner's tactical strategy C as QQQM core.

Run: python personal_portfolio_report.py
"""

import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError
from personal_policy import DAILY_QQQM_USD, ROLES


ROOT = Path(__file__).resolve().parent


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def summarize(items, usd_cash):
    """Return account weights without assigning unconfirmed investment roles."""
    positions = []
    for item in items:
        if item.get("currency") != "USD":
            continue
        symbol = item.get("symbol", "?")
        market_value = number((item.get("marketValue") or {}).get("amount"))
        if not market_value:
            market_value = number(item.get("quantity")) * number(item.get("lastPrice"))
        positions.append((symbol, market_value))
    total = sum(value for _, value in positions) + usd_cash
    positions.sort(key=lambda row: row[1], reverse=True)
    return total, [(symbol, value, value / total if total else 0) for symbol, value in positions]


def live_snapshot(client, accounts=None):
    items = []
    cash = 0.0
    found = False
    if accounts is None:
        accounts = client.get_accounts().get("result", [])
    for account in accounts:
        seq = account["accountSeq"]
        holdings = client.get_holdings(seq).get("result", {})
        items.extend(holdings.get("items", []))
        cash += number(client.get_buying_power(seq, "USD").get("result", {}).get("cashBuyingPower"))
        found = True
    if not found:
        raise RuntimeError("토스 계좌를 찾지 못함")
    return items, cash


def main():
    load_dotenv(ROOT / ".env")
    client = TossClient()
    accounts = client.get_accounts().get("result", [])
    items, cash = live_snapshot(client, accounts)
    total, positions = summarize(items, cash)
    try:
        local_holdings = json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8")).get("holdings", {})
    except (FileNotFoundError, ValueError):
        local_holdings = {}
    print(f"개인 투자 정책 현황 — {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}")
    print(f"토스 API의 USD 자산: ${total:,.2f} (보유 ${total-cash:,.2f}, 예수금 ${cash:,.2f})")
    for symbol, value, weight in positions:
        role = ROLES.get(symbol, "투자 논지 미기록")
        tag = local_holdings.get(symbol, {}).get("strategy")
        if role == "투자 논지 미기록" and tag in ("A", "B", "C", "D"):
            role = f"기존 퀀트 전략 {tag}"
        print(f"  {symbol:<6} ${value:>9,.2f}  {weight:>5.1%}  {role}")
    print(f"  USD현금 ${cash:>9,.2f}  {cash/total if total else 0:>5.1%}")
    print(f"기존 QQQM ${DAILY_QQQM_USD:.0f}/거래일 기준, 추가 입금·다른 매수 없으면 약 {cash/DAILY_QQQM_USD:.0f}거래일분")
    drift = []
    for item in items:
        symbol = item.get("symbol")
        if symbol in local_holdings:
            old = number(local_holdings[symbol].get("shares"))
            live = number(item.get("quantity"))
            if abs(old - live) > 1e-6:
                drift.append(f"{symbol} 로컬 {old:g}주 / 토스 {live:g}주")
    if drift:
        print("스캐너 보유 수량 동기화 대기: " + "; ".join(drift))

    local = {}
    try:
        local = json.loads((ROOT / "external_holdings.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        pass
    api_symbols = {item.get("symbol") for item in items}
    missing = set(local) - api_symbols
    if missing:
        try:
            quoted = {row.get("symbol"): row for row in
                      (client.get_prices(sorted(missing)).get("result") or [])}
            if not missing <= quoted.keys():
                raise ValueError("국내 시세 누락")
            krw_positions = {symbol: number(local[symbol].get("shares")) *
                             number(quoted[symbol].get("lastPrice")) for symbol in missing}
            krw_cash = sum(number(client.get_buying_power(account["accountSeq"], "KRW")
                                  .get("result", {}).get("cashBuyingPower"))
                           for account in accounts)
            fx_data = client.get_exchange_rate("USD", "KRW").get("result") or {}
            fx = number(fx_data.get("midRate"))
            if fx <= 0 or any(value <= 0 for value in krw_positions.values()):
                raise ValueError("국내 가치 또는 환율 누락")
            combined = total + (sum(krw_positions.values()) + krw_cash) / fx
            print(f"확인 가능한 전체 자산: 약 ${combined:,.2f} 상당 "
                  f"(환산 USD/KRW {fx:,.1f}, {fx_data.get('validFrom')})")
            for symbol, value in sorted(krw_positions.items()):
                print(f"  {symbol:<6} ₩{value:>10,.0f}  {value/fx/combined:>5.1%}  "
                      f"국내 보유 별도 기록·토스 시세 {quoted[symbol].get('timestamp')}")
            print(f"  KRW현금 ₩{krw_cash:>10,.0f}  {krw_cash/fx/combined:>5.1%}")
            print("전체 기준 비중: " + ", ".join(
                f"{symbol} {value/combined:.1%}" for symbol, value, _ in positions))
        except (TossAPIError, ValueError, KeyError, TypeError) as exc:
            print("전체 계좌 비중 미확정: 국내 보유 가치 조회 실패 " + type(exc).__name__)
    print("QQQM 핵심 적립과 기존 스캐너 전략 C는 별개. 이 보고서는 주문 신호를 만들지 않음.")


if __name__ == "__main__":
    main()
