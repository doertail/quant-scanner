# blockchain-rsi-monitor

## 프로젝트 개요

S&P500 + NDX100 전수 스캔 기반 퀀트 트레이딩 시스템.

**3가지 전략:**
- **방패 A** (S&P500 평균회귀): RSI < 35, Close < MA20, Close > MA200 → ATR 트레일링 스톱, TP1(RSI≥50 → 50% 매도), TP2(MA20 도달 → 전량)
- **창 B** (NDX100 모멘텀): 6개월 수익률 상위 25% + 3개월 QQQ 아웃퍼폼 + Close > MA20/MA200 (BULL만) → ATR 트레일링 스톱(×4), MA50 이탈 청산
- **지수 C** (VIX 공황): VIX > 30 시 SPY/QQQ 직접 매수 → VIX < 20 복귀 시 전량 청산

**3중 거시 필터:**
- QQQ > MA200 (상승장/하락장 판단)
- VIX 구간 (20/25/30 기준으로 전략 허용·차단)
- HYG > MA50 (신용 시장 건전성)

## 핵심 파일

### 메인 스크립트
| 파일 | 역할 |
|------|------|
| `scanner_v4.py` | 메인 스캐너 — Phase1(유니버스 스캔) + Phase2(포트폴리오 모니터), Discord 웹훅 알림 |
| `execution_layer.py` | Alpaca Paper API 주문 실행 — scanner_v4.py 실행 후 연속 실행, 킬스위치/일일손실한도/포지션캡 안전장치 포함 |
| `bot.py` | Discord Trade Bot — `!buy`, `!sell`, `!port`, `!summary`, `!cash`, `!stop` 명령어로 수동 매매 기록 |
| `risk_briefing.py` | 위험 등급 브리핑 — signals.json + 수익률곡선으로 6개 지표 대시보드 채점 후 Discord 발송 |

### 모듈
| 파일 | 역할 |
|------|------|
| `config.py` | 전역 상수 (전략 파라미터, VIX 구간, 백테스트 근거 수치) |
| `universe.py` | S&P500 / NDX100 티커 조회 (Wikipedia 스크랩) |
| `indicators.py` | RSI / MA / ATR / ADX 계산, `build_stock_data` 헬퍼 |
| `candidate_finders.py` | 전략 A/B/D 진입 후보 탐색 |
| `news_filter.py` | 어닝스 캘린더 필터 (±N일 차단) + 뉴스 감성 필터 (Gemini PASS/REDUCE/SKIP) |
| `portfolio_io.py` | portfolio.json 로드/저장 |
| `notify.py` | Discord 웹훅 + ANSI 색 제거 |
| `toss_client.py` | 토스증권 Open API 조회 클라이언트 (`TossClient` — OAuth2 토큰 + 계좌/보유종목/예수금/현재가 조회, 읽기 전용) |
| `account_status.py` | 토스 계좌 현황 CLI — 잔고+보유종목+예수금. `python account_status.py [--raw] [--account SEQ]` |
| `prices.py` | 토스 현재가 조회 CLI — `python prices.py 005930 AAPL TSLA [--raw]` (계좌 불필요) |
| `order_preflight.py` | 주문 직전 점검 CLI (DRY-RUN, 주문 없음) — `python order_preflight.py SYMBOL --side buy/sell --qty N`. 종목·호가·상하한가·유의사항·장운영·수수료·환율 + 매수가능금액/판매가능수량으로 GO/NO-GO 판정 |
| `toss_sync.py` | 토스 실계좌 → `portfolio.json` 동기화 — `python toss_sync.py [--apply] [--discord]`. 기본 dry-run, `--apply` 시 `.bak` 백업 후 갱신. 스캐너 상태(strategy/trailing_stop/tp1) 보존하며 shares·평단만 교정, 신규=Core(재태깅 권장), cash=USD 예수금. 국내 등 외부 보유는 `external_holdings.json`(별도 파일 — 스캐너 save가 안 건드림)으로 분리 |
| `toss_execution.py` | 토스 자동매매 실행기 (Phase 1 드라이런) — signals.json+실계좌 대조, 안전장치(킬스위치/포지션캡/가격sanity/장운영/Core매도금지) 후 의도 주문을 로그/디스코드. 실주문 미구현(--live 거부) |
| `forward_test.py` | 페이퍼 forward 검증 — 토스 실시간 시세+스캐너 신호로 가상 포트폴리오 운용, 일별 평가액 + **QQQ 단순보유 벤치마크** 누적(`forward_equity.json`의 `qqq`/`edge_vs_qqq`). "전략 vs 시장" 비교 = 엣지 진짜 여부 판단. `python forward_test.py [--cash N]` |
| `toss_order.py` | **실주문 CLI (Phase 2, 실제 돈)** — `python toss_order.py SYMBOL BUY/SELL QTY [--type LIMIT --price P] [--yes]`. 기본 드라이 프리뷰, **`--yes`로만 실제 전송**, 킬스위치·장운영 가드레일. `TossClient.create_order`(POST /api/v1/orders) 사용. 미국주식은 미국장 개장 필요. 소수점은 `--amount`(orderAmount) 사용 |
| `daily_report.py` | 일일 기술적 요약 → 디스코드 (**LLM·비용 0**, 로컬 파일 기반). signals/portfolio/forward_equity → 레짐+보유손익(토스 시세 best-effort)+액션+forward vs QQQ. `[--no-send] [--no-prices]`. launchd(run_forward.sh)가 매일 자동 전송 |

### 데이터
| 파일 | 역할 |
|------|------|
| `portfolio.json` | 현재 포지션 상태 (production 데이터 — 직접 수정 주의, .gitignore) |
| `external_holdings.json` | 국내 등 외부 보유 (toss_sync가 분리 관리, .gitignore) |
| `signals.json` | 최근 스캔 결과 (.gitignore) |
| `forward_equity.json` | forward 페이퍼 일별 성과 + QQQ 벤치마크 (.gitignore) |
| `forward_paper.json` | forward 페이퍼 포트폴리오 상태 (.gitignore) |
| `toss_execution_history.json` | toss_execution 드라이런 의도 이력 (.gitignore) |
| `trades.json` | 봇 매매 기록 (.gitignore) |
| `backtest/` | 백테스트 스크립트. 풀시스템 `backtest_v4.py` + 검증 스위트(robustness/validation/improve/walkforward/per_strategy/voltarget/regime_routing/dca_vs_dip/pead) — 결과·해석은 **`BACKTESTS.md`**. `archive/`는 dead code |

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# Phase 1+2 스캔 (수동)
python scanner_v4.py

# Alpaca 주문 실행 (스캔 직후)
python execution_layer.py

# 위험 브리핑 (스캔 직후, signals.json 평가)
python risk_briefing.py            # Discord 발송
python risk_briefing.py --dry-run  # 발송 없이 미리보기

# 긴급 킬스위치
touch kill_switch.flag    # 모든 자동 주문 즉시 중단
rm kill_switch.flag       # 해제

# Discord 봇 (수동 포트폴리오 기록용)
python bot.py
```

### 토스증권 운영 (현재 주력)

```bash
python account_status.py                       # 계좌 현황 (잔고/보유/예수금)
python toss_sync.py --apply                    # 실계좌 → portfolio.json 동기화
python forward_test.py                         # forward 페이퍼 검증 1일치 (launchd 자동)
python daily_report.py                         # 일일 요약 디스코드 전송 (launchd 자동)
python order_preflight.py TSLA --side sell --qty 1   # 주문 직전 점검 (DRY)
python toss_order.py PPL SELL 12               # 실주문 드라이 프리뷰
python toss_order.py PPL SELL 12 --yes         # ⚠️ 실제 주문 (실제 돈)
python toss_order.py QQQM BUY --amount 850 --yes     # 금액기반(소수점) 실매수
```

## 환경 변수 (.env)

```
DISCORD_WEBHOOK_URL=...   # scanner_v4.py — Discord 알림
DISCORD_TOKEN=...         # bot.py — Discord 봇 토큰
GEMINI_API_KEY=...        # 뉴스 감성 필터 (gemini-2.5-flash)
APCA_API_KEY_ID=...       # execution_layer.py — Alpaca Paper API
APCA_API_SECRET_KEY=...
APCA_API_BASE_URL=https://paper-api.alpaca.markets
TOSS_CLIENT_ID=...        # toss_client.py — 토스증권 Open API (계좌 조회)
TOSS_CLIENT_SECRET=...
```

## 토스증권 Open API (Alpaca→토스 전환)

- **Base URL**: `https://openapi.tossinvest.com` / OAuth2 Client Credentials (`POST /oauth2/token`)
- **계좌 목록**: `GET /api/v1/accounts` → `accountSeq` 확보
- **보유종목**: `GET /api/v1/holdings` (국내+미국 통합, `X-Tossinvest-Account: {accountSeq}` 헤더)
- **예수금**: `GET /api/v1/buying-power` (`X-Tossinvest-Account` 헤더 + `currency`=KRW/USD, 통화별 호출)
- **현재가**: `GET /api/v1/prices` (`symbols` 콤마구분 최대 200개, 계좌 헤더 불필요)
- **주문 생성**: `POST /api/v1/orders` (`X-Tossinvest-Account` 헤더, JSON 바디 `{symbol, side, orderType, quantity|orderAmount, price?}`). **소수점 수량은 quantity 불가 → orderAmount(금액) 사용.** 미국주식은 미국장 개장 필요
- **`TossClient` 메서드 (구현·검증 완료)**: 조회 `get_accounts` · `get_holdings` · `get_buying_power` · `get_prices` · `get_orderbook` · `get_price_limits` · `get_exchange_rate` · `get_sellable_quantity` · `get_commissions` · `get_stocks` · `get_stock_warnings` · `get_market_calendar` · `get_candles` / 주문 `create_order` (+`build_order_body` 순수)
- 키 발급 시 주의: ① OAuth2 클라이언트 활성화 ② **IP allowlist 등록 필수**(미등록 시 403) ③ 동적 IP면 변경 시 재등록 ④ 주문은 별도 권한 — 400 `invalid-request`면 정상 권한, 403이면 권한 없음
- 공식 문서: developers.tossinvest.com / 스펙: openapi.tossinvest.com/openapi-docs/latest/openapi.json

**진행 상태**:
- ✅ 1) 계좌·예수금·시세 조회  ✅ 2) 전체 조회 API + 주문 직전 점검(`order_preflight`)  ✅ 3) **주문 실행**(`create_order`/`toss_order.py`) — **첫 실거래 완료** (PPL 매도, QQQM 매수)
- 🔶 **현재 단계: forward 페이퍼 검증 중** (`forward_test.py`, launchd 자동). 8주 후 `edge_vs_qqq`로 레버리지/실거래 확대 판단 — 기준은 `docs/toss-auto-trading-golive.md`
- ⬜ 미구현: `/orders/{id}/cancel`·`/modify`, `GET /orders`(목록) — 필요 시 추가. `toss_execution.py`(드라이런 자동매매)의 `--live`도 아직 비활성

## 주요 CONFIG (`config.py`)

| 변수 | 값 | 설명 |
|------|----|------|
| `A_RSI_BUY` | 35 | 방패 A 매수 RSI 기준 |
| `B_RANK_TOP` | 0.25 | 창 B 모멘텀 상위 25% 컷오프 |
| `VIX_DANGER_LOW` | 25.0 | 이상 → A·B 신규 차단 |
| `VIX_PANIC` | 30.0 | 이상 → A 재개, 전략 C 진입 |
| `EARNINGS_BLOCK_DAYS` | 2 | 어닝스 ±N일 이내 후보 차단 |
| `NEWS_FILTER_TOP_N` | 5 | 전략별 상위 N개 뉴스 분석 |

## 로그

- `scanner_v4.log` — 스캐너 실행 이력

## launchd 설정 (선택)

**등록됨**: `com.quantscanner.forward` (`~/Library/LaunchAgents/com.quantscanner.forward.plist`)
— KST 화~토 07:30(미국장 마감 후) `run_forward.sh` 실행 → `scanner_v4.py` → `forward_test.py` → `daily_report.py`(디스코드 일일 요약).
로그: `forward_cron.log`. 맥이 깨어있을 때 실행(절전 중이면 깨어날 때 보충).

```bash
launchctl list | grep quantscanner                 # 등록 확인
launchctl unload ~/Library/LaunchAgents/com.quantscanner.forward.plist   # 중단
launchctl load -w ~/Library/LaunchAgents/com.quantscanner.forward.plist  # 재개
```

> `run_forward.sh`, `forward_*.log/out/err`는 머신 종속이라 .gitignore.
