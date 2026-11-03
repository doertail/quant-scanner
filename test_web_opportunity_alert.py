import json
from datetime import datetime, timezone

import web_opportunity_alert as web


def test_selector_uses_sec_company_mapping_not_model_ticker(monkeypatch):
    rows = [{"title": "Abecco wins hospital imaging customers", "url": "https://news.example/a",
             "publisher": "News", "published": datetime.now(timezone.utc).isoformat()}]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": json.dumps({"suggestions": [
                {"index": 0, "ticker": "FAKE", "thesis": "가설", "unknown": "고객"},
            ]})}

    monkeypatch.setattr(web.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web, "ticker_map", lambda: {"ABCC": (1, "Abecco Inc")})
    assert web.select_leads(rows, set(), set()) == [{**rows[0], "ticker": "ABCC",
                                                     "company": "Abecco Inc",
                                                     "thesis": "가설", "unknown": "고객"}]
    assert web.select_leads(rows, {"ABCC"}, set()) == []


def test_unmatched_official_filing_is_not_called_news_proof():
    message = web.format_alert({
        "title": "Hospital automation demand grows", "url": "https://news.example/a",
        "published": "2026-09-27T00:00:00+00:00", "ticker": "ABC", "thesis": "수요 가설",
        "unknown": "매출 확인", "evidence": None,
        "filing": {"form": "10-Q", "url": "https://www.sec.gov/a"},
        "price": None, "cash": None, "financials": None,
    })
    assert "기사 사건과의 일치는 미확인" in message
    assert "매수 신호가 아님" in message
    assert "https://news.example/a" in message


def test_generic_word_does_not_attach_unrelated_company(monkeypatch):
    rows = [{"title": "A backup power strategy for data centers", "url": "https://news.example/a",
             "publisher": "News", "published": datetime.now(timezone.utc).isoformat()}]
    monkeypatch.setattr(web, "ticker_map", lambda: {"MSTR": (1, "Strategy Inc")})
    monkeypatch.setattr(web.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("no model call expected")))
    assert web.select_leads(rows, set(), set()) == []


def test_industry_inference_needs_separate_company_story(monkeypatch):
    rows = [{"title": "Enterprise cybersecurity demand rises", "url": "https://news.example/trend",
             "publisher": "News", "published": datetime.now(timezone.utc).isoformat()}]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": json.dumps({"index": 0, "ticker": "ABCC",
                                            "theme": "cybersecurity", "thesis": "수혜 가설",
                                            "unknown": "고객 매출 확인"})}

    monkeypatch.setattr(web.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(web, "ticker_map", lambda: {"ABCC": (1, "Abecco Inc")})
    monkeypatch.setattr(web, "related_company_article", lambda *args: None)
    assert web.infer_industry_lead(rows, set(), set()) is None
    related = {"title": "Abecco cybersecurity customer deal", "url": "https://news.example/company"}
    monkeypatch.setattr(web, "related_company_article", lambda *args: related)
    result = web.infer_industry_lead(rows, set(), set())
    assert result["ticker"] == "ABCC"
    assert result["related_article"] == related
    assert "연결은 추론" in web.format_alert({**result, "evidence": None, "filing": None,
                                           "price": None, "cash": None, "financials": None})


def test_failed_discord_does_not_mark_lead_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(web, "fetch_news", lambda: [{"url": "https://news.example/a"}])
    monkeypatch.setattr(web, "held_symbols", set)
    monkeypatch.setattr(web, "select_leads", lambda *args: [{"ticker": "ABC",
                                                             "url": "https://news.example/a"}])
    monkeypatch.setattr(web, "review", lambda item: item)
    monkeypatch.setattr(web, "format_alert", lambda item: "research")
    monkeypatch.setattr(web, "send_discord", lambda *args, **kwargs: False)
    try:
        web.run(send=True)
    except RuntimeError:
        pass
    else:
        assert False, "delivery failure must be visible"
    assert not web.STATE.exists()
