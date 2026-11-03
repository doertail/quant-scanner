"""Read recent SEC filings to corroborate material US-company news.

An article is never treated as an official source. Only a recent 8-K whose
text contains a distinctive counterparty and amount is returned as evidence.
Lack of a match means 'unverified', not 'false'.
"""

import re
from datetime import date, timedelta
from functools import lru_cache

import requests
from bs4 import BeautifulSoup

SEC_HEADERS = {"User-Agent": "QuantScanner personal investment research contact local-user@localhost.invalid",
               "Accept-Encoding": "gzip, deflate"}
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
STOP = {"Akamai", "Enphase", "Boeing", "Company", "Stock", "Shares", "Lands",
        "Deal", "Agreement", "Contract", "Billion", "Million", "With", "From",
        "Announces", "Wins", "New", "The", "And", "Can", "Will", "Higher"}


def get_json(url: str):
    response = requests.get(url, headers=SEC_HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def get_text(url: str) -> str:
    response = requests.get(url, headers=SEC_HEADERS, timeout=15)
    response.raise_for_status()
    return re.sub(r"\s+", " ", BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True))


@lru_cache(maxsize=1)
def ticker_map() -> dict[str, tuple[int, str]]:
    rows = get_json(SEC_TICKERS)
    return {row["ticker"].upper(): (int(row["cik_str"]), row["title"])
            for row in rows.values()}


def recent_filings(ticker: str, forms=("8-K",), days: int = 7,
                   limit: int = 5) -> list[dict]:
    company = ticker_map().get(ticker)
    if not company:
        return []
    cik, name = company
    rows = get_json(SEC_SUBMISSIONS.format(cik=cik))["filings"]["recent"]
    cutoff = date.today() - timedelta(days=days)
    filings = []
    for form, filed, accession, doc in zip(rows["form"], rows["filingDate"],
                                           rows["accessionNumber"], rows["primaryDocument"]):
        if filed < cutoff.isoformat():
            break
        if form in forms:
            base = SEC_ARCHIVE.format(cik=cik, accession=accession.replace("-", ""))
            filings.append({"ticker": ticker, "company": name, "form": form,
                            "filed": filed, "url": base + doc, "base": base})
    return filings[:limit]


def headline_markers(title: str, company: str) -> tuple[list[str], list[str]]:
    amounts = re.findall(r"\$\s*[\d,.]+\s*(?:billion|million|bn|mn|b|m)\b", title,
                         flags=re.IGNORECASE)
    company_words = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", company)}
    names = [word for word in re.findall(r"\b[A-Z][a-z]{4,}\b", title)
             if word not in STOP and word.lower() not in company_words]
    return amounts, names


def corroborated(title: str, company: str, text: str) -> bool:
    amounts, names = headline_markers(title, company)
    lower = text.lower()
    if not amounts or not names:
        return False
    for amount in amounts:
        number, unit = re.search(r"([\d,.]+)\s*(billion|million|bn|mn|b|m)", amount,
                                 flags=re.IGNORECASE).groups()
        units = {"billion": "(?:billion|bn)", "bn": "(?:billion|bn)", "b": "(?:billion|bn)",
                 "million": "(?:million|mn)", "mn": "(?:million|mn)", "m": "(?:million|mn)"}
        amount_pattern = rf"\$?\s*{re.escape(number)}\s*{units[unit.lower()]}\b"
        for match in re.finditer(amount_pattern, lower):
            window = lower[max(0, match.start() - 600):match.end() + 600]
            if any(name.lower() in window for name in names) and not re.search(
                    r"credit agreement|loan facility|senior notes|underwriting agreement", window):
                return True
    return False


def evidence_excerpt(text: str, title: str, company: str, limit: int = 4500) -> str:
    amounts, names = headline_markers(title, company)
    markers = names + amounts
    matches = []
    for marker in markers:
        match = re.search(re.escape(marker), text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - 350)
            matches.append(text[start:start + 1000])
    return " ... ".join(dict.fromkeys(matches))[:limit] or text[:limit]


def exhibit_991(filing: dict) -> dict | None:
    """Get an 8-K EX-99.1, including issuer-prefixed SEC filenames."""
    entries = get_json(filing["base"] + "index.json")["directory"]["item"]
    for entry in entries:
        name = entry.get("name") or ""
        if (re.search(r"ex(?:hibit)?[._-]?99[._-]?1\b", name, re.IGNORECASE)
                and re.search(r"\.html?$", name, re.IGNORECASE)):
            url = filing["base"] + name
            return {"url": url, "text": get_text(url)}
    return None


def find_8k_evidence(ticker: str, title: str) -> dict | None:
    """Find a recent, matching 8-K and its issuer press-release exhibit."""
    for filing in recent_filings(ticker):
        text = get_text(filing["url"])
        if not corroborated(title, filing["company"], text):
            continue
        excerpt = evidence_excerpt(text, title, filing["company"])
        # EX-99.1 often carries the issuer's announcement and economics.
        try:
            exhibit = exhibit_991(filing)
            if exhibit and corroborated(title, filing["company"], exhibit["text"]):
                excerpt += " ... " + exhibit["text"][:5000]
        except (requests.RequestException, ValueError, KeyError):
            pass  # Primary 8-K remains the verified source.
        return {**filing, "excerpt": excerpt[:9000]}
    return None
