"""
daily_report.py — 일일 기술적 요약 리포트 → 디스코드 (LLM 없음, 비용 0)

scanner_v4의 signals.json + portfolio.json(로컬)을 읽어
한눈에 보는 요약을 디스코드로 보낸다. 펀더멘털·서술 없음 — 기술적 전략에 맞춘
"오늘 할 일 + VIX + 보유 손익"만, 폰에서 읽히게 코드블록 없이. 보유 손익은 토스 실시간가로 best-effort.

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
from config import A_MAX_SLOTS, B_MAX_SLOTS
from personal_execution import LADDER, today_todo
from personal_policy import DAILY_QQQM_USD, PANIC_REVIEW_VIX, PROTECTED_HOLDINGS

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SIGNALS = BASE_DIR / "signals.json"
MORNING = BASE_DIR / "signals_morning.json"  # 23:00 스캔 직전 run_scanner.sh가 뜬 07:30치
PORTFOLIO = BASE_DIR / "portfolio.json"
SYNC_STATE = BASE_DIR / "toss_sync_state.json"  # toss_sync.py --apply 마지막 성공 날짜
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


# 23:00 스캔은 개장 30분 뒤 인트라데이라 07:30(종가) 스캔과 후보가 갈린다. 백테스트는
# 전부 종가 기준이므로 둘 다 뜬 종목이 원본 신호다. 날짜가 다르면(=아침치가 아니면) 조용히 무시.
def morning_tickers(date: str) -> set:
    m = _load(MORNING, {})
    if m.get("date") != date:
        return set()
    ent = m.get("entries", {}) or {}
    return {c.get("ticker") for s in ("A", "B", "C", "D") for c in (ent.get(s) or [])}


# A/B 점수는 스케일이 달라 비교가 안 된다. 통일해도 평균수익이 높은 B가 항상 이기고,
# 그러면 낙폭 완충재인 A가 사라진다(BACKTESTS.md §10: A 제외 시 Sharpe 0.95→0.56).
# 그래서 비교 대신 정원제 — 슬리브마다 자리를 정해두고 그 안에서만 순위로 고른다.
# Core(지수 DCA)는 슬리브가 아니라 세지 않는다.
def slots_left(holdings: dict) -> dict:
    used = [p.get("strategy") for p in holdings.values()]
    return {"A": max(0, A_MAX_SLOTS - used.count("A")),
            "B": max(0, B_MAX_SLOTS - used.count("B"))}


WEEKDAY = "월화수목금토일"


def build_report(signals: dict, holdings: dict,
                 prices: dict | None = None, megadip_lines: list | None = None,
                 sync_line: str | None = None, confirmed: set | None = None,
                 todo: list | None = None) -> str:
    """폰에서 읽히게: 맨 위 '오늘 할 일', 비어 있는 섹션은 아예 안 쓴다, 코드블록 없음
    (한글·이모지 폭이 달라 정렬이 깨진다). todo = personal_execution 드라이런 결과(None=안 돌았음)."""
    prices = prices or {}
    confirmed = confirmed or set()
    r = signals.get("regime", {})
    date = signals.get("date", datetime.today().strftime("%Y-%m-%d"))
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        head = f"{d.month}/{d.day} ({WEEKDAY[d.weekday()]})"
    except ValueError:
        head = date
    L = [f"📊 **{head}**{stale_warning(date)}"]

    # 다운로드 실패로 유니버스가 쪼그라들어도 후보는 나온다. 낡음 경고와 같은 이유로 박는다.
    cov = signals.get("coverage_pct")
    if isinstance(cov, (int, float)) and cov < 90:
        L.append(f"⚠️ **유니버스 커버리지 {cov:.0f}% — 아래 진입후보는 일부만 본 결과다**")
    if sync_line:
        L.append(f"**{sync_line}**")

    # 1. 오늘 할 일 — 드라이런 의도 + 공포장 판단 + 보호 안 된 종목의 매도 신호 + 메가딥
    do = list(todo or [])
    if todo is None:
        do.append("⚠️ 내 전략 드라이런 결과 없음 — personal_execution 로그 확인")
    vix = r.get("vix")
    if isinstance(vix, (int, float)) and vix > PANIC_REVIEW_VIX:
        if signals_io.is_stale(date):
            do.append("과거 VIX 공포 신호: 새 스캔 전까지 추가 매수 판단 보류")
        else:
            cash_for_review = signals.get("portfolio_cash")
            if isinstance(cash_for_review, (int, float)) and cash_for_review >= 0 and not sync_line:
                do.append(f"공포장: 추가 $100 매수마다 적립 여력 5거래일분 감소. "
                          "추가 매수 여부와 금액은 실제 예수금 확인 후 직접 판단")
            else:
                do.append("공포장: 추가 매수 여부와 금액을 실제 예수금 확인 후 직접 판단")
    # 퀀트 매도 신호는 보호 보유(장기 핵심)에 안 쓴다 — 보호 종목은 아예 안 띄운다.
    for e in signals.get("exits", []) or []:
        if e.get("ticker") not in PROTECTED_HOLDINGS:
            do.append(f"{e.get('ticker', '?')} 매도 신호 — {e.get('signal', '')}")
    do += megadip_lines or []
    L += ["", "✅ **오늘 할 일**", *(f"• {x}" for x in do)] if do else ["", "✅ 할 일 없음"]

    # 2. 시장 — 내 전략에 쓰는 숫자는 VIX 하나
    if isinstance(vix, (int, float)):
        gap = LADDER[0] - vix
        trig = f"공포 매수까지 +{gap:.0f}" if gap > 0 else "공포 매수 구간"
        L += ["", f"📈 **시장** VIX {vix:.1f} · {trig}"]

    # 3. 계좌 — 한 줄에 3종목씩
    cash = signals.get("portfolio_cash")
    cash_s = (f" · 현금 ${cash:,.0f} (적립 {cash / DAILY_QQQM_USD:.0f}일분)"
              if isinstance(cash, (int, float)) else "")
    L += ["", f"💼 **보유 {len(holdings)}종목**{cash_s}"]
    cells = []
    for t, p in holdings.items():
        px, bp = prices.get(t), p.get("buy_price")
        cells.append(f"{t} {(px - bp) / bp * 100:+.1f}%" if px and bp else t)
    L += [" · ".join(cells[i:i + 3]) for i in range(0, len(cells), 3)]

    # 4. 옛 퀀트 A/B 진입 후보 — forward 검증 실패한 전략이라 있을 때만 참고로 한 줄.
    # 순위는 전략 **안에서만**(점수 스케일이 다름), 정원 넘는 순위는 ⛔ (BACKTESTS.md §10).
    ent = signals.get("entries", {}) or {}
    cands = [(c, f"{s}{i+1}") for s in ("A", "B", "C", "D")
             for i, c in enumerate((ent.get(s) or [])[:3])]
    if cands:
        left = slots_left(holdings)
        items = []
        for c, rank in cands[:6]:
            sc = c.get("score")
            sc_s = f" {sc:.2f}" if isinstance(sc, (int, float)) else ""
            cap = left.get(rank[0])
            full = " ⛔" if cap is not None and int(rank[1:]) > cap else ""
            mark = " ✅" if c.get("ticker") in confirmed else ""
            items.append(f"{c.get('ticker', '?')} {rank}{sc_s}{full}{mark}")
        L += ["", f"⚡ 퀀트 후보(참고): {' · '.join(items)}"]
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

    prices = {} if args.no_prices else fetch_prices(list(holdings.keys()))

    # 메가캡 딥 — 할 일(청산·진입 후보)만
    megadip_lines = None
    try:
        import megadip
        mpos = json.loads(megadip.POSITIONS.read_text(encoding="utf-8")) if megadip.POSITIONS.exists() else {}
        megadip_lines = megadip.action_lines(mpos, megadip.fetch_rsi(megadip.MEGA), held=holdings)
        rets = megadip.fetch_rets(megadip.MEGA)
        megadip_lines += megadip.drop_lines(rets, rets.pop("QQQ", None), held=holdings)
    except Exception as e:
        print(f"(메가캡 섹션 생략: {e})", file=sys.stderr)

    report = build_report(signals, holdings, prices,
                          megadip_lines=megadip_lines,
                          sync_line=sync_warning(_load(SYNC_STATE, {}).get("date")),
                          confirmed=morning_tickers(signals.get("date")),
                          todo=today_todo())

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
                budget=None if separate else DISCORD_SAFE_LEN - len(report) - 2)
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
