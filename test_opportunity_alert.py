import json

import opportunity_alert as oa


def article(title, ticker="AKAM", url="https://example.com/a", score="0.9"):
    return {"title": title, "url": url, "source": "News", "summary": "Summary",
            "ticker_sentiment": [{"ticker": ticker, "relevance_score": score}],
            "time_published": "20260925T120000"}


def test_candidates_reject_routine_noise_and_held_positions():
    rows = [article("Akamai signs $11.6 billion customer contract"),
            article("Exxon submits 2025 resource extraction report", "XOM", "https://example.com/b"),
            article("CRWD wins new contract", "CRWD", "https://example.com/c"),
            article("Boeing reaches tentative deal with engineers to avoid strike", "BA",
                    "https://example.com/d")]
    got = oa.candidates(rows, set(), {"CRWD"})
    assert [row["tickers"] for row in got] == [["AKAM"]]


def test_invalid_llm_ticker_cannot_become_alert(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"response": json.dumps({"index": 0, "ticker": "FAKE",
                                            "why": "이유", "unknown": "한계"})}
    monkeypatch.setattr(oa.requests, "post", lambda *a, **kw: Response())
    assert oa.choose_one(oa.candidates([article("Akamai signs $11.6 billion contract")], set(), set())) is None


def test_failed_delivery_keeps_candidate_unseen(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(oa, "STATE", state)
    monkeypatch.setattr(oa, "PORTFOLIO", tmp_path / "portfolio.json")
    monkeypatch.setattr(oa, "fetch_articles", lambda: [article("Akamai signs $11.6 billion contract")])
    monkeypatch.setattr(oa, "verified_items", lambda items: [{**items[0], "evidence": {
        "url": "https://sec.gov/a", "form": "8-K", "filed": "2026-09-24"}}])
    monkeypatch.setattr(oa, "choose_one", lambda items: {**items[0], "ticker": "AKAM",
                                                       "why": "고객 계약", "unknown": "비용"})
    monkeypatch.setattr(oa, "review_candidate", lambda item: {
        **item, "analysis": {"verdict": "보류", "reason": "이유", "risk": "위험", "trigger": "조건"},
        "price": {"lastPrice": "113", "timestamp": "now"}, "cash": 1000,
        "financials": {"source": "https://sec.gov/facts"}})
    monkeypatch.setattr(oa, "send_discord", lambda *a, **kw: False)
    try:
        oa.run(send=True)
    except RuntimeError:
        pass
    else:
        assert False, "delivery failure must be visible"
    assert not state.exists()


def test_success_records_review_and_prevents_repeat(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(oa, "STATE", state)
    monkeypatch.setattr(oa, "PORTFOLIO", tmp_path / "portfolio.json")
    monkeypatch.setattr(oa, "fetch_articles", lambda: [article("Akamai signs $11.6 billion contract")])
    monkeypatch.setattr(oa, "verified_items", lambda items: [{**items[0], "evidence": {
        "url": "https://sec.gov/a", "form": "8-K", "filed": "2026-09-24"}}])
    monkeypatch.setattr(oa, "choose_one", lambda items: {**items[0], "ticker": "AKAM"})
    monkeypatch.setattr(oa, "review_candidate", lambda item: {
        **item, "analysis": {"verdict": "보류", "reason": "투자비", "risk": "희석", "trigger": "계약 비용"},
        "price": {"lastPrice": "113", "timestamp": "now"}, "cash": 1000,
        "financials": {"source": "https://sec.gov/facts"}})
    sent = []
    monkeypatch.setattr(oa, "send_discord", lambda message, **kwargs: sent.append(message) or True)
    assert oa.run(send=True) == 0
    assert oa.run(send=True) == 0
    record = json.loads(state.read_text())
    assert len(sent) == 1
    assert record["reviews"][0]["verdict"] == "보류"
    assert record["reviews"][0]["filing"] == "https://sec.gov/a"


def test_one_alert_per_24_hours_even_with_a_new_candidate(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timezone

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"seen": {}, "alerted_tickers": {
        "AKAM": datetime.now(timezone.utc).isoformat()}}))
    monkeypatch.setattr(oa, "STATE", state)
    monkeypatch.setattr(oa, "fetch_articles", lambda: (_ for _ in ()).throw(
        AssertionError("no feed call during cooldown")))
    assert oa.run(send=True) == 0
    assert "24시간" in capsys.readouterr().out


def test_verified_items_requires_filing(monkeypatch):
    monkeypatch.setattr(oa, "find_8k_evidence", lambda ticker, title: None)
    assert oa.verified_items(oa.candidates([article("Akamai signs $11.6 billion contract")],
                                          set(), set())) == []


def test_alert_shows_official_source_account_and_no_order():
    message = oa.format_alert({
        "ticker": "AKAM", "title": "Akamai contract", "url": "https://news.example/a",
        "evidence": {"form": "8-K", "url": "https://sec.gov/a", "filed": "2026-09-24"},
        "analysis": {"verdict": "보류", "reason": "투자비", "risk": "희석", "trigger": "계약 비용"},
        "price": {"lastPrice": "113.5", "timestamp": "2026-09-26"}, "cash": 2000,
        "financials": {"source": "https://sec.gov/facts"}})
    assert "SEC 8-K" in message and "예수금 $2,000" in message
    assert "주문도 보내지 않음" in message


def test_large_capex_blocks_optimistic_model_buy(monkeypatch):
    class Client:
        def get_prices(self, symbols):
            return {"result": [{"symbol": "AKAM", "lastPrice": "113.5"}]}

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"response": json.dumps({"verdict": "매수 검토",
                "reason": "워런트 발행으로 안정성 확보", "risk": "시장 변동성",
                "trigger": "실적 발표", "source_quote": "Anthropic committed to pay $11.6 billion"})}

    monkeypatch.setattr(oa, "TossClient", Client)
    monkeypatch.setattr(oa, "live_snapshot", lambda client: ([], 2113.79))
    monkeypatch.setattr(oa, "fundamentals", lambda ticker, price: {"source": "https://sec.gov/facts"})
    monkeypatch.setattr(oa.requests, "post", lambda *args, **kwargs: Response())
    item = {"ticker": "AKAM", "title": "Akamai deal", "summary": "Contract",
            "evidence": {"excerpt": "Anthropic committed to pay $11.6 billion. "
                                    "Total capital expenditures $5.5 billion. "
                                    "A warrant may dilute shareholders."}}
    reviewed = oa.review_candidate(item)
    assert reviewed["analysis"]["verdict"] == "보류"
    assert "선행 설비투자" in reviewed["analysis"]["reason"]
    assert "희석" in reviewed["analysis"]["risk"]


def test_buy_review_priority_requires_growth_cash_flow_price_and_no_large_commitments():
    metrics = {"revenue": {"value": 2_000_000_000, "ytd_growth": 0.20},
               "market_cap": 16_000_000_000, "historical_fcf_yield": 0.04}
    assert oa.eligible_for_buy_review(metrics, "Customer committed to pay $1 billion")
    assert not oa.eligible_for_buy_review(metrics, "Customer committed to pay $1 billion. "
                                          "Capital expenditures of $5 billion are required")
    assert not oa.eligible_for_buy_review({**metrics, "market_cap": 30_000_000_000},
                                          "Customer committed to pay $1 billion")
    assert not oa.eligible_for_buy_review({**metrics, "historical_fcf_yield": None},
                                          "Customer committed to pay $1 billion")


def test_invalid_model_quote_falls_back_to_verified_hold(monkeypatch):
    class Client:
        def get_prices(self, symbols):
            return {"result": [{"symbol": "AKAM", "lastPrice": "113.5"}]}

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"response": json.dumps({"verdict": "매수 검토", "reason": "사라",
                                            "risk": "없음", "trigger": "언젠가",
                                            "source_quote": "not in SEC filing"})}

    monkeypatch.setattr(oa, "TossClient", Client)
    monkeypatch.setattr(oa, "live_snapshot", lambda client: ([], 2113.79))
    monkeypatch.setattr(oa, "fundamentals", lambda ticker, price: {"source": "https://sec.gov/facts"})
    monkeypatch.setattr(oa.requests, "post", lambda *args, **kwargs: Response())
    item = {"ticker": "AKAM", "title": "Akamai deal", "summary": "Contract",
            "evidence": {"excerpt": "Anthropic committed to pay $11.6 billion. "
                                    "Total capital expenditures $5.5 billion."}}
    reviewed = oa.review_candidate(item)
    assert reviewed["analysis"]["verdict"] == "보류"
    assert reviewed["analysis"]["reason"] != "사라"


def test_verified_lead_survives_local_selector_failure(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(oa, "STATE", state)
    monkeypatch.setattr(oa, "PORTFOLIO", tmp_path / "portfolio.json")
    monkeypatch.setattr(oa, "fetch_articles", lambda: [article("Akamai signs $11.6 billion contract")])
    monkeypatch.setattr(oa, "verified_items", lambda items: [{**items[0], "tickers": ["AKAM"],
                        "evidence": {"url": "https://sec.gov/a", "form": "8-K",
                                     "filed": "2026-09-24"}}])
    monkeypatch.setattr(oa, "choose_one", lambda items: (_ for _ in ()).throw(ValueError("model")))
    monkeypatch.setattr(oa, "review_candidate", lambda item: {
        **item, "analysis": {"verdict": "보류", "reason": "이유", "risk": "위험", "trigger": "조건"},
        "price": {"lastPrice": "113", "timestamp": "now"}, "cash": 1000,
        "financials": {"source": "https://sec.gov/facts"}})
    sent = []
    monkeypatch.setattr(oa, "send_discord", lambda message, **kwargs: sent.append(message) or True)
    assert oa.run(send=True) == 0
    assert len(sent) == 1
    assert json.loads(state.read_text())["reviews"][0]["ticker"] == "AKAM"
