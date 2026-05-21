"""
news_filter.py — 신호 후보 필터링 (어닝스 캘린더 + 뉴스 감성)

두 가지 필터:
  1. 어닝스 캘린더 (filter_candidates_by_earnings)
     yfinance Ticker.calendar로 발표 ±N일 이내 종목 차단.
     어닝스 갭 리스크로 RSI 신호가 거짓이 되기 쉬운 구간.

  2. 뉴스 감성 (filter_candidates_by_sentiment)
     yfinance 뉴스 + Gemini로 단기 악재 감지.
     PASS   : 중립/긍정 또는 판단 불가 (기본값, 트레이딩 차단 안 함)
     REDUCE : 실적 하향·소송·규제 리스크 등 단기 불확실성 → 목록 유지 + 🔶 표시
     SKIP   : 파산·SEC조사·대규모 리콜 등 명확한 악재 → 목록에서 제거
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import yfinance as yf

_log = logging.getLogger(__name__)

_SENTIMENT_PASS: dict = {"verdict": "PASS", "score": 0.5, "summary": "분석 불가 (기본값)"}
_EARNINGS_UNKNOWN: dict = {"is_near": False, "date": None, "days_until": None}


# ─── 어닝스 캘린더 필터 ──────────────────────────────────────────────────────

def is_near_earnings(ticker: str, days: int = 2) -> dict:
    """
    어닝스 발표 ±days일 이내인지 체크.

    반환: {
      'is_near': bool,           # 차단 대상 여부
      'date': str | None,        # 가장 가까운 어닝스 날짜 ('YYYY-MM-DD')
      'days_until': int | None,  # 오늘 기준 D±N (음수=과거, 양수=미래)
    }
    조회 실패 시 is_near=False 폴백 (안전 — 의심스러우면 통과).
    """
    try:
        calendar = yf.Ticker(ticker).calendar or {}
        earnings_dates = calendar.get('Earnings Date') or []
        if not earnings_dates:
            return _EARNINGS_UNKNOWN.copy()

        today = date.today()
        closest = None
        closest_abs = None
        for ed in earnings_dates:
            if not isinstance(ed, date):
                continue
            delta = (ed - today).days
            if closest_abs is None or abs(delta) < closest_abs:
                closest = ed
                closest_abs = abs(delta)

        if closest is None:
            return _EARNINGS_UNKNOWN.copy()

        delta = (closest - today).days
        return {
            'is_near': abs(delta) <= days,
            'date': closest.isoformat(),
            'days_until': delta,
        }
    except Exception:
        return _EARNINGS_UNKNOWN.copy()


def filter_candidates_by_earnings(
    candidates: list[dict],
    days: int = 2,
    logger=None,
    max_workers: int = 5,
) -> tuple[list, list]:
    """
    어닝스 ±days일 이내 후보를 차단.

    - 각 후보 dict에 'earnings' 키 추가 (in-place)
    - is_near=True 종목은 반환 목록에서 제거
    - yfinance calendar 호출은 병렬화 (max_workers)

    반환: (필터링된_목록, 차단된_목록)
    """
    if not candidates:
        return candidates, []

    lg = logger or _log
    tickers = [c['ticker'] for c in candidates]
    results: dict[str, dict] = {}

    try:
        workers = min(max_workers, max(1, len(tickers)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {ex.submit(is_near_earnings, t, days): t for t in tickers}
            for fut, t in future_map.items():
                try:
                    results[t] = fut.result(timeout=10)
                except Exception:
                    results[t] = _EARNINGS_UNKNOWN.copy()
    except Exception as e:
        lg.warning(f"어닝스 필터 병렬 처리 오류 — 전체 통과 처리: {e}")
        for c in candidates:
            c['earnings'] = _EARNINGS_UNKNOWN.copy()
        return candidates, []

    filtered: list[dict] = []
    blocked: list[dict] = []
    for c in candidates:
        info = results.get(c['ticker'], _EARNINGS_UNKNOWN.copy())
        c['earnings'] = info
        if info['is_near']:
            lg.info(
                f"  어닝스 차단 {c['ticker']}: {info['date']} (D{info['days_until']:+d})"
            )
            blocked.append(c)
        else:
            filtered.append(c)
    return filtered, blocked


# ─── 뉴스 감성 필터 ──────────────────────────────────────────────────────────


def _parse_gemini_json(text: str) -> dict:
    """Gemini 응답 텍스트에서 JSON 파싱 + 유효성 검사."""
    try:
        clean = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()
        data = json.loads(clean)
        verdict = data.get('verdict', 'PASS')
        if verdict not in ('PASS', 'REDUCE', 'SKIP'):
            verdict = 'PASS'
        score = max(0.0, min(1.0, float(data.get('score', 0.5))))
        summary = str(data.get('summary', ''))[:200]
        return {'verdict': verdict, 'score': score, 'summary': summary}
    except Exception:
        return _SENTIMENT_PASS.copy()


def get_ticker_news(ticker: str, max_items: int = 8) -> list[dict]:
    """yfinance로 최근 72시간 내 뉴스 헤드라인 반환. 실패 시 빈 리스트."""
    try:
        cutoff_ts = datetime.now(timezone.utc).timestamp() - 72 * 3600
        news: list = yf.Ticker(ticker).news or []
        recent = [
            item for item in news
            if item.get('providerPublishTime', 0) > cutoff_ts
        ]
        # 72시간 내 뉴스가 없으면 최신 순으로 폴백
        return (recent if recent else news)[:max_items]
    except Exception:
        return []


def analyze_news_sentiment(
    ticker: str,
    news_items: list[dict],
    gemini_client,
    model_name: str,
) -> dict:
    """
    Gemini로 뉴스 감성 분석.
    반환: {"verdict": "PASS"|"REDUCE"|"SKIP", "score": float, "summary": str}
    모든 오류는 _SENTIMENT_PASS 폴백.
    """
    if not news_items:
        return _SENTIMENT_PASS.copy()

    headlines = []
    for item in news_items:
        # yfinance news 구조: .title (구버전) 또는 .content.title (신버전)
        title = item.get('title') or (item.get('content') or {}).get('title', '')
        if title:
            headlines.append(f"- {title}")

    if not headlines:
        return _SENTIMENT_PASS.copy()

    prompt = f"""Analyze these recent news headlines for {ticker} stock.

Headlines:
{chr(10).join(headlines)}

Respond with ONLY a valid JSON object (no markdown, no explanation):
{{
  "verdict": "PASS",
  "score": 0.5,
  "summary": "한 문장 요약 (한국어)"
}}

verdict rules:
- PASS  : neutral or positive news, or ambiguous/insufficient information (use as default)
- REDUCE: earnings miss, lawsuit filed, regulatory investigation start, product recall (minor), guidance cut
- SKIP  : bankruptcy filing, SEC fraud charges, major accounting scandal, criminal indictment, catastrophic product failure

score: 0.0 (very negative) to 1.0 (very positive), 0.5 = neutral
"""

    def _call() -> str:
        resp = gemini_client.models.generate_content(model=model_name, contents=prompt)
        return resp.text

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call)
            text = future.result(timeout=15)
        return _parse_gemini_json(text)
    except Exception as e:
        _log.debug(f"{ticker} 감성 분석 오류: {e}")
        return _SENTIMENT_PASS.copy()


def filter_candidates_by_sentiment(
    candidates: list[dict],
    gemini_client,
    model_name: str,
    top_n: int = 5,
    logger=None,
) -> tuple[list, list]:
    """
    상위 top_n 후보에 대해 뉴스 감성 분석 수행.

    - 각 후보 dict에 'sentiment' 키 추가 (in-place)
    - SKIP 판정 종목은 반환 목록에서 제거
    - 나머지(PASS/REDUCE)는 유지
    - top_n 초과분은 분석 없이 PASS 처리

    반환: (필터링된_목록, SKIP된_목록)
    """
    if not candidates:
        return candidates, []

    lg = logger or _log
    deadline_ts = datetime.now().timestamp() + 28.0

    to_analyze = candidates[:top_n]
    rest = candidates[top_n:]

    for c in rest:
        c['sentiment'] = _SENTIMENT_PASS.copy()

    filtered: list[dict] = []
    skipped: list[dict] = []

    for c in to_analyze:
        ticker = c['ticker']

        if datetime.now().timestamp() > deadline_ts:
            lg.warning(f"뉴스 필터 28s 데드라인 초과 — {ticker} 이후 PASS 처리")
            c['sentiment'] = _SENTIMENT_PASS.copy()
            filtered.append(c)
            continue

        news_items = get_ticker_news(ticker)
        sentiment = analyze_news_sentiment(ticker, news_items, gemini_client, model_name)
        c['sentiment'] = sentiment

        verdict = sentiment.get('verdict', 'PASS')
        score = sentiment.get('score', 0.5)
        summary = sentiment.get('summary', '')
        lg.info(f"  감성 {ticker}: {verdict} ({score:.2f}) — {summary}")

        if verdict == 'SKIP':
            skipped.append(c)
        else:
            filtered.append(c)

    return filtered + rest, skipped
