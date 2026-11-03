"""Discover US company research leads from broad web news, without a news API key.

The Google News RSS feed supplies immutable article links; a local model only
selects from those links. This is research, never an order or a buy signal.
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from company_fundamentals import display_fundamentals, fundamentals
from monitor_health import report_failure
from notify import send_discord
from official_evidence import find_8k_evidence, recent_filings, ticker_map
from opportunity_alert import held_symbols, load_json
from personal_portfolio_report import live_snapshot
from toss_client import TossClient


ROOT = Path(__file__).resolve().parent
STATE = ROOT / "web_opportunity_state.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "exaone3.5:latest"
LOOKBACK_HOURS = 72
QUERIES = (
    '("cybersecurity" OR "AI security") (adoption OR customer OR demand OR contract) when:3d',
    '("AI infrastructure" OR "data center") (customer OR contract OR demand) when:3d',
    '("semiconductor" OR "power grid") (orders OR customer OR demand) when:3d',
    '("enterprise software" OR "medical device" OR "automation") (adoption OR contract OR demand) when:3d',
)
NOISE = re.compile(r"stocks? to buy|price target|shares? (rise|fall|surge|drop)|"
                   r"class action|shareholder alert|analyst rating|investor alert|"
                   r"market (forecast|size)|market to 20\d\d|CAGR|undervalued|"
                   r"stock price forecast|investment narrative", re.I)
SKIP_SOURCES = {"IndexBox", "Kalkine Media", "Simply Wall St", "The Motley Fool",
                "TradingKey", "tokenpost.com"}
GENERIC_NAMES = {"strategy", "power", "energy", "growth", "demand", "security",
                 "automation", "technology", "digital", "market", "stock", "report",
                 "industrial", "innovation", "construction", "medical", "awareness",
                 "georgia", "fresh", "global", "national", "united", "advanced",
                 "data", "sustainable", "india", "field", "force", "building",
                 "operational", "semiconductor"}
THEMES = ("cybersecurity", "data center", "semiconductor", "automation",
          "medical device", "power grid", "enterprise software")


def fetch_news(hours: int = LOOKBACK_HOURS) -> list[dict]:
    """Search sectors, not a fixed ticker watchlist. Preserve source URLs."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen = set()
    out = []
    failures = 0
    for query in QUERIES:
        url = "https://news.google.com/rss/search?" + urlencode(
            {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            failures += 1
            continue
        topic_rows = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            publisher = (item.findtext("source") or "").strip()
            try:
                published = parsedate_to_datetime(item.findtext("pubDate") or "")
            except (TypeError, ValueError):
                continue
            if (not title or not link or link in seen or published < cutoff
                    or NOISE.search(title) or publisher in SKIP_SOURCES):
                continue
            seen.add(link)
            topic_rows.append({"title": title, "url": link, "publisher": publisher,
                               "published": published.astimezone(timezone.utc).isoformat()})
        out.extend(sorted(topic_rows, key=lambda row: row["published"], reverse=True)[:8])
    if failures == len(QUERIES):
        raise RuntimeError("모든 웹 뉴스 검색 실패")
    # Limit model context while retaining representation from each search.
    return sorted(out, key=lambda row: row["published"], reverse=True)


def related_company_article(company: str, theme: str) -> dict | None:
    """Require an independent company-specific story before sharing a sector inference."""
    first = next((word for word in re.findall(r"[A-Za-z]{5,}", company)
                  if word.lower() not in GENERIC_NAMES), "")
    if not first or theme not in THEMES:
        return None
    query = f'"{first}" "{theme}" when:30d'
    url = "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    for item in ET.fromstring(response.content).findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if (link and published >= cutoff and re.search(rf"\b{re.escape(first)}\b", title, re.I)
                and theme.lower() in title.lower() and not NOISE.search(title)):
            return {"title": title, "url": link,
                    "published": published.astimezone(timezone.utc).isoformat()}
    return None


def infer_industry_lead(articles: list[dict], held: set[str], seen: set[str],
                        diagnostics: dict | None = None) -> dict | None:
    """Connect a demand story to a company only with a second, relevant source."""
    trends = [a for a in articles if a["url"] not in seen and
              any(theme in a["title"].lower() for theme in THEMES)][:20]
    if diagnostics is not None:
        diagnostics["industry_articles"] = len(trends)
    if not trends:
        return None
    prompt = (
        "다음 산업 뉴스 제목에서 몇 년 지속될 수요 변화 하나와 그 수혜를 얻을 수 있는 "
        "미국 상장회사 하나를 조사 가설로 제안하라. 회사가 뉴스에 나오지 않았으면 "
        "수혜는 추론이라고 명시하라. 기사 제목에 없는 숫자와 계약을 만들지 마라. "
        "적합한 조합이 없으면 {}를 반환하라. "
        "JSON만: {\"index\":0,\"ticker\":\"ABC\",\"theme\":\"cybersecurity\","
        "\"thesis\":\"한국어 가설\",\"unknown\":\"확인할 매출 근거\"}. "
        f"theme은 {', '.join(THEMES)} 중 하나. 제외 종목: {', '.join(sorted(held))}.\n"
        + json.dumps([{"index": i, "title": a["title"]} for i, a in enumerate(trends)],
                     ensure_ascii=False)
    )
    response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt,
                             "stream": False, "format": "json",
                             "options": {"temperature": 0}}, timeout=120)
    response.raise_for_status()
    row = json.loads(response.json()["response"])
    if not isinstance(row, dict):
        return None
    if diagnostics is not None:
        diagnostics["industry_model_proposed"] = bool(row)
    index = row.get("index")
    ticker = str(row.get("ticker") or "").upper()
    theme = str(row.get("theme") or "").lower()
    known = ticker_map()
    if (not isinstance(index, int) or not 0 <= index < len(trends) or ticker not in known
            or ticker in held or theme not in THEMES or theme not in trends[index]["title"].lower()):
        if diagnostics is not None:
            diagnostics["industry_rejection"] = "제안의 기사·종목·주제 연결 확인 실패"
        return None
    thesis = str(row.get("thesis") or "").strip()[:150]
    unknown = str(row.get("unknown") or "").strip()[:150]
    if not thesis or not unknown:
        return None
    company = known[ticker][1]
    try:
        related = related_company_article(company, theme)
    except (requests.RequestException, ET.ParseError):
        return None
    if not related or related["url"] == trends[index]["url"]:
        if diagnostics is not None:
            diagnostics["industry_rejection"] = "별도 회사 관련 기사 미확인"
        return None
    return {**trends[index], "ticker": ticker, "company": company,
            "thesis": thesis, "unknown": unknown, "related_article": related,
            "inferred": True}


def select_leads(articles: list[dict], held: set[str], seen: set[str],
                 diagnostics: dict | None = None) -> list[dict]:
    available = [item for item in articles if item["url"] not in seen]
    if diagnostics is not None:
        diagnostics["unseen_articles"] = len(available)
    if not available:
        return []
    known = ticker_map()
    first_words = {}
    for ticker, (_, company) in known.items():
        words = re.findall(r"[A-Za-z]{4,}", company)
        if words:
            first_words.setdefault(words[0].lower(), set()).add(ticker)
    matched = []
    for article in available:
        title = article["title"]
        companies = []
        for ticker, (_, company) in known.items():
            if ticker in held:
                continue
            words = re.findall(r"[A-Za-z]{4,}", company)
            if not words:
                continue
            first = words[0].lower()
            if first in GENERIC_NAMES:
                continue
            # Generic single words such as Power or Strategy are not company evidence.
            name = (words[0].title() if len(first) >= 5 and len(first_words[first]) == 1
                    else " ".join(word.title() for word in words[:2]))
            if len(name) >= 5 and re.search(rf"\b{re.escape(name)}\b", title):
                companies.append((ticker, company, len(name)))
        if companies:
            ticker, company, _ = max(companies, key=lambda row: row[2])
            matched.append({**article, "ticker": ticker, "company": company})
    matched = matched[:20]
    if diagnostics is not None:
        diagnostics["company_articles"] = len(matched)
    if not matched:
        return []
    prompt = (
        "당신은 미국 주식 리서치 보조자다. SEC 상장사 목록과 기사 제목으로 회사가 확인된 "
        "최근 뉴스만 입력한다. 몇 년 지속될 수요 변화와 그 회사의 매출 연결 가능성이 "
        "가장 뚜렷한 조사 후보 하나를 고른다. 단순 주가 변화, 애널리스트 의견, 막연한 AI 홍보, "
        "이미 알려진 실적 반복은 제외한다. 좋은 후보가 없으면 빈 배열을 반환한다. "
        "제목에서 확인되지 않는 내용이나 숫자는 만들어내지 마라. "
        "unknown에는 매수 전에 무엇을 확인해야 하는지 써라. "
        "JSON만 반환: [{\"index\":0,\"thesis\":\"한국어 한 문장\","
        "\"unknown\":\"한국어 한 문장\"}]. index는 아래 기사 번호만 사용.\n"
        + json.dumps([{"index": i, "title": a["title"], "company": a["company"],
                       "ticker": a["ticker"], "published": a["published"]}
                      for i, a in enumerate(matched)],
                     ensure_ascii=False)
    )
    response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": prompt,
                             "stream": False, "format": "json",
                             "options": {"temperature": 0}}, timeout=150)
    response.raise_for_status()
    parsed = json.loads(response.json()["response"])
    if isinstance(parsed, dict):
        parsed = next((parsed[key] for key in ("candidates", "suggestions", "leads", "items")
                       if isinstance(parsed.get(key), list)), [])
    if not isinstance(parsed, list):
        return []
    if diagnostics is not None:
        diagnostics["company_model_proposed"] = len(parsed)
    out = []
    used = set()
    for row in parsed[:1]:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        if not isinstance(index, int) or not 0 <= index < len(matched):
            continue
        ticker = matched[index]["ticker"]
        if ticker in used:
            continue
        thesis = str(row.get("thesis") or "").strip()[:150]
        unknown = str(row.get("unknown") or "").strip()[:150]
        if not thesis or not unknown:
            continue
        used.add(ticker)
        out.append({**matched[index], "thesis": thesis, "unknown": unknown})
    return out


def review(lead: dict) -> dict:
    """Independent official context and price. Absence of an 8-K is not rejection."""
    ticker = lead["ticker"]
    evidence = None
    try:
        evidence = find_8k_evidence(ticker, lead["title"])
    except (requests.RequestException, ValueError, KeyError):
        pass
    filing = None
    if not evidence:
        try:
            filings = recent_filings(ticker, forms=("8-K", "10-Q", "10-K"), days=180, limit=1)
            filing = filings[0] if filings else None
        except (requests.RequestException, ValueError, KeyError):
            pass
    price = cash = financials = None
    try:
        client = TossClient()
        quotes = client.get_prices([ticker]).get("result") or []
        price = next((row for row in quotes if row.get("symbol") == ticker
                      and row.get("lastPrice")), None)
        if price:
            _, cash = live_snapshot(client)
            financials = fundamentals(ticker, float(price["lastPrice"]))
    except (requests.RequestException, ValueError, KeyError, RuntimeError):
        pass
    return {**lead, "evidence": evidence, "filing": filing, "price": price,
            "cash": cash, "financials": financials}


def format_alert(item: dict) -> str:
    source = (f"[SEC 8-K 사건 대조](<{item['evidence']['url']}>)" if item["evidence"] else
              f"[SEC {item['filing']['form']} 기업자료](<{item['filing']['url']}>)"
              " (기사 사건과의 일치는 미확인)" if item["filing"] else
              "사건을 뒷받침할 공식 자료 미확인")
    price = item["price"]
    price_line = (f"토스 ${price['lastPrice']} ({price.get('timestamp') or '시각 미상'})"
                  if price else "토스 현재가 확인 실패")
    if item["cash"] is not None:
        price_line += f" · USD 예수금 ${item['cash']:,.0f}"
    facts = (f"\nSEC 재무: [원자료](<{item['financials']['source']}>) "
             f"{display_fundamentals(item['financials'])}" if item["financials"] else "")
    related = item.get("related_article")
    related_line = (f"회사 관련 기사: [{related['title']}](<{related['url']}>) "
                    "(산업 기사와 회사 수혜의 연결은 추론)\n" if related else "")
    return (f"🔎 **웹 뉴스 조사 후보 · {item['ticker']} · 매수 판단 전**\n"
            f"기사: [{item['title']}](<{item['url']}>) ({item['published'][:10]})\n"
            f"{related_line}"
            f"수요·수혜 가설(기사 제목 기반): {item['thesis']}\n"
            f"공식 근거: {source}\n"
            f"확인할 점: {item['unknown']}\n"
            f"{price_line}{facts}\n"
            "**현재 가격 대비 기대수익·기존 보유와의 중복을 검토하기 전에는 매수 신호가 아님. 주문 없음.**")


def run(send: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    state = load_json(STATE, load_json(ROOT / "opportunity_alert_state.json",
                                      {"seen": {}, "alerted_tickers": {}}))
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=14)).isoformat()
    seen = {url: stamp for url, stamp in (state.get("seen") or {}).items() if stamp >= recent}
    alerted = {ticker: stamp for ticker, stamp in (state.get("alerted_tickers") or {}).items()
               if stamp >= (now - timedelta(days=7)).isoformat()}
    articles = fetch_news()
    diagnostics = {"fetched_articles": len(articles)}
    excluded = held_symbols() | set(alerted)
    leads = select_leads(articles, excluded, set(seen), diagnostics)
    if not leads:
        inferred = infer_industry_lead(articles, excluded, set(seen), diagnostics)
        leads = [inferred] if inferred else []
    if not leads:
        print("이번 실행에서 디스코드 알림 후보 0건 — 투자 기회가 없거나 매수 기준에서 탈락했다는 뜻은 아님")
        print("탐색 단계: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
        return 0
    # One lead at a time; a daily brief should not force three weak picks.
    item = review(leads[0])
    message = format_alert(item)
    print(message)
    if not send:
        print("(미리보기: 전송·상태 기록 없음)")
        return 0
    if not send_discord(message, env_var="DISCORD_NEWS_WEBHOOK_URL", username="투자 아이디어 감시"):
        raise RuntimeError("디스코드 전송 실패")
    stamp = datetime.now(timezone.utc).isoformat()
    seen[item["url"]] = stamp
    alerted[item["ticker"]] = stamp
    STATE.write_text(json.dumps({"seen": seen, "alerted_tickers": alerted},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="웹 뉴스에서 새 미국 회사 조사 후보 발굴")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    try:
        return run(send=args.send)
    except Exception as exc:
        print(f"웹 뉴스 감시 실패: {type(exc).__name__}")
        if args.send:
            report_failure("웹 뉴스 후보", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
