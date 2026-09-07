"""signals_io.py — signals.json 신선도 판정 (단일 출처).

스캐너가 죽어도 소비자들은 낡은 signals.json으로 조용히 계속 돌았다
(2026-07-10 ~ 09-05, 83회 무증상). daily_report는 7월 날짜를 단 리포트를
두 달간 발송했고, forward_test는 42일 동안 얼어붙은 신호로 페이퍼 매매를
기록해 "포워드 검증"을 사실상 buy&hold로 바꿔놨다.

날짜 판정을 여기 한 곳에 두고, 대응(경고/매매중단/그냥진행)만 소비자별로 다르게 한다.
"""

import json
from datetime import datetime
from pathlib import Path

# 스캔은 화~토만 돈다 → 화요일 아침엔 토요일치(≈3일)가 정상. 4일부터 낡음.
STALE_DAYS = 4


def age_days(date_str, today: datetime | None = None) -> int | None:
    """signals.json의 date로부터 지난 일수. 파싱 불가면 None."""
    try:
        scanned = datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return ((today or datetime.today()) - scanned).days


def is_stale(date_str, today: datetime | None = None) -> bool:
    """날짜 불명도 낡은 것으로 친다 — 믿을 근거가 없으면 믿지 않는다."""
    days = age_days(date_str, today)
    return days is None or days >= STALE_DAYS


def describe(date_str, today: datetime | None = None) -> str:
    """사람이 읽는 한 줄. 신선하면 빈 문자열."""
    if not is_stale(date_str, today):
        return ""
    days = age_days(date_str, today)
    return "스캔 날짜 불명" if days is None else f"스캔 {days}일 전"
