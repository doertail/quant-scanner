# IPO Drift Backtest — 설계 문서

**날짜**: 2026-05-22
**대상**: `backtest/backtest_ipo_drift.py` 신규 추가 + `BACKTESTS.md` 섹션 7

## 1. 목적 / 가설

"대형 IPO 이후 주가가 하락한다"는 통설을 이벤트 스터디로 검증한다. 두 갈래로 측정한다.

- **Part A — IPO 종목 자체**: 상장한 회사의 주가가 상장 후 일정 기간 뒤 하락(또는 시장 대비 언더퍼폼)하는가. 락업 해제·장기 언더퍼폼 현상.
- **Part B — 시장 추세 (핵심)**: 대형 IPO 직후 SPY/QQQ 같은 광범위 시장이 약해지는가. 특히 **초대형 AI IPO 이후** 시장 추세에 초점.

상장 예정 종목(Anthropic/OpenAI/SpaceX)은 가격 이력이 없어 백테스트 불가. 이 백테스트의 산출물은 과거 대형 IPO의 **base rate(기저율) 분포**이며, 향후 IPO 기대치 참고용으로만 쓴다 — 스크립트에 워치 기능은 넣지 않는다.

## 2. 아키텍처

기존 백테스트(`backtest_earnings_ablation.py`, `backtest_macro_regime_ablation.py`)와 동일한 패턴의 단일 자급식 스크립트.

- 위치: `backtest/backtest_ipo_drift.py`
- 데이터: yfinance 일봉 (`auto_adjust=False`)
- 외부 의존: numpy, pandas, yfinance (이미 `requirements.txt`에 존재)
- 실행: `python backtest/backtest_ipo_drift.py`
- 출력: stdout에 config 블록 + 결과 표, `backtest/results_ipo_drift.csv` 저장
- 문서: `BACKTESTS.md`에 섹션 7 추가

## 3. 유니버스

`(ticker, ipo_date, ai_related)` 튜플을 모듈 상수로 하드코딩. 약 25개, AI 태그 6개.

| 연도 | 티커 (★ = ai_related) |
|---|---|
| 2018 | SPOT, DBX, DOCU |
| 2019 | UBER, LYFT, PINS, ZM, CRWD, DDOG |
| 2020 | SNOW★, ABNB, DASH, PLTR★, U, AI★ |
| 2021 | COIN, RIVN, HOOD, RBLX, GTLB, AFRM |
| 2023 | ARM★, CART, KVYO, BIRK |
| 2024 | RDDT, ALAB★ |
| 2025 | CRWV★ |

`ipo_date`는 각 종목의 실제 상장일(대략값 허용 — 스크립트는 yfinance에서 받은 첫 거래일을 실제 day-0으로 사용하고, 하드코딩 날짜와 5거래일 이상 어긋나면 경고 출력).

## 4. 측정 로직

### 공통
- forward 구간(거래일): `HORIZONS = [5, 20, 60, 120, 180, 252]` (180 ≈ 락업 해제, 252 ≈ 1년)
- 수익률: `close[t+h] / close[t] - 1`
- 구간 끝이 데이터 범위를 벗어나면(최근 IPO) 해당 horizon은 `None` 처리하고 집계에서 제외 — N이 horizon별로 다를 수 있음을 표에 명시.

### Part A — IPO 종목 자체
1. 각 티커를 yfinance에서 상장일 ~ today 범위로 다운로드.
2. 첫 거래일(day-0) 종가를 진입가로 가정.
3. 각 horizon에서 절대 forward 수익률 계산.
4. **초과수익**: 같은 (day-0 → day-0+h) 창에서 SPY forward 수익률을 차감.
5. 집계: horizon별 평균·중앙값·승률(절대수익 > 0 비율). 그룹 = 전체(25) / AI(6).

### Part B — 시장 추세
1. SPY, QQQ를 2017-01-01 ~ today로 한 번 다운로드.
2. 각 IPO 이벤트의 day-0(= yfinance 기준 IPO 종목 첫 거래일)에 대해 SPY·QQQ의 forward 수익률을 6개 horizon에서 측정.
3. **무조건부 베이스라인**: 2018-01-01 ~ 2025-12-31 *모든 거래일*을 진입일로 본 SPY·QQQ forward 수익률의 평균. 이것이 "랜덤일" 기저율.
4. 비교 지표: `IPO 직후 평균 − 베이스라인 평균` (pp). 음수면 "IPO 이후 시장이 평균보다 약함" → 가설 지지.
5. 그룹 = 전체 IPO 이벤트 / AI IPO 이벤트.

## 5. 출력 포맷

```
=== IPO Drift Backtest ===
config: 유니버스 25개(AI 6개) | HORIZONS=[5,20,60,120,180,252] | 데이터: yfinance 일봉

[Part A] IPO 종목 자체 — day-0 종가 진입
  그룹: 전체 (N=25)
  Horizon |   N | Mean abs | Median abs | Win% | Mean excess vs SPY
  ...
  그룹: AI (N=6)
  ...

[Part B] 시장 추세 — IPO day-0 이후 SPY/QQQ
  그룹: 전체 IPO 이벤트
  Horizon |   N | SPY mean | SPY win% | QQQ mean | QQQ win% | SPY base | QQQ base | SPY diff | QQQ diff
  ...
  그룹: AI IPO 이벤트
  ...

CSV 저장: backtest/results_ipo_drift.csv
```

CSV: 이벤트별 행(ticker, ipo_date, ai_related, part A 각 horizon 절대/초과수익, part B 각 horizon SPY/QQQ 수익).

## 6. 캐비엇 (BACKTESTS.md 섹션 7에 명시)

- **AI 표본 6개** — 신뢰구간 매우 넓음. 방향성 참고만 가능, 단정 불가.
- **이벤트 클러스터링 / 창 중첩** — 2020년 9~12월 등 IPO 밀집 시기의 forward 창이 겹쳐 관측치가 독립이 아님. 따라서 p-value를 보고하지 않고 기술통계 + 기저율 대비 pp 차이만 제시 (기존 백테스트들과 동일한 정직성 수준).
- **생존편향** — 상장폐지된 대형 IPO(WeWork, DIDI 등)가 유니버스에서 누락됨. 살아남은 IPO만 보면 결과가 낙관적으로 치우칠 수 있음.
- **SPAC / 직상장 혼재** — 전통 IPO, 직상장(SPOT/COIN/PLTR), SPAC 합병이 섞임. day-0 정의가 미묘하게 다름.
- **상장일 부정확** — 하드코딩 날짜가 아닌 yfinance 첫 거래일을 day-0으로 사용. 데이터 공백 시 어긋날 수 있음.
- 기존 캐비엇과 동일: 무거래비용·무슬리피지, 과거 ≠ 미래.

## 7. 범위 밖 (YAGNI)

- scanner_v4 라이브 전략 통합 안 함.
- 상장 예정 종목 워치/예측 기능 안 함.
- IPO 클러스터링을 별도 시계열 신호로 모델링하지 않음 (캐비엇 언급만).
- 통계적 유의성 검정(t-test 등) 안 함.
