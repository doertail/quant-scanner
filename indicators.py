"""
indicators.py — 기술적 지표 계산

RSI(14), MA20/50/200, ATR(14), Vol_MA20, ADX(14) + DI±.
배치 다운로드 결과(MultiIndex DataFrame)에서 종목별 지표 DataFrame을 빌드.
"""

import pandas as pd

from config import ADX_PERIOD, ATR_PERIOD, RSI_PERIOD


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """RSI(14), MA20, MA50, MA200, ATR(14), Vol_MA20 계산"""
    delta = df['Close'].diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    df['RSI']      = 100 - (100 / (1 + up.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
                                       / down.ewm(com=RSI_PERIOD - 1, adjust=False).mean()))
    df['MA20']     = df['Close'].rolling(20).mean()
    df['MA50']     = df['Close'].rolling(50).mean()
    df['MA200']    = df['Close'].rolling(200).mean()
    df['Vol_MA20'] = df['Volume'].fillna(0).rolling(20).mean()
    # 장중 실행 시 오늘 부분 거래량 대신 전일 완전 거래량 사용 (0.1x 오표시 방지)
    df['Vol_Prev'] = df['Volume'].shift(1)
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - prev_close).abs(),
        (df['Low']  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(com=ATR_PERIOD - 1, adjust=False).mean()
    return df


def compute_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> tuple[float, float, float]:
    """ADX(period), DI+, DI- 반환. 실패 시 (nan, nan, nan)."""
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        up_move, down_move = high - high.shift(1), low.shift(1) - low
        plus_dm  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        atr14    = tr.ewm(com=period - 1, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(com=period - 1, adjust=False).mean() / atr14
        minus_di = 100 * minus_dm.ewm(com=period - 1, adjust=False).mean() / atr14
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx      = dx.ewm(com=period - 1, adjust=False).mean()
        return float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])
    except Exception:
        return float('nan'), float('nan'), float('nan')


def build_stock_data(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """배치 다운로드 결과에서 종목별 지표 DataFrame을 빌드. 데이터 부족/NaN 종목은 자동 제외."""
    result = {}
    for ticker in tickers:
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df = raw[ticker][['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            df = df.dropna(subset=['Close'])
            if len(df) < 210:
                continue
            df = compute_indicators(df)
            if df[['RSI', 'MA20', 'MA50', 'MA200', 'ATR']].iloc[-1].isna().any():
                continue
            result[ticker] = df
        except Exception:
            pass
    return result


def vol_ratio_of(row: pd.Series) -> float:
    """현재(또는 전일 완전봉) 거래량 / 20일 평균. 0 또는 NaN 시 1.0."""
    vol_ma20 = row.get('Vol_MA20', 0)
    if not vol_ma20 or pd.isna(vol_ma20) or vol_ma20 == 0:
        return 1.0
    vol = row.get('Vol_Prev', row.get('Volume', 0))  # 전일 완전 거래량 우선 (장중 부분봉 방지)
    return round(float(vol) / float(vol_ma20), 2) if vol else 1.0
