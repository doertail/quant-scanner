"""
universe.py — 트레이딩 유니버스 조회

S&P 500, Nasdaq-100 종목 리스트를 Wikipedia에서 직접 스크랩.
yfinance ticker 형식에 맞춰 '.'을 '-'로 변환 (BRK.B → BRK-B).
"""

from io import StringIO

import pandas as pd
import requests

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36'
    ),
}


def get_sp500_tickers() -> list[str]:
    resp = requests.get(
        'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        headers=_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Symbol' in table.columns:
            return table['Symbol'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("S&P 500 ticker 테이블을 찾을 수 없음")


def get_nasdaq100_tickers() -> list[str]:
    resp = requests.get(
        'https://en.wikipedia.org/wiki/Nasdaq-100',
        headers=_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        if 'Ticker' in table.columns:
            return table['Ticker'].str.replace('.', '-', regex=False).tolist()
    raise ValueError("Nasdaq-100 ticker 테이블을 찾을 수 없음")
