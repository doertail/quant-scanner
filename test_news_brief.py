from datetime import datetime, timedelta, timezone

import news_brief as nb


def _item(ticker, n, url_len=100):
    """길이를 통제한 가짜 뉴스 1건 (실제 yahoo URL이 대략 100자)."""
    return {
        "ticker": ticker,
        "title": f"headline number {n} " + "x" * 40,
        "publisher": "Test Wire",
        # 티커를 URL에 넣어야 종목이 달라도 중복으로 안 걸린다
        "url": f"https://example.com/{ticker}/" + "u" * (url_len - 20) + str(n),
        "dt": datetime.now(timezone.utc) - timedelta(hours=n),
    }


ITEMS = [_item("TSLA", i) for i in range(8)]


def test_no_items_says_so():
    assert nb.section_lines([]) == ["📰 **관심 뉴스** — 최근 새 소식 없음"]


def test_unbudgeted_renders_every_item():
    lines = nb.section_lines(ITEMS)
    assert len(lines) == len(ITEMS) + 1          # 헤더 + 전체
    assert all("…외" not in l for l in lines)


def test_long_title_is_truncated():
    long_one = dict(ITEMS[0], title="T" * (nb.TITLE_MAX + 50))
    body = nb.section_lines([long_one])[1]
    assert "…" in body
    assert "T" * (nb.TITLE_MAX + 10) not in body


def test_budget_is_respected_and_reports_remainder():
    budget = 600
    lines = nb.section_lines(ITEMS, budget=budget)
    assert len("\n".join(lines)) <= budget
    assert lines[-1].startswith("• …외 ")       # 잘린 건수 표시
    assert len(lines) < len(ITEMS) + 1           # 실제로 잘렸다


def test_generous_budget_keeps_all_items():
    lines = nb.section_lines(ITEMS, budget=100_000)
    assert len(lines) == len(ITEMS) + 1
    assert all("…외" not in l for l in lines)


def test_embeds_are_suppressed():
    """링크는 <> 로 감싸야 프리뷰 카드가 채널을 잡아먹지 않는다."""
    body = nb.section_lines(ITEMS[:1])[1]
    assert "](<https://" in body and ">)" in body


def test_target_tickers_dedupes_holdings_and_watchlist(monkeypatch):
    monkeypatch.setattr(nb, "load_holdings", lambda: ["TSLA", "HSY"])
    monkeypatch.setattr(nb, "load_watchlist", lambda: {"HSY": "HSY", "NVDA": "NVDA"})
    assert nb.target_tickers() == ["TSLA", "HSY", "NVDA"]


def test_watchlist_missing_file_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(nb, "WATCHLIST_FILE", tmp_path / "nope.json")
    assert nb.load_watchlist() == {}


def test_watchlist_normalizes_case_and_blanks(monkeypatch, tmp_path):
    p = tmp_path / "watchlist.json"
    p.write_text('["nvda", " amd ", ""]', encoding="utf-8")
    monkeypatch.setattr(nb, "WATCHLIST_FILE", p)
    assert nb.load_watchlist() == {"NVDA": "NVDA", "AMD": "AMD"}


def test_watchlist_object_form_keeps_labels(monkeypatch, tmp_path):
    p = tmp_path / "watchlist.json"
    p.write_text('{"105560.KS": "KB금융", "nvda": ""}', encoding="utf-8")
    monkeypatch.setattr(nb, "WATCHLIST_FILE", p)
    # 빈 표시이름은 티커로 폴백
    assert nb.load_watchlist() == {"105560.KS": "KB금융", "NVDA": "NVDA"}


def test_korean_label_is_shown_instead_of_ticker():
    it = dict(_item("105560.KS", 0), ticker="105560.KS")
    body = nb.section_lines([it], labels={"105560.KS": "KB금융"})[1]
    assert "`KB금융`" in body
    assert "105560.KS" not in body.split("](")[0]   # 표시 태그엔 코드가 안 보여야 (URL엔 있어도 됨)


def test_missing_label_falls_back_to_ticker():
    body = nb.section_lines(ITEMS[:1], labels={})[1]
    assert "`TSLA`" in body


# ─── 중요도(등락률) ──────────────────────────────────────────────────────────

def test_change_percent_is_shown_next_to_name():
    body = nb.section_lines(ITEMS[:1], changes={"TSLA": -4.27})[1]
    assert "`TSLA -4.3%`" in body


def test_no_change_data_means_no_percent_tag():
    """등락률을 못 구했으면 0%로 꾸며내지 않는다 — 0%는 '안 움직였다'는 거짓말."""
    body = nb.section_lines(ITEMS[:1], changes={})[1]
    assert "%" not in body.split("](")[0]


def test_news_ordered_by_absolute_change(monkeypatch):
    """작게 오른 종목보다 크게 빠진 종목이 먼저 나와야 한다."""
    seen = []

    def fake(ticker, cutoff, limit):
        seen.append(ticker)
        return [_item(ticker, 0)]

    monkeypatch.setattr(nb, "fetch_us_news", fake)
    nb.fetch_news(["UP", "DOWN"], changes={"UP": +1.0, "DOWN": -9.0})
    assert seen[0] == "DOWN"


def test_unknown_change_sorts_last(monkeypatch):
    monkeypatch.setattr(nb, "fetch_us_news", lambda t, c, l: [_item(t, 0)])
    out = nb.fetch_news(["NOCHG", "TINY"], changes={"TINY": 0.1})
    assert [i["ticker"] for i in out] == ["TINY", "NOCHG"]


# ─── 시장별 소스 라우팅 ──────────────────────────────────────────────────────

def test_kr_tickers_are_detected():
    assert nb.is_kr("105560.KS") and nb.is_kr("035720.kq")
    assert not nb.is_kr("TSLA") and not nb.is_kr("QQQM")


def test_kr_ticker_routes_to_naver(monkeypatch):
    calls = {}

    def record(key):
        def f(ticker, cutoff, limit, **kw):
            calls[key] = ticker
            return []
        return f

    monkeypatch.setattr(nb, "fetch_naver_news", record("naver"))
    monkeypatch.setattr(nb, "fetch_us_news", record("yf"))
    nb.fetch_news(["105560.KS", "TSLA"], changes={"105560.KS": -5.0, "TSLA": 1.0})
    assert calls["naver"] == "105560.KS"
    assert calls["yf"] == "TSLA"


NAVER_JSON = [{"total": 2, "items": [
    {"datetime": "202609041542", "title": "KB금융, 3분기 실적 &middot; 배당 발표",
     "officeName": "연합뉴스", "officeId": "2", "articleId": "1"},
    {"datetime": "202001010900", "title": "아주 오래된 기사",
     "officeName": "옛날신문", "officeId": "2", "articleId": "9"},
]}]


def _naver_resp(payload):
    class R:
        def raise_for_status(self): pass
        def json(self): return payload
    return lambda *a, **k: R()


def test_summary_replaces_title_as_link_text_and_is_truncated():
    it = dict(ITEMS[0], summary="가" * (nb.SUMMARY_MAX + 40))
    body = nb.section_lines([it])[1]
    text = body.split("[", 1)[1].split("](")[0]
    assert ITEMS[0]["title"][:20] not in body          # 영어 제목 + 번역 요약 두 줄 금지
    assert text.endswith("…") and len(text) <= nb.SUMMARY_MAX
    assert "\n" not in body


def test_no_summary_falls_back_to_title():
    body = nb.section_lines([dict(ITEMS[0], summary="")])[1]
    assert ITEMS[0]["title"][:20] in body


def _lead_from(monkeypatch, body):
    class R:
        text = f'<article id="dic_area">{body}</article>'

        def raise_for_status(self): pass

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    return nb.naver_lead("https://x")


def test_naver_lead_takes_first_sentence(monkeypatch):
    first = "우리금융미래재단은 3일 서울 중구 천주교 서울대교구청에서 간담회를 열고 성과를 공유했다고 밝혔다."
    assert len(first) >= 40
    assert _lead_from(monkeypatch, first + " 두 번째 문장은 안 나와야 한다.") == first


def test_short_lead_pulls_in_next_sentence(monkeypatch):
    """한 줄짜리 리드는 그것만으론 뜻이 안 통해서 다음 문장까지 붙인다."""
    out = _lead_from(monkeypatch, "짧게 끝난다. 그래서 뒷문장이 붙는다.")
    assert out == "짧게 끝난다. 그래서 뒷문장이 붙는다."


def test_naver_lead_failure_is_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("requests.get", boom)
    assert nb.naver_lead("https://x") == ""      # 요약 실패가 리포트를 막지 않는다


# ─── LLM 요약 (Ollama, 전부 목킹 — 네트워크 안 씀) ──────────────────────────

class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_strip_preamble_removes_wrappers():
    assert nb._strip_preamble('  "요약: 신한은행이 협약을 맺었다."  ') == "신한은행이 협약을 맺었다."
    assert nb._strip_preamble("<think>고민중</think>결론이다.") == "결론이다."


def test_llm_summarize_returns_single_sentence(monkeypatch):
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: _Resp({"response": "첫 문장이다. 둘째 문장이다."}))
    assert nb.llm_summarize("본문") == "첫 문장이다."


def test_llm_summarize_empty_input_skips_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("빈 입력엔 호출하면 안 된다")

    monkeypatch.setattr("requests.post", boom)
    assert nb.llm_summarize("   ") == ""


def test_llm_failure_keeps_excerpt(monkeypatch):
    """Ollama가 꺼져 있어도 리포트는 나가야 한다 — 발췌로 폴백."""
    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("requests.post", down)
    items = [{"ticker": "TSLA", "summary": "원문 발췌", "raw": "본문"}]
    assert nb.summarize_items(items)[0]["summary"] == "원문 발췌"


def test_llm_success_replaces_excerpt(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"response": "진짜 요약이다."}))
    items = [{"ticker": "TSLA", "summary": "지저분한 발췌 [서울=뉴시스]", "raw": "본문"}]
    assert nb.summarize_items(items)[0]["summary"] == "진짜 요약이다."


def test_llm_disabled_is_a_noop(monkeypatch):
    monkeypatch.setattr(nb, "USE_LLM_SUMMARY", False)
    monkeypatch.setattr("requests.post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    items = [{"ticker": "TSLA", "summary": "발췌", "raw": "본문"}]
    assert nb.summarize_items(items)[0]["summary"] == "발췌"


# ─── 홍보 기사 필터 ─────────────────────────────────────────────────────────

def test_pr_articles_are_detected():
    """하나금융이 -3.7% 빠진 날 올라온 게 골프대회 후원 기사였다 (2026-09-04)."""
    assert nb.is_pr("하나금융, 17~20일 안산서 KLPGA 정규투어 챔피언십 개최")
    assert nb.is_pr("우리금융 '디노랩 테크' 첫 출항…AI 스타트업 4개사와 한배")
    assert nb.is_pr("신한은행, 취약계층에 장학금 기부")


def test_real_news_is_not_pr():
    assert not nb.is_pr("기준금리 3% 시대, 금융주 희비 교차")
    assert not nb.is_pr("신한은행 강원 주민에 복합 금융지원 제공")
    assert not nb.is_pr("KB금융 3분기 순이익 전년 대비 12% 증가")


# ─── 동반 이동 ──────────────────────────────────────────────────────────────

def test_comovement_detected_when_enough_move_together():
    ch = {"A": -4.2, "B": -3.7, "C": -3.3, "D": +0.1}
    assert nb.detect_comovement(ch) == ["A", "B", "C"]      # 크게 움직인 순


def test_comovement_needs_min_cluster():
    assert nb.detect_comovement({"A": -4.2, "B": -3.7, "C": -0.2}) == []


def test_comovement_ignores_mixed_directions():
    """방향이 섞이면 섹터 이벤트가 아니다."""
    assert nb.detect_comovement({"A": -4.0, "B": +4.0, "C": -0.1}) == []


def test_comovement_excludes_the_index_itself():
    ch = {nb.KOSPI: -5.0, "A": -4.0, "B": -3.0, "C": -2.6}
    assert nb.KOSPI not in nb.detect_comovement(ch)


def test_comovement_line_contrasts_with_index(monkeypatch):
    monkeypatch.setattr(nb, "find_sector_article", lambda *a, **k: None)
    ch = {"105560.KS": -3.3, "055550.KS": -3.7, "086790.KS": -4.2, nb.KOSPI: +1.6}
    labels = {"105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융지주"}
    lines, covered = nb.comovement_lines(ch, labels, datetime(2026, 9, 1, tzinfo=timezone.utc))
    head = lines[0]
    assert "동반 하락" in head
    assert "하나금융지주" in head and "KB금융" in head
    assert "코스피는 +1.6%" in head              # 섹터만인지 시장 전체인지 가르는 핵심
    assert covered == set(labels)


def test_explains_move_accepts_yes(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"response": "예"}))
    assert nb.explains_move("금융주 약세", "본문", ["KB금융"], "하락")


def test_explains_move_rejects_no(monkeypatch):
    """방향이 반대인 기사는 걸러야 한다 — 우리 종목은 하락인데 '은행주 상승' 기사가 1순위였다."""
    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp({"response": "아니오"}))
    assert not nb.explains_move("은행 뛰는데 증권은 뒷걸음질", "본문", ["KB금융"], "하락")


def test_explains_move_defaults_to_reject_when_llm_down(monkeypatch):
    """판정 불가면 안 쓴다. 틀린 설명은 없느니만 못하다."""
    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("requests.post", down)
    assert not nb.explains_move("아무 기사", "본문", ["KB금융"], "하락")


def test_unverified_comovement_says_so(monkeypatch):
    """설명 기사를 못 찾으면 아무 시황 기사나 붙이지 않고 못 찾았다고 말한다."""
    monkeypatch.setattr(nb, "find_sector_article", lambda *a, **k: None)
    ch = {"111111.KS": -4.0, "222222.KS": -3.5, "333333.KS": -3.0}
    lines, _ = nb.comovement_lines(ch, {}, datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert any("못 찾음" in l for l in lines)
    assert not any("](<" in l for l in lines)      # 링크를 억지로 안 붙인다


def test_sector_candidates_scan_every_ticker_in_the_group(monkeypatch):
    """한 종목 피드에서 멈추면 안 된다 — 진짜 설명 기사가 다른 종목 피드에만 있었다."""
    feeds = {
        "111111": [(datetime(2026, 9, 4, tzinfo=nb.KST), "코스피 업종 혼조", "A", "/x?article_id=1&office_id=9")],
        "222222": [(datetime(2026, 9, 4, tzinfo=nb.KST), "금융주 약세 금융지주 하락", "B", "/x?article_id=2&office_id=9")],
    }
    monkeypatch.setattr(nb, "_naver_rows", lambda code, cutoff: iter(feeds.get(code, [])))
    out = nb.sector_article_candidates(["111111.KS", "222222.KS"],
                                       datetime(2026, 9, 1, tzinfo=timezone.utc), {})
    titles = [c["title"] for c in out]
    assert "금융주 약세 금융지주 하락" in titles
    assert titles[0] == "금융주 약세 금융지주 하락"   # 섹터 키워드 많은 쪽이 1순위


def test_no_comovement_returns_nothing(monkeypatch):
    lines, covered = nb.comovement_lines({"A": +0.2}, {}, datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert lines == [] and covered == set()


# ─── 커버리지 (안 보이는 종목이 왜 안 보이나) ───────────────────────────────

def test_coverage_separates_no_news_from_no_room():
    """'뉴스가 없어서'와 '자리가 없어서'는 완전히 다른 얘기다."""
    line = nb.coverage_line(
        tickers=["A", "B", "C", "D"],
        coverage={"A": 2, "B": 2, "C": 0},      # D는 동반이동으로 이미 설명됨
        shown={"A"}, covered={"D"},
        labels={"C": "메리츠금융지주"},
    )
    assert "자리부족 B" in line
    assert "뉴스없음 메리츠금융지주" in line
    assert "A" not in line.replace("자리부족", "")   # 이미 보이는 건 반복 안 함
    assert "D" not in line                           # 동반이동으로 설명된 것도 반복 안 함


def test_coverage_empty_when_everything_shown():
    assert nb.coverage_line(["A"], {"A": 1}, shown={"A"}, covered=set()) == ""


def test_fetch_news_probes_every_ticker(monkeypatch):
    """조기 종료하면 뒤쪽 종목이 0건인지 안 본 건지 구분이 안 된다."""
    seen = []

    def fake(ticker, cutoff, limit, **kw):
        seen.append(ticker)
        return [_item(ticker, i) for i in range(2)]

    monkeypatch.setattr(nb, "fetch_us_news", fake)
    cov = {}
    tickers = [f"T{i}" for i in range(6)]        # 2건씩 = 12건, MAX_ITEMS(8) 초과
    nb.fetch_news(tickers, changes={t: -1.0 for t in tickers}, coverage=cov)
    assert set(seen) == set(tickers)             # 전부 조회함
    assert cov == {t: 2 for t in tickers}        # 건수도 전부 기록


def test_match_token_strips_holding_suffixes():
    assert nb.match_token("하나금융지주") == "하나금융"
    assert nb.match_token("신한지주") == "신한"
    assert nb.match_token("KB금융") == "KB금융"      # 뗄 접미사 없음
    assert nb.match_token("") == ""


def test_naver_drops_market_wire_without_company_name(monkeypatch):
    """'코스피 상승 마감'류가 개별 종목에 태깅돼 자리를 먹는 걸 막는다."""
    payload = [{"items": [
        {"datetime": "202609041542", "title": "코스피 상승 마감", "officeName": "뉴스1",
         "officeId": "1", "articleId": "1"},
        {"datetime": "202609041543", "title": "신한은행, 강원 주민 금융지원", "officeName": "연합뉴스",
         "officeId": "1", "articleId": "2"},
    ]}]
    monkeypatch.setattr("requests.get", _naver_resp(payload))
    cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)

    kept = nb.fetch_naver_news("055550.KS", cutoff, limit=5, label="신한지주")
    assert [i["title"] for i in kept] == ["신한은행, 강원 주민 금융지원"]

    # label 없으면 필터 안 함 (기존 동작 보존)
    assert len(nb.fetch_naver_news("055550.KS", cutoff, limit=5)) == 2


def test_naver_parses_title_press_link_and_entities(monkeypatch):
    monkeypatch.setattr("requests.get", _naver_resp(NAVER_JSON))

    cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)
    out = nb.fetch_naver_news("105560.KS", cutoff, limit=5)

    assert len(out) == 1                                  # 오래된 기사는 cutoff로 걸러짐
    assert out[0]["title"] == "KB금융, 3분기 실적 · 배당 발표"   # &middot; 언이스케이프
    assert out[0]["publisher"] == "연합뉴스"
    # 본문은 n.news.naver.com 기사 주소에서 받는다
    assert out[0]["url"] == "https://n.news.naver.com/mnews/article/2/1"
    assert out[0]["ticker"] == "105560.KS"


# ─── 시장 섹션 (VIX + Google News) ──────────────────────────────────────────

def test_vix_line_calm_shows_distance_to_first_rung():
    line = nb.vix_line(18.0, 20.0)
    assert "1차(30)까지 +12.0" in line and "평온" in line and "-10%" in line


def test_vix_line_in_panic_names_rung_and_next():
    assert "공포 매수 1차 구간" in nb.vix_line(31.0, 22.0) and "다음 35까지 +4.0" in nb.vix_line(31.0, 22.0)
    assert "3차 구간" in nb.vix_line(45.0, None) and "마지막 단계" in nb.vix_line(45.0, None)


def test_macro_section_silent_when_nothing(monkeypatch):
    monkeypatch.setattr(nb, "fetch_vix", lambda: (None, None))
    monkeypatch.setattr(nb, "_google_news", lambda *a, **k: [])
    assert nb.macro_lines() == []




def test_us_tickers_never_form_a_kospi_comovement():
    ch = {"TEM": -7.2, "HIMS": -6.7, "CRWD": -4.1, nb.KOSPI: -0.2}
    assert nb.comovement_lines(ch, {}, datetime(2026, 9, 1, tzinfo=timezone.utc)) == ([], set())


def test_holdings_include_korean_external_with_market_suffix(monkeypatch, tmp_path):
    pf, ext = tmp_path / "p.json", tmp_path / "e.json"
    pf.write_text('{"holdings": {"GOOGL": {}}}', encoding="utf-8")
    ext.write_text('{"214450": {"name": "파마리서치", "market_country": "KR", "yf_ticker": "214450.KQ"},'
                   ' "316140": {"name": "우리금융지주", "market_country": "KR"}}', encoding="utf-8")
    monkeypatch.setattr(nb, "PORTFOLIO_FILE", pf)
    monkeypatch.setattr(nb, "EXTERNAL_FILE", ext)
    monkeypatch.setattr(nb, "load_watchlist", lambda: {})
    assert nb.load_holdings() == ["GOOGL", "214450.KQ", "316140.KS"]
    assert nb.display_labels()["214450.KQ"] == "파마리서치"


def test_same_ticker_second_article_does_not_repeat_tag():
    a, b = dict(ITEMS[0], summary="첫 기사"), dict(ITEMS[0], summary="둘째 기사", url="https://x/2")
    lines = nb.section_lines([a, b], changes={a["ticker"]: -4.27})
    assert lines[1].startswith("• `") and lines[2].startswith("   └ [둘째 기사]")


def test_every_ticker_with_news_gets_one_and_seconds_fill_the_rest(monkeypatch):
    monkeypatch.setattr(nb, "fetch_us_news", lambda t, c, n, **kw: [_item(t, i) for i in range(2)])
    tickers = [f"T{i}" for i in range(nb.MAX_ITEMS + 2)]
    items = nb.fetch_news(tickers, changes={t: -float(i) for i, t in enumerate(tickers)})
    assert {it["ticker"] for it in items} == set(tickers)     # 자리부족으로 빠지는 종목 없음
    few = nb.fetch_news(tickers[:6], changes={t: -float(i) for i, t in enumerate(tickers)})
    assert len(few) == nb.MAX_ITEMS                            # 6종목 + 2번째 기사 2개
