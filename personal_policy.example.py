"""개인 보유 역할 템플릿 — `cp personal_policy.example.py personal_policy.py` 후 수정.
실제 파일은 .gitignore (공개 레포에 보유·금액을 올리지 않기 위함). 주문 권한 없음."""

DAILY_QQQM_USD = 20.0  # 기존 정기 적립액 (이 레포가 주문하지 않음)
PANIC_REVIEW_VIX = 30.0  # 이 이상이면 추가 매수 검토 알림만

# 전략 태그가 바뀌어도 자동 매도하지 않을 보유
PROTECTED_HOLDINGS = frozenset({"QQQM"})

ROLES = {
    "QQQM": "장기 핵심 적립",
}
