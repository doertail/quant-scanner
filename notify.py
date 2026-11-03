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


def chunk_lines(text: str, limit: int = 1900) -> list:
    """디스코드 2000자 제한용 분할. 줄 경계에서 자른다 — 글자 수로 자르면 링크가 반토막 난다."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                # 한 줄이 한도보다 길 때만 강제로 자른다
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


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
        chunks = chunk_lines(clean)
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
        _log.error(f"Discord 전송 실패({env_var}): {type(e).__name__}")
        return False
