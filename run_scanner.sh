#!/bin/bash
# run_scanner.sh — 23:00 스캔 래퍼 (launchd com.jihun.market-scanner가 호출)
# 출력은 launchd의 StandardOutPath(scanner_v4.log)로 간다 — 여기서 리다이렉트하면
# 같은 파일에 writer가 둘 붙어 오프셋이 어긋난다.
set -o pipefail
cd /Users/jihun/workspace/quant-scanner || exit 1

# cron/launchd 실행 시 HOME이 없어 yfinance SQLite 캐시가 실패하는 것을 방지
export HOME=/Users/jihun
PY=/Users/jihun/workspace/quant-scanner/venv/bin/python

# 스캔 전에 portfolio.json을 토스 실계좌로 맞춘다. 안 하면 07:30 이후에 손으로 사고판 게
# 반영 안 된 채로 트레일링 스톱이 갱신되고, 그 상태가 다음 날 아침까지 남는다.
# 실패해도 스캔은 계속 — 낡은 portfolio.json이라도 도는 게 아예 안 도는 것보다 낫다.
# 실패 자체는 toss_sync_state.json 날짜가 안 올라가서 다음 리포트 헤더에 뜬다.
"$PY" toss_sync.py --apply || echo "⚠️ toss_sync 실패 — 이전 portfolio.json으로 진행"
"$PY" -u scanner_v4.py
