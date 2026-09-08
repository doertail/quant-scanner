"""
daily_report.py — 일일 기술적 요약 리포트 → 디스코드 (LLM 없음, 비용 0)

scanner_v4의 signals.json + portfolio.json + forward_equity.json(로컬)을 읽어
한눈에 보는 요약을 디스코드로 보낸다. 펀더멘털·서술 없음 — 기술적 전략에 맞춘
"레짐 + 보유신호 + 액션 + forward vs QQQ"만. 보유 손익은 토스 실시간가로 best-effort.

보유·관심종목 뉴스는 news_brief.py가 붙인다(yfinance+네이버, 요약은 로컬 Ollama, 키 불필요).
DISCORD_NEWS_WEBHOOK_URL이 있으면 뉴스는 #경제로 분리 발송된다.

사용: python daily_report.py            # 리포트 생성 + 디스코드 전송
      python daily_report.py --no-send  # 콘솔만 (전송 안 함)
      python daily_report.py --no-news  # 뉴스 섹션 빼고
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import signals_io

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SIGNALS = BASE_DIR / "signals.json"
PORTFOLIO = BASE_DIR / "portfolio.json"
FORWARD = BASE_DIR / "forward_equity.json"
SYNC_STATE = BASE_DIR / "toss_sync_state.json"  # toss_sync.py --apply 마지막 성공 날짜
RESERVE_USD = 1250  # VIX 공포 예비탄 (참고 표시용)
DISCORD_SAFE_LEN = 1900  # notify.send_discord의 청크 크기 — 넘으면 2통으로 쪼개진다
NEWS_WEBHOOK_ENV = "DISCORD_NEWS_WEBHOOK_URL"  # 설정되면 뉴스를 #경제로 분리 발송


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


# 스캐너가 죽어도 리포트는 낡은 signals.json으로 계속 발송된다(2026-07-10~09-05, 83회 무증상).
# 날짜가 밀리면 헤더에 박아서 조용히 못 죽게 한다. 판정 기준은 signals_io 단일 출처.
def stale_warning(date: str) -> str:
    desc = signals_io.describe(date)
    return f" ⚠️ **{desc} — 아래 숫자는 낡았다**" if desc else ""


# 토스 동기화가 밀리면 보유·현금이 실계좌와 다른 채로 리포트가 나간다. 실패 메시지는
# forward_cron.log에만 남아 두 달간 아무도 안 봤다. 낡음 판정은 signals_io 단일 출처.
def sync_warning(sync_date) -> str:
    if not signals_io.is_stale(sync_date):
        return ""
    days = signals_io.age_days(sync_date)
    ago = "기록 없음" if days is None else f"{days}일 전"
    return f"⚠️ 토스 동기화 {ago} — 보유·현금이 실계좌와 다를 수 있다"


def build_report(signals: dict, holdings: dict, fwd_last: dict | None,
                 prices: dict | None = None, megadip_lines: list | None = None,
                 risk_line: str | None = None, sync_line: str | None = None) -> str:
    prices = prices or {}
    r = signals.get("regime", {})
    date = signals.get("date", datetime.today().strftime("%Y-%m-%d"))
    L = [f"📊 **일일 리포트 {date}**{stale_warning(date)}"]

    # 경고는 코드블록 **밖**에 굵게 — 안에 넣으면 회색 벽에 섞여 안 보인다.
    # 다운로드 실패로 유니버스가 쪼그라들어도 후보는 나온다. 낡음 경고와 같은 이유로 박는다.
    cov = signals.get("coverage_pct")
    if isinstance(cov, (int, float)) and cov < 90:
        L.append(f"⚠️ **유니버스 커버리지 {cov:.0f}% — 아래 진입후보는 일부만 본 결과다**")
    if sync_line:
        L.append(f"**{sync_line}**")

    # 1. 액션 — 오늘 손을 움직일 것이 맨 위. 나머지는 참고자료다.
    L += ["", "⚡ **액션**", "```"]
    exits = signals.get("exits", []) or []
    for e in exits:
        L.append(f"🔴 매도  {str(e.get('ticker','?')):<5}  {e.get('signal','')}")
    if not exits:
        L.append("매도 신호 없음")
    ent = signals.get("entries", {}) or {}
    cands = [(c, s) for s in ("A", "B", "C", "D") for c in (ent.get(s) or [])[:3]]
    for c, s in cands[:6]:
        rsi = c.get("rsi")
        rsi_s = f"RSI {rsi:.0f}" if isinstance(rsi, (int, float)) else ""
        L.append(f"🟢 매수  {str(c.get('ticker','?')):<5}  {s}  {rsi_s}")
    if not cands:
        L.append("진입 후보 없음")
    L.append("```")

    # 2. 보유 — 티커/손익 열 맞춰서 세로로 훑게
    cash = signals.get("portfolio_cash")
    cash_s = f" · 현금 ${cash:,.0f}(예비탄 ${RESERVE_USD:,})" if cash is not None else ""
    L += ["", f"💼 **보유 {len(holdings)}종목{cash_s}**", "```"]
    for t, p in holdings.items():
        strat = p.get("strategy", "?")
        px = prices.get(t)
        if px and p.get("buy_price"):
            pnl = (px - p["buy_price"]) / p["buy_price"] * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            L.append(f"{emoji} {t:<5} {pnl:>+7.1f}%   {strat}")
        else:
            sh = p.get("shares")
            sh_s = f"{sh:.2f}주" if isinstance(sh, (int, float)) else f"{sh}주"
            L.append(f"⚪ {t:<5} {sh_s:>8}   {strat}")
    L.append("```")

    # 3. 메가캡 딥 (ABC와 별개, 한 메시지로 통합). [0]은 섹션 헤더 — 블록 밖으로 뺀다.
    if megadip_lines:
        L += ["", f"**{megadip_lines[0].strip()}**", "```"]
        L += [x.strip() for x in megadip_lines[1:]]
        L.append("```")

    # 4. 맥락 — 매일 안 바뀌는 것들은 맨 아래 한 줄씩
    L.append("")
    if fwd_last:
        v, q = fwd_last.get("value"), fwd_last.get("qqq")
        if v is not None and q:
            pct = (v - q) / q * 100
            L.append(f"📈 forward ${v:,.0f} vs QQQ ${q:,.0f} · "
                     f"**{pct:+.1f}%** {'✅앞섬' if pct >= 0 else '🔻뒤짐'}")
    hyg = "정상" if r.get("hyg_ok", True) else "⚠️악화"
    vix = r.get("vix")
    vix_s = f"{vix:.1f}" if isinstance(vix, (int, float)) else "?"
    L.append(f"🌐 {r.get('market_regime','?')} · VIX {vix_s} {r.get('vix_zone','?')} · HYG {hyg} · "
             f"진입 A{'✅' if r.get('allow_entry_a') else '❌'} B{'✅' if r.get('allow_entry_b') else '❌'}")
    if risk_line:
        L.append(risk_line.replace(" | ", " · "))
    return "\n".join(L)


def fetch_prices(tickers):
    """보유 종목 현재가 best-effort (토스). 실패해도 리포트는 나감."""
    out = {}
    try:
        from toss_client import TossClient, TossAPIError
        c = TossClient()
        for t in tickers:
            try:
                res = c.get_prices(t).get("result") or []
                if res:
                    out[t] = float(res[0]["lastPrice"])
            except (TossAPIError, ValueError, KeyError, IndexError):
                pass
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="일일 기술적 리포트 → 디스코드")
    ap.add_argument("--no-send", action="store_true", help="콘솔만 (전송 안 함)")
    ap.add_argument("--no-prices", action="store_true", help="시세 조회 생략(빠름)")
    ap.add_argument("--no-news", action="store_true", help="뉴스 섹션 생략")
    args = ap.parse_args()

    signals = _load(SIGNALS, {})
    if not signals:
        print("signals.json 없음 — scanner_v4.py 먼저 실행", file=sys.stderr)
        return 1
    pf = _load(PORTFOLIO, {})
    holdings = pf.get("holdings", {})
    if pf.get("cash") is not None:          # portfolio.json 현금이 더 최신 (toss_sync 갱신)
        signals = {**signals, "portfolio_cash": pf["cash"]}
    fwd = _load(FORWARD, [])
    fwd_last = fwd[-1] if fwd else None

    prices = {} if args.no_prices else fetch_prices(list(holdings.keys()))

    # 메가캡 딥 섹션 (한 메시지로 통합)
    megadip_lines = None
    try:
        import megadip
        mpos = json.loads(megadip.POSITIONS.read_text(encoding="utf-8")) if megadip.POSITIONS.exists() else {}
        megadip_lines = megadip.section_lines(mpos, megadip.fetch_rsi(megadip.MEGA))
    except Exception as e:
        print(f"(메가캡 섹션 생략: {e})", file=sys.stderr)

    # 위험등급 한 줄 (risk_briefing 재사용, best-effort)
    risk_line = None
    try:
        import risk_briefing as rb
        grades, details = rb.build_dashboard(signals.get("regime", {}),
                                             rb.fetch_trend(), rb.fetch_yield_spread())
        risk_line = (f"🚦 위험 {rb.grade_overall(grades)} | 추세 {details['trend']} "
                     f"| 폭 {details['breadth']} | {details['yield_curve']}")
    except Exception as e:
        print(f"(위험등급 생략: {e})", file=sys.stderr)

    report = build_report(signals, holdings, fwd_last, prices,
                          megadip_lines=megadip_lines, risk_line=risk_line,
                          sync_line=sync_warning(_load(SYNC_STATE, {}).get("date")))

    # 뉴스: 전용 웹훅(#경제)이 있으면 그쪽으로 **따로** 보낸다.
    # 그래야 1900자를 리포트와 나눠 쓰지 않아서 요약까지 붙은 기사를 여러 건 실을 수 있다.
    # 없으면 예전처럼 리포트 뒤에 붙인다(남는 글자수만큼만 — 2통으로 쪼개지면 안 되니까).
    # 뉴스는 코드블록 **밖**이어야 링크가 눌린다.
    news_lines = None
    if not args.no_news:
        separate = bool(os.getenv(NEWS_WEBHOOK_ENV))
        try:
            import news_brief
            news_lines = news_brief.build_section(
                budget=DISCORD_SAFE_LEN - (0 if separate else len(report) + 2))
            if news_lines and not separate:
                report += "\n\n" + "\n".join(news_lines)
                news_lines = None          # 본문에 이미 들어감
        except Exception as e:
            print(f"(뉴스 섹션 생략: {e})", file=sys.stderr)
    print(report)

    if news_lines:
        print("\n" + "\n".join(news_lines))

    if not args.no_send:
        from notify import send_discord
        send_discord(report)
        print("📤 디스코드 전송 완료 (리포트)")
        if news_lines:
            ok = send_discord("\n".join(news_lines), env_var=NEWS_WEBHOOK_ENV,
                              username="관심 뉴스")
            print("📰 뉴스 전송 완료 (#경제)" if ok else "⚠️ 뉴스 전송 실패 — 리포트만 나감")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
