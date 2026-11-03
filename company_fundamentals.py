"""Conservative trailing fundamentals from SEC XBRL companyfacts.

Uses a fiscal-year annual value plus the latest year-to-date period minus the
same prior-year period. Missing comparable facts remain missing; no fabricated
zeroes or annualized quarter values are used.
"""

from datetime import date, timedelta

from official_evidence import get_json, ticker_map

REVENUE_TAGS = ("RevenueFromContractWithCustomerIncludingAssessedTax",
                "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet")
NET_INCOME_TAGS = ("NetIncomeLoss",)
OPERATING_CASH_TAGS = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment",)
SHARES_TAGS = ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding")


def rows_for(facts: dict, tags: tuple[str, ...], unit: str = "USD") -> list[dict]:
    for namespace in ("us-gaap", "dei"):
        table = facts.get(namespace) or {}
        for tag in tags:
            rows = ((table.get(tag) or {}).get("units") or {}).get(unit) or []
            if rows:
                return rows
    return []


def period_days(row: dict) -> int:
    try:
        return (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days + 1
    except (KeyError, TypeError, ValueError):
        return 0


def latest_annual(rows: list[dict]) -> dict | None:
    annual = [row for row in rows if row.get("form") == "10-K" and
              330 <= period_days(row) <= 380]
    return max(annual, key=lambda row: (row["end"], row.get("filed", "")), default=None)


def previous_year(day: str) -> str:
    value = date.fromisoformat(day)
    try:
        return value.replace(year=value.year - 1).isoformat()
    except ValueError:  # February 29
        return value.replace(year=value.year - 1, day=28).isoformat()


def trailing_metric(rows: list[dict]) -> dict | None:
    annual = latest_annual(rows)
    if not annual:
        return None
    fy_end = date.fromisoformat(annual["end"])
    next_start = (fy_end + timedelta(days=1)).isoformat()
    current = [row for row in rows if row.get("form") == "10-Q" and
               row.get("start") == next_start and 60 <= period_days(row) <= 300]
    if not current:
        return {"value": annual["val"], "end": annual["end"], "annual_only": True}
    current_row = max(current, key=lambda row: (row["end"], row.get("filed", "")))
    old_start, old_end = previous_year(current_row["start"]), previous_year(current_row["end"])
    prior = [row for row in rows if row.get("start") == old_start and
             row.get("end") == old_end]
    if not prior:
        return None
    prior_row = max(prior, key=lambda row: row.get("filed", ""))
    return {"value": annual["val"] + current_row["val"] - prior_row["val"],
            "end": current_row["end"], "annual_only": False,
            "ytd_growth": (current_row["val"] / prior_row["val"] - 1)
            if prior_row["val"] else None}


def latest_instant(rows: list[dict]) -> dict | None:
    values = [row for row in rows if row.get("start") is None and
              row.get("form") in ("10-K", "10-Q")]
    return max(values, key=lambda row: (row["end"], row.get("filed", "")), default=None)


def recent_period(end: str, max_age_days: int, as_of: date | None = None) -> bool:
    try:
        age = ((as_of or date.today()) - date.fromisoformat(end)).days
        return 0 <= age <= max_age_days
    except (TypeError, ValueError):
        return False


def fundamentals(ticker: str, price: float) -> dict:
    company = ticker_map().get(ticker)
    if not company:
        raise ValueError(f"SEC CIK 없음: {ticker}")
    cik, name = company
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    facts = get_json(url)["facts"]
    metrics = {
        "revenue": trailing_metric(rows_for(facts, REVENUE_TAGS)),
        "net_income": trailing_metric(rows_for(facts, NET_INCOME_TAGS)),
        "operating_cash": trailing_metric(rows_for(facts, OPERATING_CASH_TAGS)),
        "capex": trailing_metric(rows_for(facts, CAPEX_TAGS)),
    }
    for name, metric in metrics.items():
        if metric and not recent_period(metric.get("end"), 450):
            metrics[name] = None
    shares = latest_instant(rows_for(facts, SHARES_TAGS, "shares"))
    if shares and recent_period(shares.get("end"), 180):
        metrics["market_cap"] = shares["val"] * price
        metrics["shares_as_of"] = shares["end"]
    elif shares:
        metrics["shares_stale_as_of"] = shares.get("end")
    if metrics["operating_cash"] and metrics["capex"] and metrics.get("market_cap"):
        cash_flow = metrics["operating_cash"]["value"] - metrics["capex"]["value"]
        metrics["historical_fcf"] = cash_flow
        metrics["historical_fcf_yield"] = cash_flow / metrics["market_cap"]
    metrics["source"] = url
    metrics["company"] = name
    return metrics


def display_fundamentals(data: dict) -> str:
    parts = []
    revenue = data.get("revenue")
    if revenue:
        growth = revenue.get("ytd_growth")
        growth_label = f", 최근 누적매출 전년비 {growth:+.0%}" if growth is not None else ""
        parts.append(f"직전 12개월 매출 ${revenue['value']/1e9:.2f}B{growth_label}")
    if data.get("market_cap"):
        parts.append(f"시총 약 ${data['market_cap']/1e9:.2f}B (주식수 기준 {data['shares_as_of']})")
        if revenue and revenue["value"] > 0:
            parts.append(f"시총/직전 12개월 매출 {data['market_cap']/revenue['value']:.1f}배")
    elif data.get("shares_stale_as_of"):
        parts.append(f"발행주식 수 자료 {data['shares_stale_as_of']} 기준으로 오래돼 시총 미산출")
    if data.get("historical_fcf_yield") is not None:
        parts.append(f"과거 12개월 잉여현금흐름 수익률 약 {data['historical_fcf_yield']:.1%}")
    return "; ".join(parts) if parts else "비교 가능한 SEC 재무 수치 없음"
