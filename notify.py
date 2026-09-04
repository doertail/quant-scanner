"""
notify.py — Discord 웹훅 전송 + ANSI 색 코드 제거 헬퍼
"""

import logging
import os
import re

import requests

_log = logging.getLogger(__name__)


def strip_ansi(text: str) -> str:
    return re.sub(r'\033\[[0-9;]*m', '', text)


def send_discord(message: str, env_var: str = 'DISCORD_WEBHOOK_URL',
                 username: str = 'Scanner v4') -> bool:
    """웹훅으로 전송. 성공 여부를 돌려준다(호출부가 폴백을 고를 수 있게).

    env_var로 채널을 고른다 — 뉴스는 `DISCORD_NEWS_WEBHOOK_URL`(#경제)로 따로 보낸다.
    URL 자체는 절대 로그에 찍지 않는다.
    """
    url = os.getenv(env_var, '')
    if not url:
        _log.warning(f"{env_var} 미설정")
        return False
    try:
        clean  = strip_ansi(message)
        chunks = [clean[i:i + 1900] for i in range(0, len(clean), 1900)]
        for chunk in chunks:
            resp = requests.post(
                url,
                json={'content': chunk, 'username': username},
                timeout=10,
            )
            resp.raise_for_status()
        _log.info(f"Discord 전송 완료 ({len(chunks)}개, {env_var})")
        return True
    except Exception as e:
        _log.error(f"Discord 전송 실패({env_var}): {e}")
        return False
