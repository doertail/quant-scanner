"""Watch official filings for the user's conviction holdings, with no orders.

First run records a baseline. Later new 8-K/10-Q/10-K filings produce one
Discord review alert per filing; a filing itself is never a sell signal.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from company_fundamentals import display_fundamentals, fundamentals
from monitor_health import report_failure
from notify import send_discord
from official_evidence import exhibit_991, get_text, recent_filings
from toss_client import TossClient, TossAPIError

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "thesis_monitor_state.json"
THESES = {
    "CRWD": "보안 수요가 고객·ARR·현금흐름으로 이어지는지, 제품 사고와 매출 인식 문제로 신뢰가 훼손되는지",
    "TSLA": "장기 사업 경쟁력과 수익성이 유지되는지",
}
CRWD_TERMS = ("annual recurring revenue", "net new arr", "customer retention",
              "revenue recognition", "restatement", "department of justice",
              "securities and exchange commission", "outage", "cybersecurity incident")
TSLA_TERMS = ("automotive gross margin", "vehicle deliveries", "energy storage",
              "operating cash flow", "autonomous")
CRWD_BASELINE = {
    "arr_b": 5.84, "arr_growth_pct": 25.0, "net_new_arr_m": 332.8,
    "next_q_arr_guidance_low_m": 6184.4,
    "filed": "2026-08-26",
    "source": "https://www.sec.gov/Archives/edgar/data/1535527/000153552726000029/crwd-20260826xex991.htm",
}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def classify_filing(text: str) -> str:
    """Only explicit 8-K event headings receive urgent priority."""
    if re.search(r"item\s+4\.02\s+non.reliance", text[:10000], re.IGNORECASE):
        return "재무제표 신뢰 문제 긴급 확인"
    if re.search(r"item\s+1\.05\s+material cybersecurity incidents?", text[:10000], re.IGNORECASE):
        return "중대한 사이버 사고 긴급 확인"
    if (re.search(r"item\s+8\.01\s+other events?", text[:10000], re.IGNORECASE)
            and re.search(r"outage|system crashes|service disruption", text[:10000],
                          re.IGNORECASE)):
        return "공시된 서비스 장애 긴급 확인"
    return "정기 보유 근거 검토"


def filing_excerpt(filing: dict, text: str) -> str:
    lower = text.lower()
    terms = CRWD_TERMS if filing["ticker"] == "CRWD" else TSLA_TERMS
    snippets = []
    for term in terms:
        pos = lower.find(term)
        if pos >= 0:
            snippets.append(text[max(0, pos - 110):pos + 300])
    return " ... ".join(snippets[:2])[:700]


def financial_flags(data: dict) -> list[str]:
    """Objective deterioration prompts review, never an automatic sale."""
    flags = []
    growth = (data.get("revenue") or {}).get("ytd_growth")
    if growth is not None and growth < 0:
        flags.append(f"누적 매출 전년비 {growth:.0%}")
    operating = data.get("operating_cash")
    if operating and operating["value"] < 0:
        flags.append("직전 12개월 영업현금흐름 적자")
    return flags


def parse_crwd_metrics(text: str) -> dict:
    """Read only the clearly labelled ARR values in an issuer earnings exhibit."""
    result = {}
    arr = re.search(r"Annual Recurring Revenue\s*\(ARR\)\s+grew\s+([\d.]+)%"
                    r"\s+year.over.year\s+to\s+\$([\d.]+)\s+billion", text,
                    re.IGNORECASE)
    if arr:
        result["arr_growth_pct"] = float(arr.group(1))
        result["arr_b"] = float(arr.group(2))
    net_new = re.search(r"\$([\d.]+)\s+million\s+was\s+net new ARR", text,
                        re.IGNORECASE)
    if net_new:
        result["net_new_arr_m"] = float(net_new.group(1))
    outlook = text.find("Financial Outlook")
    if outlook >= 0:
        guidance = re.search(r"Annual recurring revenue\s+\$([\d,.]+)\s*-\s*\$([\d,.]+)"
                             r"\s+million", text[outlook:outlook + 3500], re.IGNORECASE)
        if guidance:
            result["next_q_arr_guidance_low_m"] = float(guidance.group(1).replace(",", ""))
    return result


def crwd_comparison(current: dict, previous: dict) -> tuple[str, list[str]]:
    if not current:
        return "", []
    parts, flags = [], []
    for key, label, unit in (("arr_b", "ARR", "B"),
                             ("arr_growth_pct", "ARR 전년비", "%"),
                             ("net_new_arr_m", "신규 ARR", "M")):
        if key in current:
            value = current[key]
            old = previous.get(key)
            delta = f" (직전 {old:g}{unit})" if old is not None else ""
            parts.append(f"{label} {value:g}{unit}{delta}")
    if (current.get("arr_growth_pct") is not None and previous.get("arr_growth_pct") is not None
            and current["arr_growth_pct"] < previous["arr_growth_pct"]):
        flags.append("ARR 성장률 둔화")
    if (current.get("net_new_arr_m") is not None and previous.get("net_new_arr_m") is not None
            and current["net_new_arr_m"] < previous["net_new_arr_m"]):
        flags.append("신규 ARR 직전 분기보다 감소")
    if (current.get("arr_b") is not None and
            previous.get("next_q_arr_guidance_low_m") is not None and
            current["arr_b"] * 1000 < previous["next_q_arr_guidance_low_m"]):
        flags.append("ARR가 직전 회사 전망 하단 미달")
    return "; ".join(parts), flags


def format_alert(filing: dict, excerpt: str, priority: str, financial_line: str = "",
                 flags: list[str] | None = None, kpi_line: str = "",
                 exhibit_url: str = "") -> str:
    ticker = filing["ticker"]
    icon = "🚨" if "긴급" in priority else "📄"
    flags = flags or []
    action = ("매도 여부 긴급 검토" if "긴급" in priority else
              "실적 악화 근거 점검 후 매도 여부 판단" if flags else
              "보유 근거 확인, 즉시 매도 근거는 아직 미확인")
    return (f"{icon} **보유 근거 점검 · {ticker} 새 {filing['form']}**\n"
            f"공시일 {filing['filed']} · [SEC 원문](<{filing['url']}>)\n"
            f"우선순위: {priority}\n"
            f"현재 판단: {action} (자동 주문 없음)\n"
            f"현재 확인할 논지: {THESES[ticker]}\n"
            f"관련 공시 문구: {excerpt or '지정된 핵심 문구 없음. 공시 전체 확인 필요.'}\n"
            + (f"핵심 수치: {kpi_line}\n" if kpi_line else "")
            + (f"[실적 발표문 원문](<{exhibit_url}>)\n" if exhibit_url else "")
            + (f"SEC 수치: {financial_line}\n" if financial_line else "")
            + (f"악화 단서: {', '.join(flags)}\n" if flags else "")
            + "**발췌문만으로 이전 공시와 달라졌다고 판단하지 않음. 매도 판정이나 주문이 아님.**")


def current_financial_review(ticker: str) -> tuple[str, list[str]]:
    try:
        rows = TossClient().get_prices([ticker]).get("result") or []
        price = next(float(row["lastPrice"]) for row in rows if row.get("symbol") == ticker)
        data = fundamentals(ticker, price)
        return display_fundamentals(data), financial_flags(data)
    except (TossAPIError, requests.RequestException, StopIteration, ValueError, KeyError):
        return "", []


def run(send: bool = False) -> int:
    load_dotenv(ROOT / ".env")
    state = load_state()
    current = []
    for ticker in THESES:
        current.extend(recent_filings(ticker, forms=("8-K", "10-Q", "10-K"),
                                      days=365, limit=100))
    ids = {filing["url"] for filing in current}
    if not state.get("initialized"):
        print(f"보유 공시 감시 기준선 설정: {len(ids)}건")
        if send:
            STATE.write_text(json.dumps({"initialized": datetime.now(timezone.utc).isoformat(),
                                         "seen": sorted(ids)}, indent=2), encoding="utf-8")
        return 0
    seen = set(state.get("seen") or [])
    baseline_day = state["initialized"][:10]
    new = [filing for filing in current if filing["url"] not in seen and
           filing["filed"] >= baseline_day]
    for filing in sorted(new, key=lambda row: row["filed"]):
        text = get_text(filing["url"])
        priority = classify_filing(text) if filing["form"] == "8-K" else "정기 보유 근거 검토"
        exhibit = exhibit_991(filing) if filing["form"] == "8-K" and filing.get("base") else None
        full_text = text + " " + (exhibit["text"] if exhibit else "")
        excerpt = filing_excerpt(filing, full_text)
        financial_line, flags = (current_financial_review(filing["ticker"])
                                 if filing["form"] in ("10-Q", "10-K") else ("", []))
        metrics = (parse_crwd_metrics(exhibit["text"]) if filing["ticker"] == "CRWD" and exhibit
                   else {})
        kpi_line, kpi_flags = crwd_comparison(
            metrics, (state.get("kpi") or {}).get("CRWD") or CRWD_BASELINE)
        flags += kpi_flags
        message = format_alert(filing, excerpt, priority, financial_line, flags, kpi_line,
                               exhibit["url"] if exhibit else "")
        print(message)
        if send:
            if not send_discord(message, env_var="DISCORD_NEWS_WEBHOOK_URL",
                                username="보유 근거 감시"):
                raise RuntimeError(f"{filing['ticker']} 디스코드 전송 실패")
            seen.add(filing["url"])
            if metrics:
                state["kpi"] = {**(state.get("kpi") or {}),
                                filing["ticker"]: {**metrics, "filed": filing["filed"],
                                                   "source": exhibit["url"]}}
            STATE.write_text(json.dumps({**state, "seen": sorted(seen)}, indent=2),
                             encoding="utf-8")
    if not new:
        print("새 보유 공시 없음")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="보유 회사의 신규 SEC 공시 감시")
    parser.add_argument("--send", action="store_true", help="새 공시를 디스코드로 전송")
    args = parser.parse_args()
    try:
        return run(send=args.send)
    except Exception as exc:
        print(f"보유 공시 감시 실패: {type(exc).__name__}")
        if args.send:
            report_failure("보유 공시", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
