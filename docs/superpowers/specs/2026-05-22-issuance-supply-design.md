# Issuance Supply-Shock Backtest — 설계 문서

**날짜**: 2026-05-22
**대상**: `backtest/backtest_issuance_supply.py` 신규 추가 + `BACKTESTS.md` 섹션 9
**선행**: IPO drift / size-cluster 백테스트의 후속 — 시장 *전체* 신규 공급 관점.

## 1. 목적 / 가설

size-cluster 백테스트는 "유니버스 내 28개 IPO"로만 클러스터를 쟀고, 시장 전체
공급은 일부러 범위 밖으로 뒀다. 이 백테스트가 그 빈칸을 채운다.

**가설(유동성 공급 충격)**: 시장 전체 신규 주식 발행이 많은 해일수록, 그 자본을
빨아들이느라 이후 시장(SPY/QQQ) 수익률이 약한가? 2026년 Anthropic/OpenAI/SpaceX
동시 상장 같은 대규모 공급 시나리오의 참고 base rate를 얻는 것이 목적.

## 2. 데이터 정직성 — 가장 중요한 전제

- yfinance에는 시장 전체 발행액 데이터가 없다. 하드코딩이 불가피하다.
- 분기별(~32개) 수치는 신뢰할 출처 없이 채우면 사실상 날조다. 따라서 **연도별
  공개 집계 근사치(2018~2025, 8개 데이터 포인트)**만 사용한다.
- **N=8은 통계 분석이 불가능하다.** 이 백테스트는 회귀·유의성 검정·신뢰구간을
  제시하지 않는다. **서술적 사례 분석**이다 — "어느 해가 고발행이었고 그 다음
  해 시장은 어땠나"를 보여주는 표일 뿐이다. BACKTESTS.md에 명시한다.
- 발행액 수치는 반올림 근사치다. 특히 `total_issuance_b`(IPO + follow-on)는
  `ipo_proceeds_b`보다 출처 신뢰도가 낮다. 분석은 정밀값이 아니라 **거시 패턴**
  (어느 해가 붐, 어느 해가 붕괴)에 기댄다 — 그 패턴 자체는 견고하게 알려져 있다.

## 3. 아키텍처

프로젝트의 모든 백테스트와 동일한 단일 자급식 스크립트.

- 위치: `backtest/backtest_issuance_supply.py`
- 데이터: yfinance 일봉 (SPY/QQQ) + 하드코딩 연도별 발행액
- 공유 import: `from ipo_drift_metrics import forward_return, summarize`,
  `from ipo_size_metrics import median_split, pearson` (둘 다 순수 모듈, 재사용
  목적). 새 순수 함수는 필요 없다 — 신규 단위 테스트 대상 없음.
- 실행: `python backtest/backtest_issuance_supply.py`
- 출력: stdout config + 표, `backtest/results_issuance_supply.csv` 저장
  (`.gitignore`의 `backtest/results_*.csv`로 이미 커버됨)
- 문서: `BACKTESTS.md` 섹션 9

## 4. 데이터 구조

모듈 상수 `ANNUAL_ISSUANCE`: 8개 연도를
`(year, year_start_date, ipo_proceeds_b, total_issuance_b)` 4-튜플로 하드코딩.

- `year` — 정수 연도 2018~2025.
- `year_start_date` — `"YYYY-01-02"` 형태 근사 연초 거래일(yfinance 첫 거래일로
  스냅됨).
- `ipo_proceeds_b` — 그 해 미국 전통 IPO 조달 총액(달러 10억, 근사).
- `total_issuance_b` — IPO + follow-on/secondary 합산 신규 주식 발행 총액
  (달러 10억, 근사 — 신뢰도 낮음, 캐비엇 표기).

## 5. 측정 로직

### 공통
- forward 구간: `HORIZONS = [126, 252]` 거래일 (≈ 6개월, 1년)
- 각 연도의 `year_start_date`를 진입일로, SPY·QQQ forward 수익률 측정
- 베이스라인: 2018-01-01 ~ 2025-12-31 전체 거래일 SPY/QQQ forward 수익률 평균

### 분석 (모두 서술적 — N=8)
1. **연도별 표**: 각 연도의 `ipo_proceeds_b`, `total_issuance_b`, 그리고 그
   해 진입 시 SPY/QQQ forward 126d·252d 수익률. 발행액 내림차순 정렬.
2. **상·하위 절반 비교**: 8개 연도를 `total_issuance_b` 중앙값으로 HIGH 4 / LOW 4
   분할 → 평균 forward 수익률·베이스라인 대비 diff 비교. `median_split` 재사용.
   ⚠️ 버킷당 4개 — 통계 아님, 사례 비교임을 출력에 명시.
3. **상관계수**: `ipo_proceeds_b`/`total_issuance_b` 각각과 forward 수익률의
   Pearson r. `pearson` 재사용. N=8이라 매우 noisy — 한 줄 참고치로만, "통계적
   의미 없음" 라벨과 함께 출력.

## 6. 출력 포맷

```
=== Issuance Supply-Shock Backtest ===
config: 연도 8개(2018~2025) | HORIZONS=[126,252]
        ⚠️ N=8 — 서술적 사례 분석, 통계 아님

[연도별 발행액 × forward 시장 수익률]  (발행액 내림차순)
  Year | IPO $B | Total $B | SPY 126d | SPY 252d | QQQ 126d | QQQ 252d
  ...

[상·하위 절반 비교]  (total_issuance 중앙값 분할, 버킷당 4개 — 사례 비교)
  Bucket | Horizon | SPY mean | SPY diff | QQQ mean | QQQ diff
  ...

[상관계수]  (Pearson r — N=8, 통계적 의미 없음, 참고용)
  변수            | SPY 126d | SPY 252d | QQQ 126d | QQQ 252d
  ...

[2026 시나리오 요약]
  역대 최고 발행 연도와 그 직후 시장을 나열.
  Anthropic/OpenAI/SpaceX 동시 상장 시나리오를 그 연도들과 대조 — 단,
  N=8 서술적 사례이므로 예측이 아니라 정황 참고임을 명시.

CSV 저장: backtest/results_issuance_supply.csv
```

CSV: 연도별 행(year, ipo_proceeds_b, total_issuance_b, SPY/QQQ forward 수익률).

## 7. 캐비엇 (BACKTESTS.md 섹션 9에 명시)

- **N=8 — 통계 아님.** 회귀·p-value·신뢰구간 없음. 서술적 사례 분석.
- **발행액은 근사 집계치.** `total_issuance_b`는 특히 거칠다. 분석은 정밀값이
  아니라 거시 패턴(붐/붕괴)에 의존.
- **내생성(endogeneity) — 핵심 한계.** 기업은 시장이 뜨거울 때 발행한다. 고발행
  연도가 천장 근처에 몰리는 것은 공급압력 *때문*이 아니라 발행과 고평가가 둘 다
  과열의 산물이기 때문일 수 있다. forward 음의 상관이 나와도 "공급이 시장을
  눌렀다" vs "과열 시장이 평균회귀했다"를 이 백테스트는 **구분할 수 없다.**
- **창 중첩** — 연속 연도의 252d forward 창이 겹쳐 독립 관측이 아니다.
- **2026 시나리오** — Anthropic/OpenAI/SpaceX는 비상장, 백테스트 대상 아님. 요약의
  대조는 정황 참고이지 예측이 아니다.

## 8. 범위 밖 (YAGNI)

- 분기별 데이터 — 신뢰할 출처 없어 사용 안 함.
- 회귀분석/유의성 검정 — N=8이라 무의미.
- shock ratio(직전 평균 대비) 별도 지표 — N=8에서 가치 낮음. 발행액 원본 표와
  내림차순 정렬로 '튄 해'는 직접 보임. 별도 순수 함수 만들지 않음.
- scanner_v4 라이브 전략 통합 안 함.
