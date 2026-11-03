import json

import pytest

import thesis_monitor as tm


@pytest.fixture(autouse=True)
def _crwd_thesis(monkeypatch):
    # CRWD는 실보유에서 빠졌지만 ARR 비교 로직 검증용 픽스처로 계속 쓴다
    monkeypatch.setitem(tm.THESES, "CRWD", "테스트용 논지")


def filing(url="https://sec.gov/new"):
    return {"ticker": "CRWD", "form": "8-K", "filed": "2026-09-26", "url": url}


def test_first_run_baselines_without_alert(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(tm, "STATE", state)
    monkeypatch.setattr(tm, "recent_filings", lambda ticker, **kwargs: [filing()] if ticker == "CRWD" else [])
    monkeypatch.setattr(tm, "send_discord", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("baseline must not alert")))
    assert tm.run(send=True) == 0
    assert json.loads(state.read_text())["seen"] == ["https://sec.gov/new"]


def test_new_filing_sent_once(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"initialized": "2026-09-25", "seen": []}))
    sent = []
    monkeypatch.setattr(tm, "STATE", state)
    monkeypatch.setattr(tm, "recent_filings", lambda ticker, **kwargs: [filing()] if ticker == "CRWD" else [])
    monkeypatch.setattr(tm, "get_text", lambda url: "Annual recurring revenue increased")
    monkeypatch.setattr(tm, "send_discord", lambda message, **kwargs: sent.append(message) or True)
    assert tm.run(send=True) == 0
    assert tm.run(send=True) == 0
    assert len(sent) == 1


def test_failed_delivery_does_not_mark_filing_seen(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"initialized": "2026-09-25", "seen": []}))
    monkeypatch.setattr(tm, "STATE", state)
    monkeypatch.setattr(tm, "recent_filings", lambda ticker, **kwargs: [filing()] if ticker == "CRWD" else [])
    monkeypatch.setattr(tm, "get_text", lambda url: "Revenue recognition inquiry")
    monkeypatch.setattr(tm, "send_discord", lambda *args, **kwargs: False)
    try:
        tm.run(send=True)
    except RuntimeError:
        pass
    else:
        assert False, "delivery failure must remain visible"
    assert json.loads(state.read_text())["seen"] == []


def test_only_explicit_8k_events_get_urgent_priority():
    assert "긴급" in tm.classify_filing("Item 4.02 Non-Reliance on Previously Issued Financial Statements")
    assert "긴급" in tm.classify_filing("Item 1.05 Material Cybersecurity Incidents")
    assert "긴급" not in tm.classify_filing("Generic risk factor mentions a possible restatement")
    assert "긴급" in tm.classify_filing(
        "Item 8.01 Other Events. A sensor update resulted in outages for customers")


def test_financial_deterioration_triggers_review_without_sell_order():
    flags = tm.financial_flags({"revenue": {"ytd_growth": -0.08},
                                "operating_cash": {"value": -1}})
    message = tm.format_alert({**filing(), "form": "10-Q"}, "매출 감소", "정기 보유 근거 검토",
                              "직전 12개월 매출 $1B", flags)
    assert "매도 여부 판단" in message
    assert "누적 매출 전년비 -8%" in message
    assert "자동 주문 없음" in message


def test_urgent_filing_requests_immediate_review():
    message = tm.format_alert(filing(), "Item 4.02 Non-Reliance", "재무제표 신뢰 문제 긴급 확인")
    assert "매도 여부 긴급 검토" in message


def test_crwd_earnings_exhibit_metrics_are_compared_and_recorded(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"initialized": "2026-09-25", "seen": []}))
    new_filing = {**filing(), "base": "https://sec.gov/filing/"}
    exhibit_text = ("Annual Recurring Revenue (ARR) grew 19% year-over-year to "
                    "$6.20 billion, of which $300.0 million was net new ARR added in the quarter.")
    sent = []
    monkeypatch.setattr(tm, "STATE", state)
    monkeypatch.setattr(tm, "recent_filings", lambda ticker, **kwargs:
                        [new_filing] if ticker == "CRWD" else [])
    monkeypatch.setattr(tm, "get_text", lambda url: "Item 2.02 Results of Operations")
    monkeypatch.setattr(tm, "exhibit_991", lambda row:
                        {"url": row["base"] + "ex991.htm", "text": exhibit_text})
    monkeypatch.setattr(tm, "send_discord", lambda message, **kwargs: sent.append(message) or True)
    assert tm.run(send=True) == 0
    assert "신규 ARR 직전 분기보다 감소" in sent[0]
    assert "실적 발표문 원문" in sent[0]
    assert json.loads(state.read_text())["kpi"]["CRWD"]["arr_b"] == 6.2


def test_failed_earnings_alert_does_not_advance_kpi_baseline(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"initialized": "2026-09-25", "seen": [],
                                 "kpi": {"CRWD": tm.CRWD_BASELINE}}))
    new_filing = {**filing(), "base": "https://sec.gov/filing/"}
    monkeypatch.setattr(tm, "STATE", state)
    monkeypatch.setattr(tm, "recent_filings", lambda ticker, **kwargs:
                        [new_filing] if ticker == "CRWD" else [])
    monkeypatch.setattr(tm, "get_text", lambda url: "Item 2.02")
    monkeypatch.setattr(tm, "exhibit_991", lambda row: {"url": row["base"] + "ex991.htm",
                        "text": "Annual Recurring Revenue (ARR) grew 19% year-over-year to "
                                "$6.20 billion, of which $300 million was net new ARR."})
    monkeypatch.setattr(tm, "send_discord", lambda *args, **kwargs: False)
    try:
        tm.run(send=True)
    except RuntimeError:
        pass
    else:
        assert False, "delivery failure must remain visible"
    saved = json.loads(state.read_text())
    assert saved["kpi"]["CRWD"]["arr_b"] == 5.84
    assert saved["seen"] == []


def test_arr_below_prior_company_guidance_is_flagged():
    _, flags = tm.crwd_comparison({"arr_b": 6.10, "arr_growth_pct": 24,
                                   "net_new_arr_m": 350}, tm.CRWD_BASELINE)
    assert "ARR가 직전 회사 전망 하단 미달" in flags
    assert "신규 ARR 직전 분기보다 감소" not in flags
