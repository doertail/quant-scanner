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


def send_discord(message: str) -> None:
    url = os.getenv('DISCORD_WEBHOOK_URL', '')
    if not url:
        _log.warning("DISCORD_WEBHOOK_URL 미설정")
        return
    try:
        clean  = strip_ansi(message)
        chunks = [clean[i:i + 1900] for i in range(0, len(clean), 1900)]
        for chunk in chunks:
            resp = requests.post(
                url,
                json={'content': chunk, 'username': 'Scanner v4'},
                timeout=10,
            )
            resp.raise_for_status()
        _log.info(f"Discord 전송 완료 ({len(chunks)}개)")
    except Exception as e:
        _log.error(f"Discord 전송 실패: {e}")
