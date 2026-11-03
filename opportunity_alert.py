"""Find one news-led US stock idea and alert the user for human review.

Read-only for brokerage accounts. Preview by default; --send posts to the
already configured Discord news webhook and records deduplication state.
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from notify import send_discord
from monitor_health import report_failure
from official_evidence import find_8k_evidence
from company_fundamentals import fundamentals, display_fundamentals
from personal_portfolio_report import live_snapshot, summarize
from toss_client import TossClient


ROOT = Path(__file__).resolve().parent
STATE = ROOT / "opportunity_alert_state.json"
PORTFOLIO = ROOT / "portfolio.json"
AV_URL = "https://www.alphavantage.co/query"
TOPICS = ("technology", "energy_transportation", "life_sciences")
LOOKBACK_HOURS = 72
MODEL = "exaone3.5:latest"
OLLAMA_URL = "http://localhost:11434/api/generate"
SKIP_SOURCES = {"MarketBeat", "GuruFocus", "Simply Wall Street", "Stock Titan",
                "Kalkine Media", "StocksToTrade", "Law Commentary"}
NOISE = re.compile(
    r"shareholder alert|class action|form 4|insider (sale|sell)|stock rating|"
    r"price target|stock moved|stock rises|stock falls|after [0-9.]+% (decline|gain)|"
    r"investigat|lawsuit|fiduciary duties|stock option|executes stock|sells shares|"
    r"submits .*report|patent verdict|stock really priced|economic moat|"
    r"stock cheap|stock pricey|can .* keep growing|could .* raise|eyes [0-9]|"
    r"labor|labour|union|strike|wage|pay raise|pay deal|engineers?|"
    r"tentative (deal|agreement)|collective bargain|workers? agreement",
    re.IGNORECASE,
)
EVENT = re.compile(
    r"agreement|contract|wins? |launch|secures?|expand|adds? |approvals?|"
    r"acquir|select|chose|chosen|deploy|partnership|order|award|adopt|"
    r"commercial|customer|deal|facility|capacity|FDA approv",
    re.IGNORECASE,
)
TICKER = re.compile(r"^[A-Z]{1,5}$")


def relevance(row: dict) -> float:
    try:
        return float(row.get("relevance_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def event_score(title: str) -> int:
    """Prefer concrete customer economics over a product mention or stock chatter."""
    low = title.lower()
    score = 0
    if re.search(r"agreement|contract|deal", low):
        score += 3
    if re.search(r"\$[0-9.,]+\s*(billion|bn|b)\b", low):
        score += 5
    elif re.search(r"\$[0-9.,]+\s*(million|mn|m)\b", low):
        score += 2
    if re.search(r"wins?|secures?|selected|chosen|customer", low):
        score += 2
    if re.search(r"adds?|launch|approvals?|partnership", low):
        score += 1
    return score


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def held_symbols() -> set[str]:
    return set((load_json(PORTFOLIO, {}).get("holdings") or {}).keys())


def fetch_articles(hours: int = LOOKBACK_HOURS) -> list[dict]:
    key = os.getenv("ALPHAVANTAGE_API_KEY")
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY 미설정")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y%m%dT%H%M")
    by_url = {}
    errors = []
    for index, topic in enumerate(TOPICS):
        if index:
            time.sleep(1.1)  # Alpha Vantage free tier: one request per second.
        try:
            r = requests.get(AV_URL, params={
                "function": "NEWS_SENTIMENT", "topics": topic, "sort": "LATEST",
                "time_from": since, "limit": 200, "apikey": key,
            }, timeout=20)
            r.raise_for_status()
            data = r.json()
            if "feed" not in data:
                message = str(data.get("Information") or data.get("Note") or "feed 없음")
                reason = "사용량 제한" if "rate limit" in message.lower() else "피드 응답 오류"
                raise RuntimeError(reason)
            for article in data["feed"]:
                if article.get("url"):
                    by_url[article["url"]] = article
        except (requests.RequestException, ValueError, RuntimeError) as e:
            # Request errors can contain the URL, including the API key.
            label = str(e) if isinstance(e, RuntimeError) else type(e).__name__
            errors.append(f"{topic}: {label}")
    if not by_url and errors:
        raise RuntimeError("뉴스 조회 실패: " + "; ".join(errors))
    return list(by_url.values())


def candidates(articles: list[dict], seen: set[str], held: set[str]) -> list[dict]:
    out = []
    for article in articles:
        title = (article.get("title") or "").strip()
        url = article.get("url") or ""
        if (not title or not url or url in seen or article.get("source") in SKIP_SOURCES
                or NOISE.search(title) or not EVENT.search(title)):
            continue
        tickers = []
        rows = sorted(article.get("ticker_sentiment") or [], key=relevance, reverse=True)
        for row in rows:
            relevant = relevance(row) >= 0.5
            ticker = row.get("ticker") or ""
            if relevant and TICKER.fullmatch(ticker) and ticker not in held:
                tickers.append(ticker)
        title_ticker = re.search(r"\(([A-Z]{1,5})\)", title)
        if title_ticker and title_ticker.group(1) in tickers:
            tickers = [title_ticker.group(1)]
        if not tickers:
            continue
        out.append({"title": title, "url": url, "source": article.get("source") or "",
                    "summary": (article.get("summary") or "")[:180],
                    "tickers": tickers[:5], "published": article.get("time_published") or ""})
    return sorted(out, key=lambda row: (event_score(row["title"]), row["published"]),
                  reverse=True)[:20]


def choose_one(items: list[dict]) -> dict | None:
    if not items:
        return None
    prompt = (
        "다음 뉴스에서 미국 주식 조사 후보 하나만 고르세요. 기사 제목·요약에 있는 사실만 사용하세요. "
        "신규 고객 계약, 실제 제품 채택, 매출로 이어질 수 있는 새 수요를 우선하세요. "
        "주가 변동, 애널리스트 의견, 소송, 정기 실적 요약, 막연한 AI 협력은 제외하세요. "
        "가장 영향이 큰 고객 구매 계약을 우선하고, 최신 기사라는 이유만으로 선택하지 마세요. "
        "아래 항목은 SEC 8-K 대조를 통과한 조사 후보입니다. 가장 먼저 검토할 한 건을 고르세요. "
        "매수 추천이나 추가 독립 검증을 주장하지 마세요. "
        "why와 unknown은 반드시 한국어로만 쓰세요. 영어 문장을 복사하지 마세요. "
        "JSON만 반환: {\"index\":정수,\"ticker\":문자열,\"why\":한국어 한 문장,"
        "\"unknown\":한국어 한 문장}. ticker는 해당 기사의 tickers 안에서 선택. "
        "예: {\"index\":0,\"ticker\":\"ABC\",\"why\":\"새 고객 계약이 매출로 이어질 수 있습니다.\","
        "\"unknown\":\"계약 이행 비용을 확인해야 합니다.\"}\n"
        + json.dumps(list(enumerate(items)), ensure_ascii=False)
    )
    r = requests.post(OLLAMA_URL, json={
        "model": MODEL, "prompt": prompt, "stream": False, "format": "json",
        "options": {"temperature": 0},
    }, timeout=90)
    r.raise_for_status()
    selection = json.loads(r.json()["response"])
    index = selection.get("index")
    if not isinstance(index, int) or not (0 <= index < len(items)):
        return None
    item = items[index]
    ticker = selection.get("ticker")
    if ticker not in item["tickers"]:
        return None
    why = str(selection.get("why") or "").strip()[:140]
    unknown = str(selection.get("unknown") or "").strip()[:140]
    if not why or not unknown:
        return None
    return {**item, "ticker": ticker, "why": why, "unknown": unknown}


def verified_items(items: list[dict]) -> list[dict]:
    """Only send a new-company lead when a recent issuer 8-K corroborates it."""
    result = []
    for item in items[:8]:
        for ticker in item["tickers"][:2]:
            evidence = find_8k_evidence(ticker, item["title"])
            if evidence:
                result.append({**item, "tickers": [ticker], "evidence": evidence})
                break
    return result


def eligible_for_buy_review(financials: dict, evidence_text: str) -> bool:
    """Allow a research priority label only when price and basic economics exist.

    This is a lead filter, not an expected-return estimate or order approval.
    """
    revenue = financials.get("revenue") or {}
    market_cap = financials.get("market_cap")
    fcf_yield = financials.get("historical_fcf_yield")
    if (not market_cap or not revenue.get("value") or
            revenue.get("ytd_growth") is None or fcf_yield is None):
        return False
    if (revenue["ytd_growth"] < 0.15 or fcf_yield < 0.02 or
            market_cap / revenue["value"] > 12):
        return False
    if re.search(r"capital expenditures|warrant|dilution|convertible notes", evidence_text,
                 re.IGNORECASE):
        return False
    return True


def conservative_analysis() -> dict:
    return {"verdict": "보류",
            "reason": "공식 사건은 확인됐지만 현재 가격 대비 수익성과 이행 비용은 아직 검증되지 않음.",
            "risk": "계약·제품 뉴스가 실제 이익과 주주 현금흐름으로 이어질지는 미확인.",
            "trigger": "비용·매출 인식·현금흐름을 확인할 회사 공시가 나오면 재평가."}


def review_candidate(item: dict) -> dict:
    """Join filing, latest Toss quote, account cash and a bounded local analysis.

    The local model may explain a lead, but cannot approve a purchase. Its
    earlier AKAM test ignored $5.5B of capex and recommended buying.
    """
    client = TossClient()
    rows = client.get_prices([item["ticker"]]).get("result") or []
    price = next((row for row in rows if row.get("symbol") == item["ticker"]), None)
    if not price or not price.get("lastPrice"):
        raise RuntimeError(f"{item['ticker']} 토스 가격 없음")
    holdings, cash = live_snapshot(client)
    total, positions = summarize(holdings, cash)
    financials = fundamentals(item["ticker"], float(price["lastPrice"]))
    financial_line = display_fundamentals(financials)
    prompt = (
        "너는 개인 투자 조사 보조자다. 아래의 SEC 공식 공시 발췌와 토스 가격/계좌 정보만 사용해 "
        "이 새 종목의 공식 근거와 빠진 경제성을 요약한다. "
        "사용자는 달러 자산과 3~5년 수익을 선호하고 QQQM을 거래일마다 $20 적립한다. "
        "CRWD는 뉴스에서 유망하다고 판단해 약 200만원을 빠르게 산 적이 있다. "
        "추가 실적 발표를 기다려야 한다는 획일적 기준을 만들지 마라. "
        "설비투자·워런트·계약 해지 조건이 있으면 반드시 위험에 넣어라. "
        "매수 금액을 임의로 정하지 마라. 각 문장은 한국어로 짧게 써라. "
        "source_quote는 아래 SEC 발췌문에서 15~120자를 정확히 복사해라. "
        "매수 검토는 추가 조사 우선순위일 뿐 매수 승인이나 주문 지시가 아니다. "
        "JSON만 반환: {\"verdict\":\"매수 검토|보류|제외\",\"reason\":\"한 문장\","
        "\"risk\":\"한 문장\",\"trigger\":\"재평가할 확인 자료 한 문장\","
        "\"source_quote\":\"SEC 문구\"}.\n"
        f"기사 제목: {item['title']}\n기사 요약: {item['summary']}\n"
        f"SEC 공시 발췌: {item['evidence']['excerpt']}\n"
        f"SEC 최근 재무: {financial_line}. 과거 현금흐름에는 새 계약의 미래 투자비가 반영되지 않았을 수 있음.\n"
        f"현재 가격: ${price['lastPrice']} ({price.get('timestamp')})\n"
        f"USD 계좌 총액: ${total:.2f}, 현금: ${cash:.2f}; 보유: {positions}"
    )
    try:
        response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt,
                                                   "stream": False, "format": "json",
                                                   "options": {"temperature": 0}}, timeout=120)
        response.raise_for_status()
        analysis = json.loads(response.json()["response"])
        if not isinstance(analysis, dict):
            analysis = conservative_analysis()
        quote = str(analysis.get("source_quote") or "").strip()
        if (analysis.get("verdict") not in {"매수 검토", "보류", "제외"} or
                not 15 <= len(quote) <= 120 or quote not in item["evidence"]["excerpt"] or
                any(not str(analysis.get(field) or "").strip()
                    for field in ("reason", "risk", "trigger"))):
            analysis = conservative_analysis()
    except (requests.RequestException, ValueError, KeyError, TypeError):
        analysis = conservative_analysis()
    if analysis["verdict"] == "매수 검토" and not eligible_for_buy_review(
            financials, item["evidence"]["excerpt"]):
        analysis["verdict"] = "보류"
        analysis["reason"] = "공식 사건은 확인됐지만 성장·현금흐름·가격 또는 비용 검증이 부족함."
    excerpt = item["evidence"]["excerpt"].lower()
    if "capital expenditures" in excerpt:
        analysis["reason"] = "고객 계약은 확인됐지만 선행 설비투자 회수와 실제 이익률이 아직 불확실함."
        analysis["risk"] = "공시에 선행 설비투자가 명시됨. 계약액을 이익으로 볼 수 없음."
        analysis["trigger"] = "설비투자 재원·계약 이행 비용·매출 인식 계획을 확인한 뒤 재평가."
        analysis["verdict"] = "보류"
    if "warrant" in excerpt:
        analysis["risk"] += " 고객 워런트의 주식 희석 가능성도 확인 필요."
    return {**item, "price": price, "cash": cash, "total": total,
            "positions": positions,
            "financials": financials, "analysis": analysis}


def format_alert(item: dict) -> str:
    result = item["analysis"]
    price = item["price"]
    existing = ", ".join(f"{symbol} {weight:.0%}" for symbol, _, weight in
                         (item.get("positions") or [])[:3])
    return (f"🔎 **새 회사 검토 · {item['ticker']} · {result['verdict']}**\n"
            f"기사: [{item['title']}](<{item['url']}>)\n"
            f"공식 근거: [SEC {item['evidence']['form']}](<{item['evidence']['url']}>) "
            f"({item['evidence']['filed']})\n"
            f"판단: {result['reason']}\n위험: {result['risk']}\n"
            f"재평가 조건: {result['trigger']}\n"
            f"SEC 재무: [원자료](<{item['financials']['source']}>) "
            f"{display_fundamentals(item['financials'])}. "
            "과거 현금흐름에는 새 계약 투자비가 반영되지 않았을 수 있음.\n"
            f"토스 가격 ${price['lastPrice']} ({price.get('timestamp')}); "
            f"USD 예수금 ${item['cash']:,.0f}, QQQM $20 적립 약 {item['cash']/20:.0f}거래일분.\n"
            + (f"USD 계좌 큰 보유: {existing}.\n" if existing else "")
            + "**공식 근거가 있는 조사 후보. 자동 매수 승인은 하지 않으며 주문도 보내지 않음.**")


def run(send: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    state = load_json(STATE, {"seen": {}})
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(days=14)).isoformat()
    cooldown_cutoff = (now_dt - timedelta(days=7)).isoformat()
    seen = {url: ts for url, ts in (state.get("seen") or {}).items() if ts >= cutoff}
    alerted = {ticker: ts for ticker, ts in (state.get("alerted_tickers") or {}).items()
               if ts >= cooldown_cutoff}
    # The user wants a useful lead, not a fresh ping for every news item.
    # Existing state files predate last_sent_at, so recover it from sent tickers.
    last_sent = state.get("last_sent_at") or max((state.get("alerted_tickers") or {}).values(),
                                                 default=None)
    if send and last_sent and datetime.fromisoformat(last_sent) > now_dt - timedelta(hours=24):
        print("최근 24시간 내 알림 발송 완료 — 다음 예약 실행에서 새 후보 검토")
        return 0
    articles = fetch_articles()
    items = candidates(articles, set(seen), held_symbols())
    items = [{**item, "tickers": [t for t in item["tickers"] if t not in alerted]}
             for item in items]
    items = [item for item in items if item["tickers"]]
    verified = verified_items(items)
    selectable = [{key: value for key, value in item.items() if key != "evidence"}
                  for item in verified]
    try:
        choice = choose_one(selectable)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        choice = None
    if choice is None and verified:
        # SEC corroboration already passed; a local model outage must not hide
        # a research lead. The downstream review still defaults to hold.
        choice = {**selectable[0], "ticker": selectable[0]["tickers"][0],
                  "why": "SEC 공시로 사건을 확인한 조사 후보",
                  "unknown": "수익성과 가격 대비 기대수익은 추가 확인 필요"}
    if choice:
        original = next(item for item in verified if item["url"] == choice["url"])
        choice = review_candidate({**original, "ticker": choice["ticker"]})
    if choice:
        print(format_alert(choice))
    else:
        print("공식 공시까지 확인된 새 투자 조사 후보 없음")
    if not send:
        print("(미리보기: 전송·상태 기록 없음)")
        return 0
    if choice and not send_discord(format_alert(choice), env_var="DISCORD_NEWS_WEBHOOK_URL",
                                   username="투자 아이디어 감시"):
        raise RuntimeError("디스코드 전송 실패 — 상태를 기록하지 않음")
    now = datetime.now(timezone.utc).isoformat()
    reviews = list(state.get("reviews") or [])[-49:]
    if choice:
        seen[choice["url"]] = now
        alerted[choice["ticker"]] = now
        reviews.append({"sent_at": now, "ticker": choice["ticker"],
                        "article": choice["url"], "filing": choice["evidence"]["url"],
                        "verdict": choice["analysis"]["verdict"],
                        "reason": choice["analysis"]["reason"],
                        "price": choice["price"]["lastPrice"], "cash": choice["cash"]})
    STATE.write_text(json.dumps({"seen": seen, "alerted_tickers": alerted,
                                 "last_sent_at": now if choice else last_sent,
                                 "reviews": reviews},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스 기반 미국 주식 아이디어 한 건 선별")
    parser.add_argument("--send", action="store_true", help="디스코드 알림과 중복 방지 기록")
    args = parser.parse_args()
    try:
        return run(send=args.send)
    except Exception as e:
        detail = "뉴스 API 사용량 제한" if "사용량 제한" in str(e) else type(e).__name__
        print(f"아이디어 감시 실패: {detail}")
        if args.send:
            report_failure("뉴스 후보", detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
