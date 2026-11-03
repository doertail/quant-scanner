import official_evidence as oe
from official_evidence import corroborated, headline_markers


def test_contract_needs_distinctive_counterparty_and_amount():
    title = "Akamai (AKAM) Lands an $11.6 Billion Anthropic Deal"
    company = "AKAMAI TECHNOLOGIES INC"
    assert corroborated(title, company, "Anthropic committed to pay $11.6 billion over seven years")
    assert not corroborated(title, company, "Akamai repurchased $11.6 billion of shares")
    assert not corroborated(title, company, "Anthropic expanded its cloud relationship")
    assert not corroborated("Axon signs deal with Newport Beach Police Department",
                            "AXON ENTERPRISE INC", "Newport Beach Police Department uses Axon")


def test_company_name_is_not_distinctive_counterparty():
    amounts, names = headline_markers("Akamai Lands $11.6 Billion Deal", "AKAMAI TECHNOLOGIES INC")
    assert amounts
    assert names == []


def test_exhibit_991_accepts_issuer_prefixed_filename(monkeypatch):
    monkeypatch.setattr(oe, "get_json", lambda url: {"directory": {"item": [
        {"name": "crwd-20260826xex991.htm"}]}})
    monkeypatch.setattr(oe, "get_text", lambda url: "official earnings release")
    exhibit = oe.exhibit_991({"base": "https://sec.gov/filing/"})
    assert exhibit == {"url": "https://sec.gov/filing/crwd-20260826xex991.htm",
                       "text": "official earnings release"}


def test_contract_evidence_keeps_exhibit_costs(monkeypatch):
    filing = {"ticker": "AKAM", "company": "Akamai Technologies", "form": "8-K",
              "filed": "2026-09-24", "url": "https://sec.gov/filing/main.htm",
              "base": "https://sec.gov/filing/"}
    monkeypatch.setattr(oe, "recent_filings", lambda ticker: [filing])
    monkeypatch.setattr(oe, "get_text", lambda url:
                        "Anthropic committed to pay $11.6 billion over seven years")
    monkeypatch.setattr(oe, "exhibit_991", lambda row: {"url": row["base"] + "ex991.htm",
                        "text": "Anthropic committed to pay $11.6 billion. "
                                "Total capital expenditures could be $5.5 billion. "
                                "A warrant may dilute shares."})
    evidence = oe.find_8k_evidence("AKAM", "Akamai signs $11.6 billion Anthropic contract")
    assert "capital expenditures" in evidence["excerpt"]
