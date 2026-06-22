"""
toss_client.py — 토스증권 Open API 클라이언트 (조회 전용)

OAuth2 Client Credentials 흐름으로 access token을 발급받아
계좌 목록·보유종목(국내+미국)을 조회한다.

향후 2단계(시세)·3단계(주문)는 이 클라이언트에 메서드를 추가하는 방식으로 확장.
주문 메서드는 의도적으로 포함하지 않음 (조회 전용 단계).

공식 문서: https://developers.tossinvest.com/docs
OpenAPI 스펙: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
"""

import logging
import os
import time

import requests

_log = logging.getLogger(__name__)

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
DEFAULT_TIMEOUT = 10


class TossAPIError(RuntimeError):
    """토스 API 호출 실패 (인증·네트워크·응답 형식 등). 원본 응답 본문을 함께 보존."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class TossClient:
    """토스증권 Open API 조회 클라이언트.

    사용:
        client = TossClient()              # .env의 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 사용
        accounts = client.get_accounts()
        holdings = client.get_holdings(account_seq)
    """

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.getenv("TOSS_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("TOSS_CLIENT_SECRET", "")
        if not self.client_id or not self.client_secret:
            raise TossAPIError(
                "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 미설정 — .env에 발급받은 키를 넣어주세요."
            )
        self._token: str | None = None
        self._token_expiry: float = 0.0  # epoch seconds

    # ─── 인증 ──────────────────────────────────────────────────────────────
    def _get_token(self) -> str:
        """access token 발급. 만료 60초 전까지는 캐시된 토큰 재사용."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        try:
            resp = requests.post(
                BASE_URL + TOKEN_PATH,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise TossAPIError(f"토큰 발급 네트워크 오류: {e}") from e

        if resp.status_code != 200:
            raise TossAPIError(
                f"토큰 발급 실패 (HTTP {resp.status_code})",
                status=resp.status_code,
                body=resp.text,
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise TossAPIError("토큰 응답이 JSON이 아님", body=resp.text) from e

        token = data.get("access_token")
        if not token:
            raise TossAPIError("토큰 응답에 access_token 없음", body=resp.text)

        expires_in = data.get("expires_in", 3600)
        self._token = token
        self._token_expiry = time.time() + float(expires_in)
        _log.info("토스 토큰 발급 완료 (만료 %ss)", expires_in)
        return token

    # ─── 공통 요청 ─────────────────────────────────────────────────────────
    def _get(self, path: str, account_seq: int | str | None = None, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)

        try:
            resp = requests.get(
                BASE_URL + path,
                headers=headers,
                params=params,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise TossAPIError(f"{path} 네트워크 오류: {e}") from e

        if resp.status_code != 200:
            raise TossAPIError(
                f"{path} 호출 실패 (HTTP {resp.status_code})",
                status=resp.status_code,
                body=resp.text,
            )
        try:
            return resp.json()
        except ValueError as e:
            raise TossAPIError(f"{path} 응답이 JSON이 아님", body=resp.text) from e

    # ─── 조회 메서드 ───────────────────────────────────────────────────────
    def get_accounts(self) -> dict:
        """계좌 목록 조회. GET /api/v1/accounts"""
        return self._get("/api/v1/accounts")

    def get_holdings(self, account_seq: int | str, symbol: str | None = None) -> dict:
        """보유 주식 조회 (국내+미국 통합). GET /api/v1/holdings

        account_seq: get_accounts() 결과의 accountSeq 값
        symbol: 특정 종목만 필터링 (선택)
        """
        params = {"symbol": symbol} if symbol else None
        return self._get("/api/v1/holdings", account_seq=account_seq, params=params)

    def get_buying_power(self, account_seq: int | str, currency: str) -> dict:
        """매수 가능 금액(현금성 예수금) 조회. GET /api/v1/buying-power

        currency: "KRW" 또는 "USD" (통화별로 따로 조회)
        응답 result.cashBuyingPower = 순수 현금 기반 매수 가능 금액
        """
        return self._get(
            "/api/v1/buying-power",
            account_seq=account_seq,
            params={"currency": currency},
        )

    def get_prices(self, symbols) -> dict:
        """현재가 조회 (마켓 데이터, 계좌 불필요). GET /api/v1/prices

        symbols: 종목 심볼 리스트 또는 콤마 구분 문자열 (최대 200개)
                 국내는 종목코드(예: '005930'), 미국은 티커(예: 'AAPL')
        응답 result = [{symbol, lastPrice, currency, timestamp}]
        """
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join(str(s) for s in symbols)
        return self._get("/api/v1/prices", params={"symbols": symbols})

    def get_orderbook(self, symbol: str) -> dict:
        """호가 조회. GET /api/v1/orderbook → result.asks / result.bids"""
        return self._get("/api/v1/orderbook", params={"symbol": symbol})

    def get_price_limits(self, symbol: str) -> dict:
        """상/하한가 조회. GET /api/v1/price-limits (미국 등은 null)"""
        return self._get("/api/v1/price-limits", params={"symbol": symbol})

    def get_exchange_rate(self, base_currency: str, quote_currency: str, date_time: str | None = None) -> dict:
        """환율 조회. GET /api/v1/exchange-rate (예: base=USD, quote=KRW)"""
        params = {"baseCurrency": base_currency, "quoteCurrency": quote_currency}
        if date_time:
            params["dateTime"] = date_time
        return self._get("/api/v1/exchange-rate", params=params)

    def get_sellable_quantity(self, account_seq: int | str, symbol: str) -> dict:
        """판매 가능 수량 조회. GET /api/v1/sellable-quantity"""
        return self._get(
            "/api/v1/sellable-quantity",
            account_seq=account_seq,
            params={"symbol": symbol},
        )

    def get_commissions(self, account_seq: int | str) -> dict:
        """매매 수수료 조회. GET /api/v1/commissions → result[].commissionRate(%)"""
        return self._get("/api/v1/commissions", account_seq=account_seq)

    def get_stocks(self, symbols) -> dict:
        """종목 기본 정보 조회. GET /api/v1/stocks (콤마 구분 최대 200)"""
        if isinstance(symbols, (list, tuple)):
            symbols = ",".join(str(s) for s in symbols)
        return self._get("/api/v1/stocks", params={"symbols": symbols})

    def get_stock_warnings(self, symbol: str) -> dict:
        """매수 유의사항 조회. GET /api/v1/stocks/{symbol}/warnings"""
        return self._get(f"/api/v1/stocks/{symbol}/warnings")

    def get_market_calendar(self, country: str, date: str | None = None) -> dict:
        """장 운영 정보 조회. GET /api/v1/market-calendar/{KR|US}"""
        country = country.upper()
        if country not in ("KR", "US"):
            raise TossAPIError(f"country는 KR 또는 US여야 함: {country}")
        params = {"date": date} if date else None
        return self._get(f"/api/v1/market-calendar/{country}", params=params)

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        count: int | None = None,
        before: str | None = None,
        adjusted: bool | None = None,
    ) -> dict:
        """캔들 차트 조회. GET /api/v1/candles

        interval: '1m' 또는 '1d' / count: 최대 200 / before: 페이지네이션(ISO8601)
        adjusted: 수정주가 적용 여부
        """
        if interval not in ("1m", "1d"):
            raise TossAPIError(f"interval은 '1m' 또는 '1d': {interval}")
        params: dict = {"symbol": symbol, "interval": interval}
        if count is not None:
            params["count"] = count
        if before:
            params["before"] = before
        if adjusted is not None:
            params["adjusted"] = str(adjusted).lower()
        return self._get("/api/v1/candles", params=params)
