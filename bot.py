"""
Discord Trade Bot
────────────────────────────────────────────────────────────
Commands:
  !buy     TICKER 수량 가격 [A|B|C]  — 매수 기록 (전략 기본값: A)
  !sell    TICKER 가격 [수량]        — 매도 기록 + 현금 자동 반영
  !port                              — 포트폴리오 조회 (실시간 평가손익)
  !summary                           — 전략별 승률 및 수익률 요약
  !cash    [금액]                    — 현금 잔고 조회/업데이트
  !stop    TICKER 가격               — 트레일링 스톱 수동 설정
  !help / !도움말                    — 명령어 안내
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import discord
import yfinance as yf
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR       = Path(__file__).resolve().parent
PORTFOLIO_FILE = BASE_DIR / 'portfolio.json'
TRADES_FILE    = BASE_DIR / 'trades.json'

load_dotenv(BASE_DIR / '.env')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


# ─── 포트폴리오 ────────────────────────────────────────────────────────────────

def load_portfolio() -> tuple[dict, float]:
    try:
        with open(PORTFOLIO_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('holdings', {}), float(data.get('cash', 0))
    except FileNotFoundError:
        return {}, 0.0


def save_portfolio(holdings: dict, cash: float) -> None:
    with open(PORTFOLIO_FILE, 'w', encoding='utf-8') as f:
        json.dump({'holdings': holdings, 'cash': cash}, f, indent=2, ensure_ascii=False)


# ─── 거래 히스토리 ─────────────────────────────────────────────────────────────

def load_trades() -> list:
    try:
        with open(TRADES_FILE, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def append_trade(trade: dict) -> None:
    trades = load_trades()
    trades.append(trade)
    with open(TRADES_FILE, 'w', encoding='utf-8') as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


# ─── 실시간 가격 조회 ──────────────────────────────────────────────────────────

def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    """yfinance로 현재가 일괄 조회 (동기 함수 — executor에서 실행)"""
    if not tickers:
        return {}
    data = yf.download(
        tickers, period='5d',
        group_by='ticker',
        progress=False, threads=False,
    )
    prices = {}
    single = len(tickers) == 1
    for t in tickers:
        try:
            # yfinance: 단일 ticker → 컬럼이 단일 레벨, 복수 → 멀티 레벨
            col = data['Close'] if single else data[t]['Close']
            prices[t] = float(col.dropna().iloc[-1])
        except Exception as e:
            print(f'[Warn] {t} 가격 조회 실패: {e}')
    return prices


# ─── 이벤트 ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f'[Bot] {bot.user} 연결됨')


# ─── 명령어 ───────────────────────────────────────────────────────────────────

@bot.command(name='buy')
async def cmd_buy(ctx, ticker: str = '', shares: str = '', price: str = '', strategy: str = 'A'):
    """!buy TICKER 수량 가격 [A|B|C]"""
    ticker   = ticker.upper()
    strategy = strategy.upper()

    if not ticker or not shares or not price:
        await ctx.send('사용법: `!buy TICKER 수량 가격 [A|B|C]`\n예) `!buy AAPL 10 150.25 A`')
        return
    try:
        shares_f = float(shares)
        price_f  = float(price)
    except ValueError:
        await ctx.send('수량과 가격은 숫자로 입력하세요.')
        return
    if strategy not in ('A', 'B', 'C'):
        await ctx.send('전략은 A, B, C 중 하나여야 합니다.')
        return

    holdings, cash = load_portfolio()

    if ticker in holdings:
        old        = holdings[ticker]
        old_shares = old.get('shares', 0)
        old_price  = old.get('buy_price', price_f)
        new_shares = old_shares + shares_f
        avg_price  = (old_shares * old_price + shares_f * price_f) / new_shares
        holdings[ticker]['shares']    = round(new_shares, 4)
        holdings[ticker]['buy_price'] = round(avg_price, 4)
        msg = (f'**{ticker}** 추가 매수\n'
               f'  {old_shares} + {shares_f} = {new_shares}주\n'
               f'  평균단가: ${avg_price:.2f}')
    else:
        holdings[ticker] = {
            'shares':        shares_f,
            'buy_price':     price_f,
            'trailing_stop': 0.0,
            'tp1_hit':       False,
            'strategy':      strategy,
        }
        msg = (f'**{ticker}** 신규 매수 기록\n'
               f'  수량: {shares_f}주  |  단가: ${price_f:.2f}  |  전략: {strategy}\n'
               f'  ※ trailing_stop은 다음 스캔 시 자동 설정됩니다.')

    cost = round(shares_f * price_f, 2)
    cash = round(cash - cost, 2)
    save_portfolio(holdings, cash)
    await ctx.send(msg + f'\n  현금 차감: -${cost:,.2f} → 잔고 ${cash:,.2f}')


@bot.command(name='sell')
async def cmd_sell(ctx, ticker: str = '', price: str = '', shares: str = ''):
    """!sell TICKER 가격 [수량]  — 수량 생략 시 전량 매도, 현금 자동 반영"""
    ticker = ticker.upper()
    if not ticker or not price:
        await ctx.send('사용법: `!sell TICKER 가격 [수량]`\n예) `!sell AAPL 152.50` 또는 `!sell AAPL 152.50 5`')
        return

    try:
        price_f = float(price)
    except ValueError:
        await ctx.send('가격은 숫자로 입력하세요.')
        return

    holdings, cash = load_portfolio()
    if ticker not in holdings:
        await ctx.send(f'**{ticker}** 포트폴리오에 없음.')
        return

    held      = holdings[ticker].get('shares', 0)
    buy_price = holdings[ticker].get('buy_price', 0)
    strategy  = holdings[ticker].get('strategy', 'A')

    if shares:
        try:
            sell_shares = float(shares)
        except ValueError:
            await ctx.send('수량은 숫자로 입력하세요.')
            return
        sell_shares = min(sell_shares, held)
    else:
        sell_shares = held

    proceeds = round(sell_shares * price_f, 2)
    pnl      = round((price_f - buy_price) * sell_shares, 2)
    pnl_pct  = round((price_f - buy_price) / buy_price * 100, 2) if buy_price else 0
    pnl_sign = '+' if pnl >= 0 else ''
    cash     = round(cash + proceeds, 2)

    # 거래 히스토리 기록
    append_trade({
        'date':       datetime.today().strftime('%Y-%m-%d'),
        'ticker':     ticker,
        'strategy':   strategy,
        'shares':     sell_shares,
        'buy_price':  buy_price,
        'sell_price': price_f,
        'pnl':        pnl,
        'pnl_pct':    pnl_pct,
    })

    if sell_shares >= held:
        del holdings[ticker]
        msg = (f'**{ticker}** 전량 매도 ({held}주 × ${price_f:.2f})\n'
               f'  수익: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct}%)\n'
               f'  현금 반영: +${proceeds:,.2f} → 잔고 ${cash:,.2f}')
    else:
        remaining = round(held - sell_shares, 4)
        holdings[ticker]['shares'] = remaining
        msg = (f'**{ticker}** 일부 매도 ({sell_shares}주 × ${price_f:.2f})\n'
               f'  수익: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct}%)  |  잔여 {remaining}주\n'
               f'  현금 반영: +${proceeds:,.2f} → 잔고 ${cash:,.2f}')

    save_portfolio(holdings, cash)
    await ctx.send(msg)


@bot.command(name='port')
async def cmd_port(ctx):
    """!port — 포트폴리오 조회 (실시간 평가손익)"""
    holdings, cash = load_portfolio()
    if not holdings:
        await ctx.send(f'포트폴리오 비어있음. 현금: ${cash:,.2f}')
        return

    await ctx.send('⏳ 실시간 가격 조회 중...')

    tickers = list(holdings.keys())
    try:
        prices = await asyncio.wait_for(
            asyncio.to_thread(_fetch_prices, tickers),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        await ctx.send('⚠️ 가격 조회 시간 초과 (20초). yfinance 응답 없음.')
        return

    total_cost   = 0.0
    total_value  = 0.0

    lines = ['```']
    lines.append(f'{"종목(전략)":<11} {"수량":>6} {"단가":>8} {"현재가":>8} {"평가손익":>10} {"수익률":>7}')
    lines.append('─' * 58)

    for ticker, pos in sorted(holdings.items()):
        label     = f'{ticker}({pos.get("strategy","?")})'
        shares    = pos.get('shares', 0)
        bp        = pos.get('buy_price', 0)
        cur       = prices.get(ticker)
        cost      = shares * bp
        total_cost += cost

        if cur:
            value      = shares * cur
            upnl       = value - cost
            upnl_pct   = (cur - bp) / bp * 100 if bp else 0
            sign       = '+' if upnl >= 0 else ''
            total_value += value
            lines.append(
                f'{label:<11} {shares:>6.2f} ${bp:>7.2f} ${cur:>7.2f} '
                f'{sign}${upnl:>8.2f} {sign}{upnl_pct:>5.1f}%'
            )
        else:
            total_value += cost
            lines.append(f'{label:<11} {shares:>6.2f} ${bp:>7.2f} {"N/A":>8} {"─":>10} {"─":>7}')

    lines.append('─' * 58)
    total_upnl     = total_value - total_cost
    total_upnl_pct = (total_upnl / total_cost * 100) if total_cost else 0
    sign           = '+' if total_upnl >= 0 else ''
    lines.append(
        f'{"합계":<11} {"":>6} {"":>8} {"":>8} '
        f'{sign}${total_upnl:>8.2f} {sign}{total_upnl_pct:>5.1f}%'
    )
    lines.append('```')
    lines.append(f'평가금액 ${total_value:,.2f}  |  현금 ${cash:,.2f}  |  총자산 ${total_value + cash:,.2f}')

    await ctx.send('\n'.join(lines))


@bot.command(name='summary')
async def cmd_summary(ctx):
    """!summary — 전략별 승률 및 수익률 요약"""
    trades = load_trades()
    if not trades:
        await ctx.send('거래 히스토리 없음. `!sell` 로 매도 기록을 남기면 집계됩니다.')
        return

    # 전략별 집계
    stats: dict[str, dict] = {}
    for t in trades:
        s = t.get('strategy', '?')
        if s not in stats:
            stats[s] = {'count': 0, 'wins': 0, 'total_pnl': 0.0, 'pnl_pcts': []}
        stats[s]['count']    += 1
        stats[s]['total_pnl'] = round(stats[s]['total_pnl'] + t.get('pnl', 0), 2)
        stats[s]['pnl_pcts'].append(t.get('pnl_pct', 0))
        if t.get('pnl', 0) >= 0:
            stats[s]['wins'] += 1

    lines = ['```', f'{"전략":<6} {"건수":>5} {"승률":>7} {"평균수익률":>10} {"총손익":>12}', '─' * 44]
    all_count = all_wins = 0
    all_pnl   = 0.0
    all_pcts  = []

    for s, d in sorted(stats.items()):
        win_rate = d['wins'] / d['count'] * 100
        avg_pct  = sum(d['pnl_pcts']) / len(d['pnl_pcts'])
        sign     = '+' if d['total_pnl'] >= 0 else ''
        lines.append(
            f'{s:<6} {d["count"]:>5}건  {win_rate:>5.1f}%  {avg_pct:>+9.2f}%  {sign}${d["total_pnl"]:>10,.2f}'
        )
        all_count += d['count']
        all_wins  += d['wins']
        all_pnl    = round(all_pnl + d['total_pnl'], 2)
        all_pcts  += d['pnl_pcts']

    lines.append('─' * 44)
    all_win_rate = all_wins / all_count * 100
    all_avg_pct  = sum(all_pcts) / len(all_pcts)
    sign         = '+' if all_pnl >= 0 else ''
    lines.append(
        f'{"전체":<6} {all_count:>5}건  {all_win_rate:>5.1f}%  {all_avg_pct:>+9.2f}%  {sign}${all_pnl:>10,.2f}'
    )
    lines.append('```')

    await ctx.send('\n'.join(lines))


@bot.command(name='cash')
async def cmd_cash(ctx, amount: str = ''):
    """!cash [금액] — 현금 잔고 조회/업데이트"""
    if not amount:
        _, cash = load_portfolio()
        await ctx.send(f'현재 현금 잔고: ${cash:,.2f}\n변경하려면: `!cash 5000`')
        return
    try:
        amount_f = float(amount)
    except ValueError:
        await ctx.send('금액은 숫자로 입력하세요.')
        return

    holdings, _ = load_portfolio()
    save_portfolio(holdings, amount_f)
    await ctx.send(f'현금 잔고 업데이트: ${amount_f:,.2f}')


@bot.command(name='stop')
async def cmd_stop(ctx, ticker: str = '', price: str = ''):
    """!stop TICKER 가격 — 트레일링 스톱 수동 설정"""
    ticker = ticker.upper()
    if not ticker or not price:
        await ctx.send('사용법: `!stop AAPL 145.00`')
        return
    try:
        price_f = float(price)
    except ValueError:
        await ctx.send('가격은 숫자로 입력하세요.')
        return

    holdings, cash = load_portfolio()
    if ticker not in holdings:
        await ctx.send(f'**{ticker}** 포트폴리오에 없음.')
        return

    old_stop = holdings[ticker].get('trailing_stop', 0)
    holdings[ticker]['trailing_stop'] = price_f
    save_portfolio(holdings, cash)
    await ctx.send(f'**{ticker}** 트레일링 스톱 수정: ${old_stop:.2f} → ${price_f:.2f}')


@bot.command(name='도움말', aliases=['명령어', 'help'])
async def cmd_help_kr(ctx):
    """!도움말 — 명령어 목록"""
    msg = (
        '```\n'
        '!buy     TICKER 수량 가격 [A|B|C]  — 매수 기록 (전략 기본값: A)\n'
        '!sell    TICKER 가격 [수량]        — 매도 기록 + 현금 자동 반영\n'
        '!port                             — 포트폴리오 조회 (실시간 평가손익)\n'
        '!summary                          — 전략별 승률 및 수익률 요약\n'
        '!cash    [금액]                   — 현금 잔고 조회/업데이트\n'
        '!stop    TICKER 가격              — 트레일링 스톱 수동 설정\n'
        '\n'
        '예시)\n'
        '  !buy AAPL 10 150.25\n'
        '  !buy NVDA 5 800.00 B\n'
        '  !sell AAPL 152.50\n'
        '  !sell AAPL 152.50 5\n'
        '  !stop AAPL 145.00\n'
        '  !cash 5000\n'
        '```'
    )
    await ctx.send(msg)


if __name__ == '__main__':
    token = os.getenv('DISCORD_BOT_TOKEN', '')
    if not token:
        print('[Error] .env에 DISCORD_BOT_TOKEN이 없습니다.')
        raise SystemExit(1)
    bot.run(token)
