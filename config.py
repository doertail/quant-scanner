"""
config.py — 스캐너 v4 전역 상수

VIX 구간, 전략 A/B/C/D 파라미터, 거시 필터 임계값 등 모든 매직 넘버를 한곳에.
백테스트 근거가 있는 값은 주석으로 명시.
"""

from pathlib import Path

# ─── 경로 ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PORTFOLIO_FILE = BASE_DIR / 'portfolio.json'

# ─── 데이터/지표 ─────────────────────────────────────────────────────────────
LOOKBACK_DAYS  = 420          # 캘린더일 (MA200 충분히 확보)
RSI_PERIOD     = 14
ATR_PERIOD     = 14

# HYG 크레딧 필터
HYG_MA_PERIOD  = 50           # HYG MA 기간 (신용 조건 판단)

# 거시 필터
QQQ_MA_PERIOD  = 200

# VIX 구간 필터 (백테스트 근거)
#   VIX < 20       : 정상 — A·B 모두 허용
#   VIX 20–25      : 스위트스팟 — A 허용(최우수 구간), B 허용
#   VIX 25–30      : 위험 구간 — A 차단(최악 구간), B 차단
#   VIX > 30       : 공황 이후 — A 허용(반등 매수), B 차단
VIX_SWEET_LOW  = 20.0
VIX_DANGER_LOW = 25.0
VIX_PANIC      = 30.0

# QQQM DCA
DCA_BULL       = 20.0
DCA_BEAR       = 100.0
DCA_SIDEWAYS   = 50.0

# ADX / 시장폭 / VIX-RV (3-레이어 국면 판단)
ADX_PERIOD             = 14
ADX_TREND_THRESHOLD    = 25
ADX_SIDEWAYS_THRESHOLD = 20
BREADTH_BULL           = 60.0
BREADTH_BEAR           = 40.0
VIX_RV_HIGH            = 1.2
VIX_RV_LOW             = 0.8

# 전략 A — S&P 500 방패 (평균회귀)
A_RSI_BUY      = 35           # v3부터 35로 사용 (백테스트는 40)
A_RSI_PARTIAL  = 50           # TP1: RSI 50 도달 → 타이트 스톱 전환
A_ATR_MULT     = 3.0
A_ATR_TIGHT    = 1.5          # TP1 이후 타이트 스톱 배수

# 전략 B — NDX100 모멘텀 (6개월 수익률 랭킹 + QQQ 상대강도)
# 신 방식: 6개월 상위 25% + 3개월 QQQ 아웃퍼폼 → 백테스트 CAGR +33.7%
B_MOM_LONG     = 126
B_MOM_SHORT    = 63
B_RANK_TOP     = 0.25
B_ATR_MULT     = 4.0          # 3→4 상향 (backtest_improve 검증: CAGR↑·MDD↓·Sharpe↑. 모멘텀 승자 whipsaw 감소)
B_MA_EXIT_PD   = 50

# 전략 C — VIX 패닉 지수 매수 (백테스트: SPY 96.4% 승률, 평균 +11.5%, Sharpe 1.21)
C_TICKERS      = ['SPY', 'QQQ']
C_POSITION_PCT = 20.0
VIX_C_EXIT     = 20.0

# 포지션 사이징
A_POSITION_PCT = 10.0
B_POSITION_PCT = 10.0
D_POSITION_PCT = 10.0

# 전략 D — 크립토 관련주 모멘텀 (ETH>MA50 레짐, CAGR +17.62%, Sharpe 0.813)
D_TICKERS    = ['MSTR', 'BLOK', 'MARA', 'RIOT', 'COIN', 'BITO']
D_MOM_LONG   = 126
D_MOM_SHORT  = 63
D_RANK_TOP   = 0.50
D_ATR_MULT   = 3.0
D_MA_EXIT_PD = 50

# 어닝스 캘린더 필터 (발표 ±N일 이내 신호 차단)
EARNINGS_FILTER_ENABLE = True
EARNINGS_BLOCK_DAYS    = 2

# 뉴스 감성 필터
NEWS_FILTER_ENABLE = False    # Gemini 미사용(키 무효) — 켜려면 유효한 GEMINI_API_KEY + True
NEWS_FILTER_TOP_N  = 5        # 전략별 상위 N개만 분석 (최대 10 Gemini 호출)
