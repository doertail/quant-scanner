#!/bin/zsh
# add_news_webhook.sh — #경제 채널 웹훅 URL을 .env에 등록한다.
#
# URL을 채팅이나 명령줄에 붙여넣지 않는다 — 클립보드에서 직접 읽는다.
# (2026-09-03 교훈: 채팅 UI가 `TOKEN=` 뒤 문자열을 ***로 마스킹해서 리터럴 ***가 저장된 적 있음)
#
# 사용법: 디스코드에서 웹훅 URL을 "복사"한 뒤
#   zsh /Users/jihun/workspace/quant-scanner/add_news_webhook.sh

set -e
ENV_FILE=/Users/jihun/workspace/quant-scanner/.env
KEY=DISCORD_NEWS_WEBHOOK_URL

URL="$(pbpaste)"

# ── 검증: 값은 출력하지 않고 형태만 본다 ──────────────────────────────────
if [[ -z "$URL" ]]; then
  echo "❌ 클립보드가 비어 있음. 디스코드에서 웹훅 URL을 복사한 뒤 다시 실행."
  exit 1
fi
if [[ "$URL" != https://discord.com/api/webhooks/* && "$URL" != https://discordapp.com/api/webhooks/* ]]; then
  echo "❌ 디스코드 웹훅 URL 형태가 아님 (https://discord.com/api/webhooks/... 이어야 함)"
  echo "   클립보드 길이: ${#URL}자 — 다른 걸 복사했을 가능성"
  exit 1
fi
echo "✅ 형태 확인: 웹훅 URL, ${#URL}자"

# ── 실제로 보내지는지 먼저 확인 ────────────────────────────────────────────
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL" \
  -H 'Content-Type: application/json' \
  --data '{"content":"👀 관심 뉴스 채널 연결됨 — 이 메시지가 보이면 성공.","username":"관심 뉴스"}')"
if [[ "$CODE" != "204" && "$CODE" != "200" ]]; then
  echo "❌ 전송 실패 (HTTP $CODE). 웹훅이 삭제됐거나 URL이 틀림. .env는 안 건드렸음."
  exit 1
fi
echo "✅ 테스트 메시지 전송됨 — 디스코드 #경제 확인해봐"

# ── .env 갱신: 기존 줄이 있으면 교체, 없으면 추가 (다른 키는 보존) ────────
touch "$ENV_FILE"
cp "$ENV_FILE" "$ENV_FILE.bak"

# ⚠️ 파일이 개행 없이 끝나면 append가 마지막 줄에 이어붙어 그 키를 망가뜨린다.
#    (2026-09-04에 실제로 TOSS_CLIENT_SECRET 줄 끝에 웹훅이 붙어버렸음)
if [[ -s "$ENV_FILE" ]] && [[ "$(tail -c 1 "$ENV_FILE" | xxd -p)" != "0a" ]]; then
  printf '\n' >> "$ENV_FILE"
  echo "ℹ️  .env 끝에 개행이 없어 하나 추가함"
fi

if grep -q "^${KEY}=" "$ENV_FILE"; then
  # URL에 / 가 있으니 sed 구분자는 |. URL은 화면에 안 찍힌다.
  TMP="$(mktemp)"
  grep -v "^${KEY}=" "$ENV_FILE" > "$TMP"
  printf '%s=%s\n' "$KEY" "$URL" >> "$TMP"
  mv "$TMP" "$ENV_FILE"
  echo "✅ .env의 ${KEY} 교체함 (백업: .env.bak)"
else
  printf '%s=%s\n' "$KEY" "$URL" >> "$ENV_FILE"
  echo "✅ .env에 ${KEY} 추가함 (백업: .env.bak)"
fi

chmod 600 "$ENV_FILE"

# ── 검증: 다른 키가 안 망가졌는지 확인 (값은 안 찍는다) ────────────────────
BAD="$(python3 - "$ENV_FILE" <<'PY'
import sys
bad = []
for i, line in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    key = line.split("=", 1)[0]
    if not key.replace("_", "").isalnum() or key != key.upper():
        bad.append(f"{i}행 키 이상: {key[:40]}")
print("; ".join(bad))
PY
)"
if [[ -n "$BAD" ]]; then
  echo "❌ .env가 이상해졌다: $BAD"
  echo "   되돌리려면: cp $ENV_FILE.bak $ENV_FILE"
  exit 1
fi
echo "✅ .env 무결성 확인 (다른 키 정상)"

echo
echo "다음: cd /Users/jihun/workspace/quant-scanner && ./venv/bin/python daily_report.py"
echo "  → 리포트는 기존 채널, 뉴스는 #경제로 따로 간다."
