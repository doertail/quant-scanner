# Risk Briefing Pipeline — 설계 문서

**날짜**: 2026-05-22
**대상**: `risk_briefing.py` + `risk_dashboard.py` 신규, `.gitignore` 수정

## 1. 목적

scanner_v4는 정보를 *수집*해 `signals.json`에 쓰고 Discord로 원시 신호를 *덤프*한다.
빠진 것은 "규율 있는 위험 평가" — 6개 지표를 결정적 규칙으로 채점한 **위험 등급
대시보드**다. 이 파이프라인이 그 빈칸을 채운다.

원칙(대화에서 합의): 예측기가 아니라 **상태 모니터**다. 단일 신호가 아닌 **앙상블**.
on/off가 아닌 **자세(posture) 제안**. 비결정적 AI 서술이 아닌 **결정적 규칙**.

## 2. 아키텍처

- `risk_dashboard.py` (프로젝트 루트) — 순수 채점 함수. 외부 의존성 없음, 단위 테스트.
- `test_risk_dashboard.py` (루트) — plain-assert 테스트, `python3`로 실행.
- `risk_briefing.py` (루트) — 오케스트레이션 스크립트. `scanner_v4.py` 직후 실행.
  `signals.json` + `portfolio.json`을 읽고, yield curve/QQQ를 yfinance로 받고,
  대시보드를 채점·평가하고, 직전 등급과 비교하고, Discord로 발송.
- `briefing_state.json` — 직전 브리핑의 지표 등급 저장 (gitignore에 추가).
- 재사용: `notify.py`(`send_discord`), `portfolio_io.py`(포트폴리오 로드).
- 실행: `python risk_briefing.py [--signals PATH] [--dry-run]`
  - `--dry-run`: Discord 발송 대신 stdout 출력 (검증·미리보기용).
  - `--signals PATH`: signals.json 경로 지정 (기본 `signals.json`).

scanner_v4.py는 수정하지 않는다. risk_briefing은 신규 yfinance 호출(아래 §3)을
자체적으로 한다.

## 3. 수집 데이터

### signals.json에서 (이미 존재)
`regime` 블록: `market_regime`(BULL/SIDEWAYS/BEAR), `breadth_pct`, `vix`,
`vix_zone`(NORMAL/SWEET/DANGER/PANIC), `hyg_ok`, `bull`(QQQ>MA200 bool).

### 신규 yfinance 호출 (risk_briefing이 직접)
- **QQQ** — 200일 MA 이격도 계산용. signals.json엔 `bull` bool만 있고 이격 거리가
  없다. QQQ 종가 + MA200을 직접 계산.
- **수익률곡선** — `^TNX`(10년 국채 수익률 지수)와 `^IRX`(13주 ≈ 3개월). 분류기에
  없던, 침체를 선행하는 지표. spread = TNX − IRX.
  ⚠️ `^TNX`/`^IRX`는 yfinance에서 스케일이 ×10일 수 있다(예: 4.2%가 42로). 구현 시
  실제 값을 출력해 스케일을 확인하고 임계값을 그에 맞춘다. spread의 *부호*는 스케일과
  무관하므로 역전(inverted) 판정은 항상 안전하다.

## 4. 6개 지표 채점

각 지표는 `"🟢"` / `"🟡"` / `"🔴"` / `"⚪"`(데이터 없음) 중 하나.

| 지표 | 소스 | 규칙 |
|---|---|---|
| 레짐 | signals.json `market_regime` | BULL🟢 / SIDEWAYS🟡 / BEAR🔴 |
| 추세 | QQQ vs MA200 | QQQ≤MA200🔴 / 이격>15%🟡(과열) / 그 외🟢 |
| 시장폭 | signals.json `breadth_pct` | ≥60🟢 / 40–60🟡 / <40🔴 / None⚪ |
| 신용 | signals.json `hyg_ok` | true🟢 / false🔴 |
| 변동성 | signals.json `vix_zone` | NORMAL🟢 / SWEET🟡 / DANGER·PANIC🔴 |
| 수익률곡선 | `^TNX` − `^IRX` spread | 역전🔴 / 평탄🟡 / 정상🟢 (임계값 §3 참조) |

소스 데이터가 없으면(키 누락 등) 해당 지표는 ⚪, 메시지에 명시.

## 5. 종합 평가

🔴·🟡 개수로 종합 등급 (⚪는 세지 않음):
- 🔴 0개 & 🟡 ≤1개 → **LOW**
- 🔴 1개 또는 🟡 ≥2개 → **ELEVATED**
- 🔴 ≥2개 → **HIGH**

자세(posture) 한 줄:
- LOW → "보유 유지 — 갈라짐 신호 없음"
- ELEVATED → "주의 — 신규 비중 확대 자제, 지표 추이 관찰"
- HIGH → "위험 — 여러 지표 동시 악화, 단계적 축소 검토"

## 6. "바뀐 것" 비교

`briefing_state.json`에 직전 실행의 `{지표명: 등급}` + 종합 등급을 저장.
이번 실행에서 등급이 바뀐 지표만 추출 → 메시지에 강조 (예: `신용 🟢→🔴`).
state 파일이 없으면(첫 실행) "이전 기록 없음"으로 표시.
이번 실행 종료 시 현재 등급으로 state 파일을 덮어쓴다.

## 7. Discord 출력 포맷

`notify.py`의 `send_discord`로 발송 (`--dry-run`이면 stdout). 간결한 구조:

```
📊 위험 브리핑 — YYYY-MM-DD

종합 위험 등급: <LOW|ELEVATED|HIGH>
자세: <posture 한 줄>

대시보드
  레짐        🟢 BULL
  추세        🟡 QQQ MA200 +16.7% (과열)
  시장폭      🟢 62%
  신용        🟢 HYG > MA50
  변동성      🟢 VIX 16.9 (NORMAL)
  수익률곡선  🟢 +X.XX (정상)

지난 브리핑 대비 변화
  - 신용 🟢→🔴   (또는 "변화 없음" / "이전 기록 없음")

⚠️ 이건 예측이 아니라 상태 모니터다. 임계값은 판단값(미최적화),
   밸류에이션은 MA200 이격도 프록시뿐. 단일 지표가 아니라 앙상블로 읽을 것.
```

## 8. 캐비엇 (메시지 + 코드 주석에 명시)

- **모니터지 예측 아님** — 레짐 백테스트(섹션 10)가 거짓경보율 80%를 증명. 등급은
  현재 상태 보고지 미래 신호가 아니다.
- **임계값은 판단값** — 15% 이격, breadth 60/40, yield 임계값 등은 최적화된 값이
  아니라 합리적 기본값.
- **밸류에이션 미흡** — CAPE·수익률곡선 외 밸류에이션은 MA200 이격도 프록시뿐.
- **앙상블로 읽기** — 단일 지표 하나의 색이 아니라 전체 구도와 "바뀐 것"을 본다.

## 9. 범위 밖 (YAGNI)

- 사용자 수기 메모 입력 — 코드에 그런 입력 경로가 없다. 시스템 데이터만 평가.
- 자동 매매 연동 — 평가·발송만. 주문은 execution_layer.py 소관.
- scheduling(launchd/cron) — 프로젝트에 등록된 스케줄 없음. 수동 실행, plist는 범위 밖.
- Gemini 합성 — scanner_v4가 이미 Gemini 분석을 한다. 브리핑은 결정적 규칙만.
- scanner_v4.py 수정 — 안 함. risk_briefing은 signals.json 소비자.
