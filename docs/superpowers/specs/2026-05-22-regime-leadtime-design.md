# Regime Lead-Time Backtest — 설계 문서

**날짜**: 2026-05-22
**대상**: `backtest/backtest_regime_leadtime.py` + `backtest/regime_leadtime_metrics.py` 신규 + `BACKTESTS.md` 섹션 10

## 1. 목적 / 질문

"강세장이 언제 끝나는지 알 수 있는 지표가 있나?"에 대한 정직한 검증.

**질문**: 기존 3-레이어 거시 레짐 분류기(BULL / SIDEWAYS / BEAR)가 실제 시장
하락(드로다운)을 *미리* 경고하는가? 경고한다면 며칠 선행하는가(lead time)? 그리고
헛경보는 얼마나 자주 내는가?

핵심: 천장을 맞히는 지표는 없다. 이 백테스트는 "맞히나"가 아니라 "분류기가
하락 전에 신호를 주나, 거짓경보율은 얼마나 높나"를 측정한다.

## 2. 아키텍처

프로젝트의 모든 백테스트와 동일한 자급식 패턴.

- `backtest/regime_leadtime_metrics.py` — 순수 의존성 없는 함수 `find_drawdown_events`.
  드로다운 탐지는 비자명한 로직이라 단위 테스트 대상.
- `backtest/test_regime_leadtime_metrics.py` — plain-assert 테스트.
- `backtest/backtest_regime_leadtime.py` — 메인. 3-레이어 레짐 파이프라인을 자체
  보유(기존 `backtest_macro_regime_ablation.py`와 동일 로직·임계값을 복사 —
  프로젝트가 백테스트를 자급식으로 유지하는 관례를 따름). yfinance 일봉.
- 실행: `python backtest/backtest_regime_leadtime.py`
- 출력: stdout + `backtest/results_regime_leadtime.csv` (`.gitignore`의
  `backtest/results_*.csv`로 커버됨)
- 문서: `BACKTESTS.md` 섹션 10

## 3. 데이터 / 상수

- 기간: 2017-06-01 ~ 현재 (MA200/ADX 워밍업 후 2018-01-01부터 분류).
- 레짐 유니버스: `backtest_macro_regime_ablation.py`와 동일 — QQQ + 50종목 시장폭
  표본 + ^VIX + ^GSPC. 임계값(ADX 25/20, breadth 60/40, VIX-RV 0.8/1.2)도 동일.
- 드로다운 측정 대상: QQQ, SPY.
- `DRAWDOWN_THRESHOLD = 0.10` — 고점 대비 −10% 이상을 드로다운 이벤트로.
- `LOOKBACK = 90` 거래일 — 드로다운 고점 이전, 경고 신호를 탐색하는 창.
- `FP_WINDOW = 63` 거래일 — non-BULL 플립이 "확인"되는 후행 창(약 3개월).

## 4. 측정 로직

### 4.1 일별 레짐 시계열
`backtest_macro_regime_ablation.py`의 `vote_regime_row` + `compute_adx_series` +
breadth/VIX-RV 조립 파이프라인을 그대로 사용해 매 거래일 BULL/SIDEWAYS/BEAR 산출.

### 4.2 드로다운 이벤트 (`find_drawdown_events`, 순수 함수)
- 입력: 종가 리스트, threshold.
- 러닝 고점을 추적. 고점 대비 threshold 이상 하락했다가 그 고점을 회복하기
  전까지를 한 에피소드로. 에피소드 = `{peak_idx, cross_idx, trough_idx, depth}`:
  - `peak_idx` — 직전 고점.
  - `cross_idx` — 고점 대비 −threshold를 처음 돌파한 날.
  - `trough_idx` — 회복(또는 데이터 끝) 전 최저점.
  - `depth` — (peak − trough)/peak, 양수 분수.
- 에피소드는 겹치지 않음(고점→회복 단위). 데이터 끝에서 회복 전 종료 시에도
  depth ≥ threshold면 기록.

### 4.3 선행성 (lead time)
각 드로다운 이벤트에 대해, 창 `[peak_idx − LOOKBACK, trough_idx]`에서:
- **BULL 이탈 신호**: 레짐이 BULL이 아닌(SIDEWAYS/BEAR) 첫 날 → `left_bull_idx`.
- **BEAR 전환 신호**: 레짐이 BEAR인 첫 날 → `bear_idx`.
- 신호가 창 안에 없으면 "miss"(경고 없음).
- 리드타임(거래일):
  - vs 고점 = `peak_idx − 신호_idx` (양수 = 고점 전 경고).
  - vs −10% 돌파 = `cross_idx − 신호_idx`.

### 4.4 거짓경보율 (false-positive)
전체 레짐 시계열에서 BULL → non-BULL 전이가 일어난 모든 날을 "플립"으로 수집.
각 플립에 대해, 이후 `FP_WINDOW` 거래일 안에 해당 지수(QQQ)가 플립일 종가 대비
−10% 이상 하락하면 "확인된 경보", 아니면 "거짓경보".
- 보고: 플립 수, 확인 수, 거짓경보율. BULL → BEAR 전이만 따로도 동일 계산.
- 의미: 항상 BEAR를 외치는 분류기는 모든 하락을 "선행"하지만 거짓경보율로 들통남.

## 5. 출력 포맷

```
=== Regime Lead-Time Backtest ===
config: 기간 2018~현재 | 드로다운 −10% | LOOKBACK 90거래일 | FP창 63거래일

[드로다운 이벤트]  (QQQ / SPY)
  Index | Peak date | Depth | ≤−20%? | BULL이탈 리드(고점/−10%) | BEAR전환 리드(고점/−10%)
  ...

[선행성 요약]
  BULL 이탈: 고점 전 경고 N/M건, 중앙 리드타임 X거래일 (vs 고점), Y (vs −10%)
  BEAR 전환: 동일 형식

[거짓경보율]
  BULL→non-BULL 플립: 총 N건, 확인 K건, 거짓경보율 (N−K)/N
  BULL→BEAR 플립: 동일

[해석]  정직한 서술 — 선행하는지, 거짓경보가 신호를 무력화하는지.

CSV 저장: backtest/results_regime_leadtime.csv
```

CSV: 드로다운 이벤트별 행(index, peak/cross/trough date, depth, 각 신호 리드타임).

## 6. 캐비엇 (BACKTESTS.md 섹션 10에 명시)

- **심각한 약세장 표본 극소** — 8년에 −20%는 2건(2020 코로나, 2022). −10% 조정을
  포함해도 한 자릿수. 리드타임 중앙값은 사례 통계지 추정량이 아니다.
- **In-sample 편향** — 레짐 임계값이 바로 이 2018~2025 기간에 맞춰 calibrate됐다.
  따라서 측정된 리드타임은 낙관적으로 편향됐다. 진짜 out-of-sample 검증 아님.
- **기계적 요소** — 레짐 투표가 QQQ vs MA200을 부분적으로 쓴다. 고점 부근에서
  가격이 MA200 근처로 내려오면 레짐이 non-BULL로 바뀌는 건 추세 추종의
  자연스러운 결과 — "선행 예측"이라기보다 후행 추세 확인에 가깝다.
- **거짓경보율이 본질** — 리드타임만 보면 오해. 거짓경보율과 함께 읽어야 한다.
- 창 중첩, 무거래비용 등 공통 캐비엇.

## 7. 범위 밖 (YAGNI)

- 밸류에이션·수익률곡선 등 신규 지표 추가 안 함(별도 프로젝트).
- scanner_v4 라이브 통합 안 함 — 백테스트만.
- 레짐 임계값 재최적화 안 함 — 기존 값 그대로 검증.
- 약세장 정의를 MA200 이탈로 바꾸지 않음 — 고점 대비 −10% 드로다운 기준 고정.
