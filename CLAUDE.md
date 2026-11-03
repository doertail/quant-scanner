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
| `notify.py` | Discord 웹훅 + ANSI 색 제거. `send_discord(msg, env_var=...)` — 채널별 웹훅 선택, 성공 여부 반환 |
| `toss_client.py` | 토스증권 Open API 조회 클라이언트 (`TossClient` — OAuth2 토큰 + 계좌/보유종목/예수금/현재가 조회, 읽기 전용) |
| `account_status.py` | 토스 계좌 현황 CLI — 잔고+보유종목+예수금. `python account_status.py [--raw] [--account SEQ]` |
| `prices.py` | 토스 현재가 조회 CLI — `python prices.py 005930 AAPL TSLA [--raw]` (계좌 불필요) |
| `order_preflight.py` | 주문 직전 점검 CLI (DRY-RUN, 주문 없음) — `python order_preflight.py SYMBOL --side buy/sell --qty N`. 종목·호가·상하한가·유의사항·장운영·수수료·환율 + 매수가능금액/판매가능수량으로 GO/NO-GO 판정 |
| `toss_sync.py` | 토스 실계좌 → `portfolio.json` 동기화 — `python toss_sync.py [--apply] [--discord]`. 기본 dry-run, `--apply` 시 `.bak` 백업 후 갱신. 스캐너 상태(strategy/trailing_stop/tp1) 보존하며 shares·평단만 교정, 신규=Core(재태깅 권장), cash=USD 예수금. 국내 등 외부 보유는 `external_holdings.json`(별도 파일 — 스캐너 save가 안 건드림)으로 분리 |
| `toss_order.py` | **실주문 CLI (Phase 2, 실제 돈)** — `python toss_order.py SYMBOL BUY/SELL QTY [--type LIMIT --price P] [--yes]`. 기본 드라이 프리뷰, **`--yes`로만 실제 전송**, 킬스위치·장운영 가드레일. `TossClient.create_order`(POST /api/v1/orders) 사용. 미국주식은 미국장 개장 필요. 소수점은 `--amount`(orderAmount) 사용 |
| `daily_report.py` | 일일 요약 → 디스코드 하루 1통 (**LLM·비용 0**). 폰 가독성 우선: 맨 위 **오늘 할 일**(personal_execution 드라이런 이력 + 보호 안 된 종목 매도신호 + 메가딥 `action_lines`) → VIX·공포 매수까지 거리 → 보유 손익 한 줄 3종목. 비어 있는 섹션은 안 쓰고 코드블록 없음. `[--no-send] [--no-prices]`. launchd(run_forward.sh)가 매일 자동 전송 |
| `news_brief.py` | 보유·관심종목 뉴스 + **한국어 한 문장 요약** → 리포트 섹션 (키 불필요, 비용 0). 대상 = **실보유 자동**(`portfolio.json` 미국 + `external_holdings.json` 국내, 매일 아침 toss_sync가 갱신 — 국내는 토스 `get_stocks`의 market으로 `.KS`/`.KQ` 결정) + `watchlist.json`(보유 안 한 관심종목만). **중요도 = 당일 등락률 절대값**(많이 움직인 종목 먼저, 자리 부족하면 덜 움직인 것부터 잘림). 소스: 미국=Google News RSS / 한국=네이버 모바일 증권 API(회사명 없는 시황기사는 버림). 지수·채권 ETF는 대상 제외. 요약=**로컬 Ollama `config.NEWS_LLM_MODEL`(qwen3:14b)**(외부 전송 없음, 꺼져 있으면 원문 발췌로 폴백). 코드블록 **밖**에 붙여야 링크가 눌린다. `python news_brief.py [--hours N] [--no-llm]` |
| `web_opportunity_alert.py` | 관심종목 밖 기술·보안·반도체·인프라 웹 뉴스(Google News RSS)에서 로컬 LLM이 조사 후보를 고르고, SEC 8-K·XBRL 재무·토스 가격·예수금을 붙여 `DISCORD_NEWS_WEBHOOK_URL`로 알림. 기본 미리보기, `--send`일 때만 전송·중복 상태 저장(`web_opportunity_state.json`). 독립 launchd `com.quantscanner.opportunity-alert`(`run_opportunity_alert.sh`)가 매일 09:00/15:00/21:00 KST 실행. 주문 없음. (옛 `opportunity_alert.py`는 Alpha Vantage 뉴스 유료화로 2026-11 삭제) |
| `thesis_monitor.py` | TSLA 신규 8-K/10-Q/10-K 공시와 EX-99.1 실적 발표문을 SEC에서 감시. 재무 악화 단서·잠정 행동을 디스코드로 알림. 첫 실행은 기준선만 설정. 독립 launchd `com.quantscanner.thesis-monitor` 매일 21:10 KST. 자동 매도 주문은 하지 않음. |
| `personal_execution.py` | **내 전략 자동매매 드라이런**(주문 없음, launchd 07:30 `run_forward.sh`에서 `--no-send`로 돌고 결과는 daily_report '오늘 할 일'로 합쳐짐). 공포 사다리(VIX 30/35/40마다 공포 시작 시 TLT 평가액의 1/3씩 TLT→QQQM, VIX<20 종료·남은 몫 보관 — BACKTESTS §13) · ETF 아닌 종목 USD 계좌 10% 초과분 매도 · `standing_orders.json` 대기 지정가 재주문 · 예수금 적립 10일분 미만 경고. 상태 `panic_state.json`, 이력 `personal_execution_history.json`(모두 .gitignore) |
| `personal_policy.py` | 개인 보유 역할·보호 목록(`PROTECTED_HOLDINGS` = 자동 매도 금지)·QQQM 적립액. **.gitignore**(공개 레포) — 새 클론은 `cp personal_policy.example.py personal_policy.py`. 투자 판단 기록 `docs/personal-investment-system.md` 등도 로컬 전용 |
| `monitor_health.py` | 뉴스·보유 공시 예약 감시 실패 시 #경제 디스코드에 24시간당 한 번까지 상태 경고. |
| ↳ 동반 이동 | 3종목 이상이 같은 방향 2.5%↑ → 개별 뉴스 대신 한 줄로 묶고 **코스피 대비** 표기(섹터만인지 시장 전체인지). 설명 기사는 버렸던 시황 기사에서 찾되 **Ollama가 '이 기사가 그 방향 움직임을 설명하나' 판정**해서 통과한 것만 붙인다. 못 찾으면 억지로 안 붙이고 '못 찾음'이라고 말한다 |
| ↳ 커버리지 | 마지막 `📋` 줄이 **안 보이는 종목의 이유**를 밝힌다(`자리부족` vs `뉴스없음`). 관심종목 전부가 개별뉴스·동반이동·이 줄 중 하나로 설명된다 |
| `add_news_webhook.sh` | `#경제` 웹훅을 `.env`에 등록(.gitignore). **클립보드에서 읽는다** — 웹훅 URL을 채팅·명령줄에 노출하지 않기 위함. 등록 전 실제 전송 테스트 |
| `megadip.py` | **메가캡 딥 전략 모니터 (ABC와 별개 자문)** — 검증: backtest_megacap_dip(QQQ+1%p, 생존편향). 진입 RSI<30 / 청산 RSI>50 또는 120일. 관리 포지션은 `megadip.json`(.gitignore, 예: MSFT). 워치리스트=메가캡 12종. 디스코드 신호, launchd 자동. 실매매는 수동 |

### 데이터
| 파일 | 역할 |
|------|------|
| `portfolio.json` | 현재 포지션 상태 (production 데이터 — 직접 수정 주의, .gitignore) |
| `external_holdings.json` | 국내 등 외부 보유 (toss_sync가 분리 관리, .gitignore). ⚠️ 토스 응답에 국내분이 0건이면 toss_sync가 **덮어쓰지 않는다** — 전량매도로 비우려면 직접 편집 |
| `watchlist.json` | **보유하지 않은** 관심종목 `{"티커": "표시이름"}`(보유는 자동 포함, .gitignore — 예시 `watchlist.example.json`). 뉴스 대상 = 보유 + 이 목록. 토스 API에 관심종목이 없어 수동 관리. **한국 종목은 `.KS` 접미사 필수**(`105560.KS`=KB금융) — yfinance 규칙. 티커만 있는 배열도 받음 |
| `signals.json` | 최근 스캔 결과 (.gitignore) |
| `trades.json` | 봇 매매 기록 (.gitignore) |
| `backtest/` | 백테스트 스크립트. 풀시스템 `backtest_v4.py` + 검증 스위트(robustness/validation/improve/walkforward/per_strategy/voltarget/regime_routing/dca_vs_dip/pead) — 결과·해석은 **`BACKTESTS.md`**. `archive/`는 dead code |

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# Phase 1+2 스캔 (수동)
python scanner_v4.py

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
python personal_execution.py --no-send         # 내 전략 드라이런 (launchd 자동)
python daily_report.py                         # 일일 요약 디스코드 전송 (launchd 자동)
python order_preflight.py TSLA --side sell --qty 1   # 주문 직전 점검 (DRY)
python toss_order.py PPL SELL 12               # 실주문 드라이 프리뷰
python toss_order.py PPL SELL 12 --yes         # ⚠️ 실제 주문 (실제 돈)
python toss_order.py QQQM BUY --amount 850 --yes     # 금액기반(소수점) 실매수
```

## 환경 변수 (.env)

```
DISCORD_WEBHOOK_URL=...   # scanner_v4.py + daily_report.py 리포트 — Discord 알림
DISCORD_NEWS_WEBHOOK_URL=...  # 관심 뉴스 전용(#경제). 있으면 뉴스를 별도 메시지로 분리 발송,
                              # 없으면 리포트 뒤에 붙임(자리 부족해서 2~3건만 들어감).
                              # 등록은 `zsh add_news_webhook.sh` (클립보드에서 읽음)
DISCORD_TOKEN=...         # bot.py — Discord 봇 토큰
GEMINI_API_KEY=...        # 뉴스 감성 필터 (gemini-2.5-flash)
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
- ❌ **관심종목(watchlist) 엔드포인트는 없다** — 2026-09-04 공식 스펙 확인, 총 33개 경로에 없음(`watchlist`/`interests`/`favorites` 계열 전부 404). 관심종목은 앱 전용 기능이라 `watchlist.json`으로 수동 관리한다. 다시 찾지 말 것
- 미사용 엔드포인트(필요하면 여기부터): `/rankings`(인기·등락 순위) · `/trades` · `/stocks/all` · `/market-indicators/*` · `/conditional-orders`(예약주문) · `/stocks/{symbol}/short-selling`·`investor-trading`·`program-trades`

**진행 상태**:
- ✅ 1) 계좌·예수금·시세 조회  ✅ 2) 전체 조회 API + 주문 직전 점검(`order_preflight`)  ✅ 3) **주문 실행**(`create_order`/`toss_order.py`) — **첫 실거래 완료** (PPL 매도, QQQM 매수)
- ❌ 퀀트 A/B forward 페이퍼 검증: 2026-09-08~11-03 8주 −7.1% vs QQQ −5.0% → Go-Live 기준(≥−5%) 미달, **중단·코드 삭제**(forward_test/execution_layer/toss_execution)
- 🔶 **현재 단계: 내 전략 드라이런** (`personal_execution.py`, launchd 자동) 2~4주 확인 후 실주문 전환 검토
- ⬜ 미구현: `/orders/{id}/cancel`·`/modify`, `GET /orders`(목록) — 필요 시 추가

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
— KST 화~토 07:30(미국장 마감 후) `run_forward.sh` 실행 → `toss_sync.py` → `scanner_v4.py` → `personal_execution.py --no-send`(내 전략 드라이런 → 이력) → `daily_report.py`(디스코드 일일 요약 1통).
로그: `forward_cron.log`. 맥이 깨어있을 때 실행(절전 중이면 깨어날 때 보충).

```bash
launchctl list | grep quantscanner                 # 등록 확인
launchctl unload ~/Library/LaunchAgents/com.quantscanner.forward.plist   # 중단
launchctl load -w ~/Library/LaunchAgents/com.quantscanner.forward.plist  # 재개
```

> `run_forward.sh`, `forward_*.log/out/err`는 머신 종속이라 .gitignore.
