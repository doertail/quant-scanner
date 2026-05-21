# quant-scanner

> **⚠️ 학습/연구용 공개 — 투자 자문이 아닙니다. 본 코드는 어떤 종목 추천도 아니며, 사용으로 인한 모든 결과는 사용자 본인 책임입니다.**

S&P 500 + Nasdaq-100 전수 스캔 기반 멀티 전략 퀀트 시스템. 평균회귀 + 모멘텀 + 변동성 전략을 결합하고, 3중 거시 필터로 진입 시점을 제어합니다.

개인 학습 / 연구 / 백테스트 결과 정리를 목적으로 공개합니다. 실제 운영 환경에서 그대로 사용하는 것을 권장하지 않습니다.

---

## 📊 전략 개요

### 4가지 전략 + 3중 거시 필터

| 전략 | 유니버스 | 핵심 시그널 | 청산 | 백테스트 |
|---|---|---|---|---|
| **방패 A** (평균회귀) | S&P 500 | RSI < 35, Close < MA20, Close > MA200 | ATR 트레일링 / TP1 RSI≥50 50%분할 / TP2 MA20 전량 | — |
| **창 B** (모멘텀) | NDX 100 | 6M 수익률 상위 25% + 3M QQQ 아웃퍼폼 + Close > MA20/MA200 | ATR 트레일링 / MA50 이탈 | CAGR +33.7% |
| **지수 C** (변동성) | SPY / QQQ | VIX > 30 상향 돌파 | VIX < 20 복귀 시 전량 | SPY 승률 96.4%, 평균 +11.5%, Sharpe 1.21 |
| **크립토 D** (모멘텀) | 크립토 관련주 6종 | ETH > MA50 + 6M 상위 50% + 3M ETH 아웃퍼폼 | ATR 트레일링 / MA50 이탈 | CAGR +17.62%, Sharpe 0.813 |

> 백테스트 결과는 과거 데이터 기반이며 미래 성과를 보장하지 않습니다.
> 전체 백테스트 결과 + 재현 방법: **[BACKTESTS.md](./BACKTESTS.md)**

### 핵심 백테스트 수치 (재현 검증)

| 검증 항목 | 결과 |
|---|---|
| **어닝스 필터 효과** (±2일 ablation, 50 large caps × 2y) | 어닝스 ±2일 시그널 5일 평균 −2.08% / 승률 28.6%, 그 외 +0.16% / 51.8% → **차이 +23.2pp 승률** |
| **VIX 25-30 구간 위험성** (30종목 × 10y) | 승률 **19.6%** / 평균 −3.55% — 모든 구간 중 유일하게 손실 |
| **VIX 20-25 sweet spot** | 승률 **55.1%** / 평균 +3.06% — 모든 구간 중 최고 |
| **전략 C SPY VIX panic buy** (16건/11년) | 승률 **96%** / 평균 +11.4% / 보유 환산 +33.1%/yr |
| **전략 C QQQ** | 승률 **98%** / 평균 +13.5% / +43.0%/yr |
| **전략 D BTC>MA50 필터 효과** | CAGR +14.28% (필터 없음 +11.50%), MDD −15.68% (BTC B&H −81.40%) |

### 3중 거시 필터

- **시장 국면**: QQQ vs MA200 + ADX(14) + S&P500 시장폭 → BULL / SIDEWAYS / BEAR 투표
- **VIX 구간**: < 20 정상 / 20-25 스위트스팟 / 25-30 위험(차단) / > 30 공황 (C 진입 신호)
- **HYG 크레딧**: HYG vs MA50 — 신용 시장 건전성 게이트

### 신호 필터링 (Phase 1 후)

1. **어닝스 캘린더 필터** — yfinance calendar로 발표 ±2일 종목 자동 차단 (갭 리스크 회피)
2. **뉴스 감성 필터** — yfinance 뉴스 + Gemini 2.5 Flash로 PASS/REDUCE/SKIP 판정

---

## 🏗️ 구조

```
config.py             # 모든 전역 상수
universe.py           # S&P500 / NDX100 티커 조회 (Wikipedia)
indicators.py         # RSI / MA / ATR / ADX
candidate_finders.py  # 전략별 진입 후보 탐색
news_filter.py        # 어닝스 캘린더 + 뉴스 감성 필터
portfolio_io.py       # portfolio.json I/O
notify.py             # Discord 웹훅
scanner_v4.py         # 메인 스캐너 (Phase 1 스캔 + Phase 2 포트폴리오 모니터)
execution_layer.py    # Alpaca Paper API 자동 주문 (선택)
bot.py                # Discord 봇 (수동 매매 기록용)
backtest/             # 전략별 백테스트 (참고)
```

---

## 🔧 사용 (참고용)

```bash
# 의존성
pip install -r requirements.txt

# 환경 변수
cp .env.example .env
# .env 파일 편집 — GEMINI_API_KEY, DISCORD_WEBHOOK_URL 등

# Phase 1+2 스캔
python scanner_v4.py

# Alpaca 자동 주문 (paper trading)
python execution_layer.py
```

자세한 운영 가이드는 [CLAUDE.md](CLAUDE.md) 참고.

---

## ⚠️ 디스클레이머

- 본 프로젝트는 **개인 학습 및 연구 목적**으로 공개합니다.
- **투자 자문이 아니며**, 어떤 종목 추천도 포함하지 않습니다.
- 백테스트 수치는 과거 데이터 기반이며 미래 수익을 보장하지 않습니다.
- 본 코드를 실제 자금으로 운영해 발생하는 모든 손익은 **사용자 본인 책임**입니다.
- 트레이딩은 원금 손실 가능성이 있는 고위험 활동입니다.
- 실거래 전에 충분한 백테스트, 페이퍼 트레이딩, 그리고 자격을 갖춘 금융 전문가의 자문을 거치시기 바랍니다.

---

## 📝 메모

- 코드 작성 과정 일부에 [Claude](https://claude.com/claude-code) (Anthropic)의 도움을 받았습니다.
- 어닝스 필터 아이디어는 [himself65/finance-skills](https://github.com/himself65/finance-skills) 의 earnings-preview 스킬에서 영감을 받았습니다.

## 📄 라이선스

MIT
