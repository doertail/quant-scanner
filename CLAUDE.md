# blockchain-rsi-monitor

## 프로젝트 개요

S&P500 + NDX100 전수 스캔 기반 퀀트 트레이딩 시스템.

**3가지 전략:**
- **방패 A** (S&P500 평균회귀): RSI < 35, Close < MA20, Close > MA200 → ATR 트레일링 스톱, TP1(RSI≥50 → 50% 매도), TP2(MA20 도달 → 전량)
- **창 B** (NDX100 모멘텀): RSI > 65, Close > MA20, Close > MA200 → ATR 트레일링 스톱, MA50 이탈 청산
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

### 데이터
| 파일 | 역할 |
|------|------|
| `portfolio.json` | 현재 포지션 상태 (production 데이터 — 직접 수정 주의, .gitignore) |
| `signals.json` | 최근 스캔 결과 (.gitignore) |
| `trades.json` | 봇 매매 기록 (.gitignore) |
| `backtest/` | 백테스트 스크립트 (`archive/`는 dead code) |

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

## 환경 변수 (.env)

```
DISCORD_WEBHOOK_URL=...   # scanner_v4.py — Discord 알림
DISCORD_TOKEN=...         # bot.py — Discord 봇 토큰
GEMINI_API_KEY=...        # 뉴스 감성 필터 (gemini-2.5-flash)
APCA_API_KEY_ID=...       # execution_layer.py — Alpaca Paper API
APCA_API_SECRET_KEY=...
APCA_API_BASE_URL=https://paper-api.alpaca.markets
```

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

평일 장 마감 후 자동 실행이 필요하면 plist를 직접 작성해 등록한다. 현재는 등록된 plist 없음.
