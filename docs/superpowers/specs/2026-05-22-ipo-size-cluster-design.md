# IPO Size & Clustering Backtest — 설계 문서

**날짜**: 2026-05-22
**대상**: `backtest/backtest_ipo_size_cluster.py` 신규 추가 + `BACKTESTS.md` 섹션 8
**선행**: `2026-05-22-ipo-drift-backtest-design.md` (IPO drift 백테스트)의 확장

## 1. 목적 / 가설

IPO drift 백테스트(Part B)는 대형 IPO 이후 시장이 베이스라인보다 약해지지 않음을 보였다.
이 확장은 한 단계 더 들어간다 — **crowding-out 가설**: IPO 규모가 클수록, 또는 대형
IPO가 한 시기에 몰릴수록(clustering), 그 자금을 대느라 다른 주식이 팔려 시장이
약해지는가?

검증 변수 3개:
- **조달액(deal size)** — IPO에서 시장이 흡수해야 한 주식의 달러 가치. crowding-out
  메커니즘과 직결.
- **시가총액(market cap)** — 상장일 기준 회사 가치. "얼마짜리 회사냐"의 헤드라인 숫자.
- **클러스터 강도(cluster intensity)** — 각 이벤트 ±90일 내 유니버스 IPO 조달액 합.

산출물은 규모·밀집도별 시장 forward 수익률의 base rate이다. Anthropic/OpenAI/SpaceX는
비상장이라 백테스트 대상이 아니며, 출력의 요약 단락에서 "역대 최대규모 버킷의 기저율"
참고로만 언급한다.

## 2. 아키텍처

프로젝트의 모든 백테스트와 동일한 단일 자급식 스크립트.

- 위치: `backtest/backtest_ipo_size_cluster.py`
- 데이터: yfinance 일봉 (`auto_adjust=False`)
- 공유 import: `from ipo_drift_metrics import forward_return, summarize` (순수 모듈, 재사용
  목적으로 설계됨). 그 외 로직(유니버스, fetch, 베이스라인)은 자체 보유 — 기존
  `backtest_ipo_drift.py`는 수정하지 않는다.
- 실행: `python backtest/backtest_ipo_size_cluster.py`
- 출력: stdout config 블록 + 분할표 + 요약, `backtest/results_ipo_size_cluster.csv` 저장
  (`.gitignore`의 `backtest/results_*.csv`로 이미 커버됨)
- 문서: `BACKTESTS.md` 섹션 8

## 3. 데이터

자체 유니버스 상수 `IPO_UNIVERSE_SIZED`: 28개 IPO를
`(ticker, ipo_date, ai_related, deal_size_b, mktcap_b)` 5-튜플로 하드코딩.
티커·날짜·ai 플래그는 `backtest_ipo_drift.py`의 `IPO_UNIVERSE`와 동일하게 유지한다.

- `deal_size_b` — IPO 조달액(달러, 10억 단위).
- `mktcap_b` — 상장일 종가 기준 시가총액(달러, 10억 단위).

⚠️ **정직성 이슈 2가지** (BACKTESTS.md 캐비엇에 명시):
1. **근사값** — 모든 규모 수치는 공개 자료 기반 반올림 근사. 분석은 중앙값 2분할이라
   개별 오차가 버킷 소속을 뒤집는 일은 드물지만, 점추정으로 읽으면 안 된다.
2. **직상장** — SPOT·COIN·PLTR·RBLX는 신주 발행이 없어 조달액이 $0이다. 이들의
   `deal_size_b`는 상장 첫날 유통 가능해진 주식의 근사 시장가치(시장이 흡수해야 한
   물량)로 기록하고, 캐비엇에 직상장임을 표기한다.

## 4. 측정 로직

### 공통
- forward 구간: `HORIZONS = [5, 20, 60, 120, 180, 252]` (drift 백테스트와 동일)
- day-0 = yfinance 기준 IPO 종목 첫 거래일
- 베이스라인: 2018-01-01 ~ 2025-12-31 전체 거래일의 SPY/QQQ forward 수익률 평균
  (drift 백테스트와 동일 방식)

### 클러스터 강도
각 IPO 이벤트 e에 대해:
`cluster_intensity(e) = Σ deal_size_b(x)` for all x in universe where
`|ipo_date(x) − ipo_date(e)| ≤ 90 days` (자기 자신 포함).

### 분할 분석
세 변수(`deal_size_b`, `mktcap_b`, `cluster_intensity`) 각각에 대해:
1. 28개 이벤트를 해당 변수의 중앙값 기준 상위(HIGH)·하위(LOW) 두 버킷으로 분할.
2. 각 버킷에서 SPY·QQQ forward 수익률을 6개 horizon에서 `summarize`로 집계.
3. 베이스라인 대비 diff(pp) 계산.
4. 가설 지지 조건: HIGH 버킷의 diff가 LOW 버킷보다 유의하게 낮음(시장이 더 약함).

보조로, 각 변수와 각 horizon forward 수익률 사이의 **피어슨 상관계수**를 출력한다
(서술 통계 — p-value는 보고하지 않음, 창 중첩으로 독립성이 깨지므로).

## 5. 출력 포맷

```
=== IPO Size & Clustering Backtest ===
config: 유니버스 28개 | 변수: deal_size / mktcap / cluster_intensity
        중앙값 분할 | HORIZONS=[5,20,60,120,180,252]

[분할 1] 조달액(deal size) — 중앙값 $X.XB
  버킷  | N  | Horizon별 SPY mean / SPY diff / QQQ mean / QQQ diff ...
  HIGH  | 14 | ...
  LOW   | 14 | ...

[분할 2] 시가총액(market cap) — 중앙값 $X.XB
  (동일 구조)

[분할 3] 클러스터 강도 — 중앙값 $X.XB
  (동일 구조)

[상관계수] 변수 × forward 수익률 (Pearson r, 서술 통계)
  변수            | SPY 5d | SPY 20d | ... | SPY 252d
  deal_size       | ...
  mktcap          | ...
  cluster         | ...

[최대규모 요약]
  역대 최대규모 버킷(상위 N개)의 시장 forward 수익률 기저율: ...
  Anthropic/OpenAI/SpaceX는 시총이 역사상 어떤 유니버스 이벤트보다 크므로
  상위 버킷 기저율이 가장 가까운 참고치 — 단 예측이 아니라 base rate임.

CSV 저장: backtest/results_ipo_size_cluster.csv
```

CSV: 이벤트별 행(ticker, ipo_date, ai, deal_size_b, mktcap_b, cluster_intensity,
각 horizon SPY/QQQ forward 수익률).

## 6. 캐비엇 (BACKTESTS.md 섹션 8에 명시)

- **규모 수치는 근사값** — 공개 자료 기반 반올림. 점추정 금지.
- **직상장 4건**(SPOT/COIN/PLTR/RBLX) 조달액은 첫날 유통가치 근사 — 진짜 조달액 $0.
- **중앙값 2분할 / N=14** — 버킷이 작아 신뢰구간이 넓다. AI×규모 교차 분할은 버킷당
  3개로 무의미하므로 하지 않는다.
- **창 중첩·생존편향** — drift 백테스트와 동일. 이벤트 forward 창이 겹쳐 독립이 아니며
  p-value를 보고하지 않는다. 상장폐지 대형 IPO(WeWork/DIDI)는 누락.
- **Anthropic/OpenAI/SpaceX** — 비상장이라 유니버스에 없고 백테스트되지 않는다. 요약
  단락의 참고치는 base rate이지 종목별 예측이 아니다.

## 7. 범위 밖 (YAGNI)

- Part A(IPO 종목 자체) 규모별 분석은 하지 않음 — crowding-out 가설은 시장(Part B) 가설.
- 시장 전체 IPO 총액(유니버스 밖 소형 IPO 포함) 하드코딩은 하지 않음 — 클러스터는
  유니버스 내 ±90일 합으로만 정의.
- 3분할/회귀분석/유의성 검정 안 함.
- scanner_v4 라이브 전략 통합 안 함.
