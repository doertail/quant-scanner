"""
portfolio_io.py — portfolio.json 로드/저장
"""

import json
import logging

from config import PORTFOLIO_FILE

_log = logging.getLogger(__name__)


def load_portfolio() -> tuple[dict, float]:
    try:
        with open(PORTFOLIO_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('holdings', {}), float(data.get('cash', 0))
    except FileNotFoundError:
        _log.warning("portfolio.json 없음")
        return {}, 0.0
    except Exception as e:
        _log.error(f"포트폴리오 로드 실패: {e}")
        return {}, 0.0


def save_portfolio(holdings: dict, cash: float) -> None:
    try:
        with open(PORTFOLIO_FILE, 'w', encoding='utf-8') as f:
            json.dump({'holdings': holdings, 'cash': cash}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log.error(f"포트폴리오 저장 실패: {e}")
