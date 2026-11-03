"""Rate-limited Discord alert when a scheduled investment monitor fails."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from notify import send_discord

STATE = Path(__file__).resolve().parent / "monitor_health_state.json"


def report_failure(name: str, detail: str = "") -> bool:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        state = {}
    now = datetime.now(timezone.utc)
    previous = state.get(name)
    try:
        if previous and now - datetime.fromisoformat(previous) < timedelta(hours=24):
            return True
    except ValueError:
        pass
    suffix = f" ({detail})" if detail else ""
    message = (f"⚠️ **투자 감시 중단 · {name}**{suffix}\n"
               "이번 실행의 새 뉴스·공시를 확인하지 못함. 알림이 없다는 것을 기회나 위험이 없다는 뜻으로 해석하지 마세요.")
    if not send_discord(message, env_var="DISCORD_NEWS_WEBHOOK_URL", username="투자 감시 상태"):
        return False
    state[name] = now.isoformat()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
