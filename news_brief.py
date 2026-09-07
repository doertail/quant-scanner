"""
news_brief.py — 보유·관심종목 뉴스 헤드라인 + 한국어 한 문장 요약 → 일일 리포트 섹션
(요약은 로컬 Ollama. 외부 API 키 없음, 비용 0, 기사 본문이 밖으로 나가지 않는다.)

**중요도 = 당일 등락률의 절대값.** 많이 움직인 종목의 뉴스를 위에 놓는다.
"1건씩 골고루"는 뭐가 중요한지 알려주지 않는다 — 조용한 종목의 정기 리포트와
4% 빠진 종목의 기사가 같은 무게로 보이면 안 된다. 자리가 모자라면 **덜 움직인 종목부터** 잘린다.

소스는 시장별로 다르다:
  · 미국 종목 → yfinance 뉴스 (영어)
  · 한국 종목(`.KS`/`.KQ`) → 네이버 금융 종목뉴스 (한글, 국내 커버리지가 압도적)
    yfinance는 국내 종목 뉴스가 부실하다 — 메리츠금융지주는 2건뿐이고 그나마 무관한 기사였다.

관심종목은 토스 API로 못 가져온다(2026-09-04 확인: REST/웹소켓 스펙 전문에 `관심`/`watch` 0건).
그래서 `watchlist.json`(`{"티커": "표시이름"}`)으로 직접 관리한다. 보유종목은 자동 포함.
한국 종목은 yfinance 규칙대로 `.KS` 접미사가 필요하다 — `105560.KS`(KB금융).

사용:
    python news_brief.py              # 콘솔에 섹션 미리보기
    python news_brief.py --hours 24   # 최근 24시간만
    python news_brief.py --no-llm     # 요약 없이 원문 발췌 (Ollama 안 씀)

**커버리지**: 관심종목 전부가 설명되어야 한다 — 안 보이는 종목은 마지막 `📋` 줄이
`자리부족`인지 `뉴스없음`인지 밝힌다. 조용한 건지 잘린 건지 모르면 리포트를 믿을 수 없다.

**동반 이동**: 3종목 이상이 같은 방향으로 2.5% 넘게 움직이면 개별 뉴스 대신 한 줄로 묶고
코스피 대비를 같이 보여준다. 섹터 이벤트는 개별 종목 피드로는 절대 안 잡히기 때문이다
(2026-09-04: 코스피 +1.6%인데 4대 금융지주가 -3~-4%. 각 종목 피드엔 골프대회 후원 기사뿐이었다).
"""

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")   # ALPHAVANTAGE_API_KEY — 단독 실행에도 필요
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
PORTFOLIO_FILE = BASE_DIR / "portfolio.json"

# 디스코드 2000자 제한 — 리포트 본문과 한 메시지를 공유하므로 넉넉히 남긴다.
MAX_ITEMS = 8
MAX_PER_TICKER = 2
TITLE_MAX = 90
# 요약. 원문 발췌로 시작했다가 LLM으로 올렸다 — 발췌는 요약이 아니었다.
# 사진 캡션("(오른쪽부터) 진옥동 회장…"), 부제 덩어리, "[서울=뉴시스]" 같은 게 그대로 딸려오고
# 문장 중간에서 잘렸다. 로컬 Ollama로 진짜 한 문장 요약을 만든다(무료, 키 없음, 외부 전송 없음).
SUMMARY_MAX = 110   # 모델이 70자 지시를 넘길 때가 있어 여유. 문장이 잘리면 요약이 아니게 된다
WITH_SUMMARY = True      # 끄면 헤드라인만 (한 통에 더 많이 들어간다)

USE_LLM_SUMMARY = True   # 끄면 원문 발췌로 폴백
OLLAMA_URL = "http://localhost:11434/api/generate"
# exaone3.5 = LG 한국어 특화. 실측(2026-09-04) 국내 기사 6.3s / qwen2.5:14b는 20.2s에 품질은 비슷.
OLLAMA_MODEL = "exaone3.5:latest"
OLLAMA_TIMEOUT = 40      # 한 건. launchd 잡이 멈추면 안 되니 짧게.
SUMMARY_WORKERS = 4
_SUMMARY_PROMPT = (
    "다음 기사를 한국어 한 문장으로 요약해라. "
    "사진 캡션·기자명·부제·언론사 표기는 무시하고 핵심 사실만. "
    "70자 이내. 설명 없이 요약문만 출력.\n\n기사:\n"
)
# 월요일 실행은 주말을 건너뛰므로 금요일 미국장 뉴스를 놓치지 않게 72시간.
DEFAULT_HOURS = 72

KR_SUFFIXES = (".KS", ".KQ")
KOSPI = "^KS11"          # 동반 이동이 시장 전체인지 섹터만인지 가르는 기준선
MOVE_THRESHOLD = 2.5     # 이 정도 움직여야 "크게"
MIN_CLUSTER = 3          # 3종목 이상 같은 방향이면 섹터 이벤트로 본다
SECTOR_CANDIDATES = 4    # 설명 기사 후보 상한 — LLM 판정 호출을 묶어둔다
# 홍보 기사 — 주가와 무관. 회사명이 들어있어도 버린다.
_PR_RE = re.compile(r"후원|챔피언십|골프|KLPGA|KPGA|시상|수상|기부|사회공헌|봉사|장학|캠페인|출항|위촉|임명식")
# 동반 이동을 설명할 만한 섹터 언급
_SECTOR_RE = re.compile(r"금융주|은행주|증권주|보험주|금융지주|반도체주|섹터|업종|순매도|순매수|약세|강세|로테이션")
NAVER_NEWS_URL = "https://finance.naver.com/item/news_news.naver"
NAVER_BASE = "https://finance.naver.com"
# finance.naver.com의 news_read는 JS로 여기로 튕긴다. 처음부터 이 주소를 쓴다(본문도 여기 있다).
NAVER_ARTICLE = "https://n.news.naver.com/mnews/article/{office}/{article}"
_UA = {"User-Agent": "Mozilla/5.0", "Referer": NAVER_BASE + "/"}
KST = timezone(timedelta(hours=9))


# ─── 대상 종목 ──────────────────────────────────────────────────────────────

def load_watchlist() -> dict:
    """watchlist.json → {티커: 표시이름}. 없거나 깨졌으면 빈 dict.

    티커만 있는 배열(`["NVDA"]`)도 받는다 — 그 경우 표시이름은 티커 그대로.
    """
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    if isinstance(data, list):
        data = {t: None for t in data}          # 표시이름 없음 → 정규화된 티커로 폴백
    if not isinstance(data, dict):
        return {}
    out = {}
    for t, label in data.items():
        t = str(t).strip().upper()
        if t:
            out[t] = (str(label).strip() if label is not None else "") or t
    return out


def load_holdings() -> list:
    try:
        pf = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return list((pf.get("holdings") or {}).keys())


def target_tickers() -> list:
    """보유 + 관심. 순서 유지, 중복 제거."""
    seen, out = set(), []
    for t in load_holdings() + list(load_watchlist()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def display_labels() -> dict:
    """티커 → 리포트에 찍을 이름. 보유 미국주는 티커 그대로가 제일 읽기 좋다."""
    return load_watchlist()


def is_kr(ticker: str) -> bool:
    return ticker.upper().endswith(KR_SUFFIXES)


# ─── 중요도: 당일 등락률 ────────────────────────────────────────────────────

def fetch_changes(tickers) -> dict:
    """{티커: 등락률%}. 조회 실패한 종목은 빠진다(0으로 채우지 않는다 — 0%는 '안 움직였다'는 뜻이라 거짓말이 된다).

    ⚠️ 전체 마지막 두 **행**을 쓰면 안 된다. 한국장과 미국장은 세션이 어긋나서,
    한국 오후에 돌리면 미국 종목의 오늘 행이 NaN이고 ffill하면 등락률이 0%로 찍힌다.
    종목별로 유효한 마지막 두 종가를 각자 쓴다.
    """
    tickers = list(tickers)
    if not tickers:
        return {}
    # 동반 이동이 "섹터만"인지 "시장 전체"인지 가르려면 지수가 필요하다
    if any(is_kr(t) for t in tickers) and KOSPI not in tickers:
        tickers = tickers + [KOSPI]
    import warnings

    import yfinance as yf

    out = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(tickers, period="7d", progress=False, auto_adjust=False)["Close"]
    except Exception:
        return {}

    for t in tickers:
        try:
            s = (df[t] if len(tickers) > 1 else df).dropna()
            if len(s) >= 2 and s.iloc[-2]:
                out[t] = (s.iloc[-1] / s.iloc[-2] - 1) * 100
        except Exception:
            continue
    return out


# ─── 뉴스 소스 ──────────────────────────────────────────────────────────────

def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def fetch_yf_news(ticker: str, cutoff: datetime, limit: int) -> list:
    """미국 종목 — yfinance 뉴스."""
    import yfinance as yf

    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []

    out = []
    for it in raw:
        if len(out) >= limit:
            break
        c = it.get("content") or {}
        dt = _parse_dt(c.get("pubDate"))
        title = (c.get("title") or "").strip()
        url = ((c.get("clickThroughUrl") or c.get("canonicalUrl") or {}) or {}).get("url")
        if not (dt and title and url) or dt < cutoff:
            continue
        out.append({
            "ticker": ticker,
            "title": title,
            "publisher": ((c.get("provider") or {}).get("displayName") or "").strip(),
            "url": url,
            "dt": dt,
            # yfinance summary는 영어 원문 그대로 — 발췌 폴백이자 LLM 입력이 된다
            "summary": (c.get("summary") or c.get("description") or "").strip(),
            "raw": (c.get("summary") or c.get("description") or "").strip(),
        })
    return out


_NAVER_ROW = re.compile(
    r'<td class="title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<td class="info">(.*?)</td>\s*<td class="date">\s*(.*?)</td>',
    re.S,
)
_NAVER_IDS = re.compile(r"article_id=(\d+).*?office_id=(\d+)")
_NAVER_BODY = re.compile(r'<article[^>]*id="dic_area"[^>]*>(.*?)</article>', re.S)


def naver_body(url: str, timeout: int = 8) -> str:
    """기사 본문 전체(태그 제거). 실패하면 빈 문자열 — 리포트를 막지 않는다."""
    import requests

    try:
        r = requests.get(url, headers=_UA, timeout=timeout)
        r.raise_for_status()
    except Exception:
        return ""
    m = _NAVER_BODY.search(r.text)
    return re.sub(r"\s+", " ", _clean(m.group(1))).strip() if m else ""


def _lead(body: str) -> str:
    """본문에서 리드 문단만. LLM 요약 실패 시 폴백으로 쓴다."""
    if not body:
        return ""
    parts = re.split(r"(?<=다\.)\s+", body)
    lead = parts[0] if parts else body
    if len(lead) < 40 and len(parts) > 1:
        lead = lead + " " + parts[1]
    return lead.strip()


def naver_lead(url: str, timeout: int = 8) -> str:
    return _lead(naver_body(url, timeout))


# ─── LLM 요약 (로컬 Ollama) ─────────────────────────────────────────────────

def _strip_preamble(text: str) -> str:
    """모델이 가끔 붙이는 껍데기 제거 — 따옴표, `요약:`, <think> 블록."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    # 따옴표를 먼저 벗겨야 `"요약: ..."` 형태에서 접두어가 잡힌다 (순서 바꾸면 안 됨)
    text = text.strip().strip('"“”\'').strip()
    text = re.sub(r"^\s*(요약|Summary)\s*[:：]\s*", "", text)
    return text.strip().strip('"“”\'').strip()


def llm_summarize(text: str, model: str = "") -> str:
    """한 문장 한국어 요약. 실패·부재 시 빈 문자열(호출부가 발췌로 폴백)."""
    text = (text or "").strip()
    if not text:
        return ""
    import requests

    try:
        r = requests.post(OLLAMA_URL, timeout=OLLAMA_TIMEOUT, json={
            "model": model or OLLAMA_MODEL,
            "prompt": _SUMMARY_PROMPT + text[:2000],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 120},
        })
        r.raise_for_status()
        out = _strip_preamble(r.json().get("response", ""))
    except Exception:
        return ""
    # 한 문장만. 모델이 두세 문장 뱉으면 첫 문장으로 자른다.
    parts = re.split(r"(?<=다\.)\s+", out)
    return (parts[0] if parts else out).strip()


def _load_body(item: dict) -> None:
    """네이버 기사 본문을 뒤늦게 채운다. 실패해도 조용히 넘어간다."""
    body = naver_body(item["url"])
    item["raw"] = body
    item["summary"] = _lead(body)
    item["needs_body"] = False


def summarize_items(items: list) -> list:
    """items의 `summary`를 LLM 요약으로 교체(가능한 것만). 원본 리스트를 그대로 돌려준다.

    Ollama가 안 떠 있거나 느리면 조용히 기존 발췌를 유지한다 — 요약 때문에 리포트를 놓치면 안 된다.
    """
    if not (USE_LLM_SUMMARY and items):
        return items
    from concurrent.futures import ThreadPoolExecutor

    # 렌더될 기사만 본문을 받는다 (fetch_news는 목록만 훑고 넘어간다)
    with ThreadPoolExecutor(max_workers=SUMMARY_WORKERS) as ex:
        list(ex.map(_load_body, [it for it in items if it.get("needs_body")]))

    targets = [it for it in items if (it.get("raw") or it.get("summary"))]
    if not targets:
        return items
    with ThreadPoolExecutor(max_workers=SUMMARY_WORKERS) as ex:
        results = list(ex.map(lambda it: llm_summarize(it.get("raw") or it["summary"]), targets))
    for it, s in zip(targets, results):
        if s:
            it["summary"] = s
    return items


def _clean(html: str) -> str:
    """태그·엔티티 제거. 네이버 제목엔 &middot; &hellip; &quot; 정도만 나온다."""
    import html as _h

    return _h.unescape(re.sub(r"<[^>]+>", "", html)).strip()


def match_token(label: str) -> str:
    """회사명에서 기사 제목 매칭용 토큰. `하나금융지주` → `하나금융`, `신한지주` → `신한`.

    네이버 종목뉴스는 시황 통신기사("코스피 상승 마감")를 개별 종목에도 태깅한다.
    회사 이름이 안 들어간 제목을 걸러내는 데 쓴다.
    """
    label = (label or "").strip()
    for suffix in ("금융지주", "지주", "홀딩스"):
        if label.endswith(suffix) and len(label) > len(suffix):
            return label[: -len(suffix)] if suffix != "금융지주" else label[:-2]
    return label


def is_pr(title: str) -> bool:
    """홍보성 기사 — 주가와 무관하다.

    금융지주의 '회사명 들어간 기사'는 대부분 이쪽이다. 실제로 하나금융지주가 -3.7% 빠진 날
    골프대회 후원 기사가 올라왔다(2026-09-04). 주가를 설명하지 않는 기사는 자리만 먹는다.
    """
    return bool(_PR_RE.search(title or ""))


def _naver_rows(code: str, cutoff: datetime):
    """(dt, title, press, href) 최신순. 네트워크 실패는 빈 결과."""
    import requests

    try:
        r = requests.get(NAVER_NEWS_URL, params={"code": code, "page": 1},
                         headers=_UA, timeout=10)
        r.raise_for_status()
    except Exception:
        return
    r.encoding = "euc-kr"
    for href, title, press, date_s in _NAVER_ROW.findall(r.text):
        try:
            dt = datetime.strptime(_clean(date_s), "%Y.%m.%d %H:%M").replace(tzinfo=KST)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        title = _clean(title)
        if title:
            yield dt, title, _clean(press), _clean(href)


def fetch_naver_news(ticker: str, cutoff: datetime, limit: int, label: str | None = None) -> list:
    """한국 종목 — 네이버 금융 종목뉴스. 종목코드는 `.KS` 떼고 6자리.

    label이 있으면 회사명이 제목에 없는 시황 기사는 버린다. 전부 걸러지면 빈 리스트 —
    "코스피 상승 마감"으로 자리를 채우느니 그 종목을 아예 빼는 게 낫다.
    홍보 기사(후원·시상·사회공헌)도 버린다.
    """
    token = match_token(label) if label else ""
    code = ticker.upper().rsplit(".", 1)[0]

    out = []
    for dt, title, press, href in _naver_rows(code, cutoff):
        if len(out) >= limit:
            break
        if token and token not in title:
            continue                            # 회사명 없는 시황 기사 — 버린다
        if is_pr(title):
            continue                            # 후원·시상·사회공헌 — 주가와 무관

        ids = _NAVER_IDS.search(href)
        url = (NAVER_ARTICLE.format(article=ids.group(1), office=ids.group(2)) if ids
               else (NAVER_BASE + _clean(href) if href.startswith("/") else _clean(href)))
        out.append({
            "ticker": ticker,
            "title": title,
            "publisher": _clean(press),
            "url": url,
            "dt": dt,
            # 본문은 여기서 안 받는다 — 렌더될 기사만 summarize_items가 받아온다
            "summary": "", "raw": "", "needs_body": bool(ids),
        })
    return out


def _naver_texts(url: str, fetch: bool) -> dict:
    """본문 1회만 받아서 LLM 입력(raw)과 폴백 발췌(summary)를 같이 만든다."""
    if not fetch:
        return {"summary": "", "raw": ""}
    body = naver_body(url)
    return {"summary": _lead(body), "raw": body}


# ─── 동반 이동 (섹터 이벤트) ────────────────────────────────────────────────

def detect_comovement(changes: dict, threshold: float = MOVE_THRESHOLD,
                      min_count: int = MIN_CLUSTER) -> list:
    """같은 방향으로 크게 움직인 종목 묶음(티커 리스트). 없으면 빈 리스트.

    개별 종목 뉴스로는 섹터 이벤트를 설명할 수 없다 — 2026-09-04에 코스피가 +1.6% 오르는 동안
    4대 금융지주가 다 -3~-4% 빠졌는데, 각 종목 피드엔 그 이유가 없고 시황 기사에만 있었다.
    """
    for sign in (-1, 1):
        group = [t for t, v in changes.items()
                 if t != KOSPI and (v * sign) >= threshold]
        if len(group) >= min_count:
            return sorted(group, key=lambda t: abs(changes[t]), reverse=True)
    return []


def sector_article_candidates(tickers, cutoff: datetime, labels: dict | None = None,
                              limit: int = SECTOR_CANDIDATES) -> list:
    """동반 이동을 설명할 만한 시황 기사 후보들. 종목 피드에서 **버렸던** 쪽을 다시 본다.

    섹터 키워드 밀도 + 최신순으로 정렬만 한다 — 실제로 설명하는지는 `explains_move`가 판정한다.
    """
    labels = labels or {}
    seen, cands = set(), []
    for t in tickers:
        if not is_kr(t):
            continue
        token = match_token(labels.get(t, ""))
        for dt, title, press, href in _naver_rows(t.upper().rsplit(".", 1)[0], cutoff):
            if (token and token in title) or title in seen:
                continue                        # 개별 종목 기사는 여기선 제외
            hits = len(_SECTOR_RE.findall(title))
            if not hits:
                continue
            seen.add(title)
            ids = _NAVER_IDS.search(href)
            url = (NAVER_ARTICLE.format(article=ids.group(1), office=ids.group(2))
                   if ids else NAVER_BASE + href)
            cands.append({"hits": hits, "dt": dt, "title": title,
                          "publisher": press, "url": url})
    # 한 종목 피드만 보고 멈추면 안 된다 — 2026-09-04에 실제 설명 기사("반도체 투심 살아나며
    # 금융주 약세")는 KB금융 피드에만 있었고, 우리금융 피드에서 멈추면 영영 못 본다.
    cands.sort(key=lambda c: (c["hits"], c["dt"]), reverse=True)
    return cands[:limit]


def explains_move(article_title: str, body: str, names, direction: str) -> bool:
    """이 기사가 그 종목들의 그 방향 움직임을 실제로 설명하나? 판정은 로컬 Ollama.

    키워드 밀도만으로 고르면 방향이 반대인 기사가 뽑힌다 — 2026-09-04에 우리 4종목은 하락인데
    "은행주는 상승"이라는 기사가 1순위였다. **애매하면 아니오**(잘못된 설명이 없느니만 못하다).
    """
    if not USE_LLM_SUMMARY:
        return True                             # LLM 껐으면 예전처럼 키워드 1순위 사용
    prompt = (
        f"종목: {', '.join(names)}\n"
        f"오늘 이 종목들은 모두 {direction}했다.\n\n"
        f"기사 제목: {article_title}\n"
        f"기사 내용: {(body or '')[:1200]}\n\n"
        f"이 기사가 위 종목들이 오늘 {direction}한 이유를 설명하는가? "
        f"기사가 반대 방향을 말하거나 다른 업종 얘기면 아니오. "
        f"확실하지 않으면 아니오. '예' 또는 '아니오' 한 단어만 출력."
    )
    import requests

    try:
        r = requests.post(OLLAMA_URL, timeout=OLLAMA_TIMEOUT, json={
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.0, "num_predict": 10},
        })
        r.raise_for_status()
        ans = _strip_preamble(r.json().get("response", ""))
    except Exception:
        return False                            # 판정 불가 = 안 씀
    return ans.startswith("예") and not ans.startswith("예외")


def find_sector_article(tickers, cutoff: datetime, labels: dict | None = None,
                        names=None, direction: str = "하락"):
    """후보 중 **실제로 설명하는** 첫 기사. 없으면 None."""
    for c in sector_article_candidates(tickers, cutoff, labels):
        texts = _naver_texts(c["url"], fetch=WITH_SUMMARY)
        if not explains_move(c["title"], texts.get("raw", ""), names or tickers, direction):
            continue
        return {"title": c["title"], "publisher": c["publisher"],
                "url": c["url"], "dt": c["dt"], **texts}
    return None


def comovement_lines(changes: dict, labels: dict, cutoff: datetime) -> tuple:
    """(줄 목록, 이미 설명된 티커 집합). 동반 이동이 없으면 ([], set())."""
    group = detect_comovement(changes)
    if not group:
        return [], set()

    names = [labels.get(t, t) for t in group]
    lo, hi = min(changes[t] for t in group), max(changes[t] for t in group)
    span = f"{lo:+.1f}%" if abs(hi - lo) < 0.1 else f"{lo:+.1f} ~ {hi:+.1f}%"
    idx = changes.get(KOSPI)
    idx_s = f" — 코스피는 {idx:+.1f}%" if idx is not None else ""

    direction = "하락" if lo < 0 else "상승"
    head = f"🏦 **동반 {direction}**: {' · '.join(names)} ({span}){idx_s}"
    lines = [head]

    art = find_sector_article(group, cutoff, labels, names=names, direction=direction)
    if not art:
        # 억지로 아무 시황 기사나 붙이지 않는다. 틀린 설명은 없느니만 못하다.
        lines.append("   ↳ 이유를 설명하는 기사는 못 찾음")
    if art:
        summary = (llm_summarize(art.get("raw") or "") if USE_LLM_SUMMARY else "") or art.get("summary", "")
        if len(summary) > SUMMARY_MAX:
            summary = summary[:SUMMARY_MAX - 1].rstrip() + "…"
        src = f" — {art['publisher']}" if art["publisher"] else ""
        lines.append(f"   ↳ [{art['title'][:TITLE_MAX]}](<{art['url']}>){src}")
        if summary:
            lines.append(f"   ↳ {summary}")
    return lines, set(group)


def fetch_news(tickers, hours: int = DEFAULT_HOURS, changes: dict | None = None,
               labels: dict | None = None, coverage: dict | None = None) -> list:
    """중요도(|등락률|) 큰 종목부터 뉴스를 모은다. 한 종목의 실패가 전체를 막지 않는다.

    coverage dict를 주면 {티커: 찾은 건수}를 채운다 — "뉴스가 없어서 안 나온 것"과
    "자리가 없어서 잘린 것"을 구분해 보고하기 위함. 그래서 **전 종목을 다 훑는다**
    (조기 종료하면 뒤쪽 종목이 0건인지 안 본 건지 알 수 없다).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    changes, labels = changes or {}, labels or {}
    # 등락률을 못 구한 종목은 뒤로 (-1). 0.0과 구분해야 한다.
    ordered = sorted(tickers, key=lambda t: abs(changes.get(t, float("nan"))) if t in changes else -1.0,
                     reverse=True)

    items, seen_urls = [], set()
    for t in ordered:
        if is_kr(t):
            got = fetch_naver_news(t, cutoff, MAX_PER_TICKER, label=labels.get(t))
        else:
            got = fetch_yf_news(t, cutoff, MAX_PER_TICKER)
        fresh = [it for it in got if it["url"] not in seen_urls]
        seen_urls.update(it["url"] for it in fresh)
        items.extend(fresh)
        if coverage is not None:
            coverage[t] = len(fresh)
    return items[:MAX_ITEMS]


def coverage_line(tickers, coverage: dict, shown: set, covered: set,
                  labels: dict | None = None) -> str:
    """보이지 않는 종목이 **왜** 안 보이는지 한 줄로. 보이는 건 이미 위에 있으니 반복하지 않는다.

    이게 없으면 "뉴스가 없어서 안 나온 것"과 "자리가 없어 잘린 것"이 구분이 안 된다.
    관심종목 전부가 이 줄 하나로 설명되어야 한다.
    """
    labels = labels or {}
    no_news, cut = [], []
    for t in tickers:
        if t in shown or t in covered:
            continue
        (cut if coverage.get(t, 0) > 0 else no_news).append(labels.get(t, t))

    parts = []
    if cut:
        parts.append(f"자리부족 {'·'.join(cut)}")
    if no_news:
        parts.append(f"뉴스없음 {'·'.join(no_news)}")
    return f"📋 {' | '.join(parts)}" if parts else ""


# ─── 시장 큰 사건 (내 종목 밖) ──────────────────────────────────────────────
# 보유·관심 10종목만 보면 연준·금리·관세처럼 "남들 다 아는데 나만 모르는 것"을 영영 못 본다.
# Alpha Vantage NEWS_SENTIMENT는 기사마다 토픽 관련도를 숫자로 준다 — 단어 리스트를 손으로
# 늘리는 대신 점수 합으로 정렬한다.

AV_URL = "https://www.alphavantage.co/query"
# ⚠️ `topics=a,b`는 OR가 아니라 AND다 (실측 2026-09-07: 4개 동시 요청 → items 0).
# 토픽당 따로 부르고 여기서 합친다. 하루 3콜, 무료 한도 25콜.
AV_TOPICS = ("economy_monetary", "economy_fiscal", "financial_markets")
MACRO_ITEMS = 3
# 13F 지분공시 자동생성 기사 도배 — 95건 중 25건이 이 소스였다(2026-09-07).
# 정규식이 아니라 소스 한 줄이라 사고마다 늘어나지 않는다.
AV_SKIP_SOURCES = {"MarketBeat"}


def _av_score(article: dict) -> float:
    """우리가 요청한 토픽들의 관련도 합. 여러 토픽에 걸친 기사가 위로 온다."""
    return sum(float(t.get("relevance_score") or 0)
               for t in (article.get("topics") or []) if t.get("topic") in AV_TOPICS)


def fetch_macro(hours: int = DEFAULT_HOURS, limit: int = MACRO_ITEMS) -> list:
    """시장 전체 뉴스 상위 N건. 키가 없거나 실패하면 빈 리스트 — 리포트를 막지 않는다.

    `sort=RELEVANCE`는 쓰지 않는다. 질의 관련도순이라 열흘 전 기사가 1순위로 올라온다
    (실측 2026-09-07). 최신 창은 time_from으로 자르고 순위는 토픽 점수로 매긴다.
    """
    import os

    import requests

    key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    if not key:
        return []
    t_from = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y%m%dT%H%M")

    feed = {}
    for topic in AV_TOPICS:
        try:
            r = requests.get(AV_URL, timeout=15, params={
                "function": "NEWS_SENTIMENT", "topics": topic, "sort": "LATEST",
                "time_from": t_from, "limit": 50, "apikey": key,
            })
            r.raise_for_status()
            got = r.json().get("feed") or []      # 한도 초과 시 {"Information": ...} → 빈 리스트
        except Exception:
            continue                              # 한 토픽이 죽어도 나머지는 쓴다
        for a in got:
            feed.setdefault(a.get("url"), a)

    out, seen_heads = [], set()
    for a in sorted(feed.values(), key=_av_score, reverse=True):
        title = (a.get("title") or "").strip()
        head = title[:40]
        if not (title and a.get("url")) or a.get("source") in AV_SKIP_SOURCES or head in seen_heads:
            continue                              # 같은 기사가 제목만 바뀌어 여러 건 온다
        seen_heads.add(head)
        body = (a.get("summary") or "").strip()
        out.append({"title": title, "url": a["url"], "publisher": a.get("source") or "",
                    "summary": body, "raw": body})
        if len(out) >= limit:
            break
    return out


def macro_lines(hours: int = DEFAULT_HOURS) -> list:
    """시장 섹션 줄들. 기사가 없으면 빈 리스트(헤더도 안 붙인다)."""
    items = summarize_items(fetch_macro(hours))
    if not items:
        return []
    lines = ["🌍 **시장** (내 종목 밖)"]
    for it in items:
        title = it["title"]
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX - 1].rstrip() + "…"
        src = f" — {it['publisher']}" if it["publisher"] else ""
        lines.append(f"• [{title}](<{it['url']}>){src}")
        summary = (it.get("summary") or "").strip()
        if WITH_SUMMARY and summary:
            if len(summary) > SUMMARY_MAX:
                summary = summary[:SUMMARY_MAX - 1].rstrip() + "…"
            lines.append(f"   ↳ {summary}")
    return lines


# ─── 렌더 ───────────────────────────────────────────────────────────────────

def section_lines(items: list, budget: int | None = None, labels: dict | None = None,
                  changes: dict | None = None, shown_out: set | None = None) -> list:
    """디스코드 메시지 줄들. megadip.section_lines와 같은 계약.

    ⚠️ 이 섹션은 코드블록(```) **밖**에 붙여야 링크가 눌린다.
    budget: 이 섹션이 쓸 수 있는 최대 글자수. 넘치면 뒤에서 잘라내고 몇 건 생략했는지 표시한다.
            (yahoo URL 하나가 100자를 먹어서 8건이면 1200자를 넘긴다 — 반드시 필요한 가드)
    """
    header = "📰 **관심 뉴스** (많이 움직인 순)"
    if not items:
        return ["📰 **관심 뉴스** — 최근 새 소식 없음"]

    labels, changes = labels or {}, changes or {}
    rendered = []
    for it in items:
        title = it["title"]
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX - 1].rstrip() + "…"
        src = f" — {it['publisher']}" if it["publisher"] else ""
        name = labels.get(it["ticker"], it["ticker"])
        chg = changes.get(it["ticker"])
        tag = f"{name} {chg:+.1f}%" if chg is not None else name
        # <> 로 임베드 억제 (헤드라인 여러 개면 프리뷰 카드가 채널을 잡아먹는다)
        line = f"• `{tag}` [{title}](<{it['url']}>){src}"
        summary = (it.get("summary") or "").strip()
        if WITH_SUMMARY and summary:
            if len(summary) > SUMMARY_MAX:
                summary = summary[:SUMMARY_MAX - 1].rstrip() + "…"
            line += f"\n   ↳ {summary}"
        rendered.append((line, it["ticker"]))

    if budget is None:
        if shown_out is not None:
            shown_out.update(tk for _, tk in rendered)
        return [header] + [ln for ln, _ in rendered]

    L, used = [header], len(header)
    shown = set()
    for i, (line, tk) in enumerate(rendered):
        remaining = len(rendered) - i
        # 마지막 한 줄은 "…외 N건" 꼬리표 자리를 남겨둔다
        tail = len(f"• …외 {remaining}건") + 1 if remaining > 1 else 0
        if used + len(line) + 1 + tail > budget:
            L.append(f"• …외 {remaining}건")
            break
        L.append(line)
        shown.add(tk)
        used += len(line) + 1

    if shown_out is not None:
        shown_out.update(shown)
    return L if len(L) > 1 else [f"{header} — 자리 부족"]


def build_section(hours: int = DEFAULT_HOURS, budget: int | None = None) -> list:
    # 시장 큰 사건이 맨 위 — 내 종목 뉴스는 그 아래. 실패하면 조용히 빠진다.
    try:
        macro = macro_lines(hours)
    except Exception:
        macro = []

    tickers = target_tickers()
    labels = display_labels()
    changes = fetch_changes(tickers)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    co_lines, covered = comovement_lines(changes, labels, cutoff)

    # 동반 이동으로 이미 설명된 종목은 개별 뉴스 자리를 먹지 않는다 — 다른 종목에 자리를 준다
    rest = [t for t in tickers if t not in covered]
    coverage = {}
    items = summarize_items(fetch_news(rest, hours=hours, changes=changes,
                                       labels=labels, coverage=coverage))

    head = (macro + [""] if macro else []) + (co_lines + [""] if co_lines else [])
    used = len("\n".join(head)) + 1 if head else 0
    # 📋 커버리지 줄은 예산 밖에서 뒤에 붙는다 — 자리를 미리 빼두지 않으면 총량이 넘친다.
    # 아무것도 안 보인 최악의 경우로 잡는다(실제 줄은 항상 이보다 짧다).
    used += len(coverage_line(tickers, coverage, set(), covered, labels)) + 1
    shown = set()
    news = section_lines(items, budget=(budget - used) if budget else None,
                         labels=labels, changes=changes, shown_out=shown)

    cov = coverage_line(tickers, coverage, shown, covered, labels)
    if cov:
        news = news + [cov]
    return head + news


def main():
    ap = argparse.ArgumentParser(description="보유·관심종목 뉴스 헤드라인")
    ap.add_argument("--hours", type=int, default=DEFAULT_HOURS, help=f"조회 시간 범위 (기본 {DEFAULT_HOURS})")
    ap.add_argument("--no-llm", action="store_true", help="LLM 요약 없이 원문 발췌 (Ollama 안 씀)")
    args = ap.parse_args()

    if args.no_llm:
        globals()["USE_LLM_SUMMARY"] = False

    tickers = target_tickers()
    changes = fetch_changes(tickers)
    print(f"(대상 {len(tickers)}종목: {', '.join(tickers) or '없음'})")
    if changes:
        ranked = sorted(((t, v) for t, v in changes.items() if t != KOSPI),
                        key=lambda kv: abs(kv[1]), reverse=True)
        print("(중요도순: " + ", ".join(f"{t} {v:+.1f}%" for t, v in ranked) + ")")
    labels = display_labels()
    print("\n".join(build_section(args.hours)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
