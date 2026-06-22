"""
prices.py — 토스증권 현재가 조회 CLI (마켓 데이터, 계좌 불필요)

사용:
    python prices.py 005930 AAPL TSLA       # 종목 여러 개 (국내코드/미국티커 혼용 가능)
    python prices.py 005930,000660          # 콤마 구분도 가능
    python prices.py AAPL --raw             # 원본 JSON

2단계(시세 조회 통합)의 토대 — 스캐너가 토스 시세를 쓰도록 확장할 때 TossClient.get_prices() 재사용.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

from toss_client import TossClient, TossAPIError


def main() -> int:
    parser = argparse.ArgumentParser(description="토스증권 현재가 조회")
    parser.add_argument("symbols", nargs="+", help="종목 심볼 (공백 또는 콤마 구분, 최대 200개)")
    parser.add_argument("--raw", action="store_true", help="API 원본 JSON 출력")
    args = parser.parse_args()

    # "AAPL,TSLA" 같이 콤마로 들어온 것도 펼침
    symbols = [s for chunk in args.symbols for s in chunk.split(",") if s]

    load_dotenv()
    try:
        client = TossClient()
        data = client.get_prices(symbols)
    except TossAPIError as e:
        print(f"❌ 시세 조회 실패: {e}", file=sys.stderr)
        if e.body:
            print(f"   응답 본문: {e.body}", file=sys.stderr)
        return 1

    if args.raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    result = data.get("result", data)
    if not isinstance(result, list):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"\n📈 현재가 {len(result)}종목")
    for it in result:
        sym = it.get("symbol", "?")
        last = it.get("lastPrice", "?")
        ccy = it.get("currency", "")
        ts = it.get("timestamp") or "체결 없음"
        print(f"  {sym:<10} {last:>14} {ccy:<4}  {ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
