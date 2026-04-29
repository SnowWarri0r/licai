"""Technical analysis calculations for A-share T-trading."""
from __future__ import annotations
import numpy as np
import pandas as pd

from config import config


def calculate_moving_averages(df: pd.DataFrame, periods: list[int] = None) -> dict[int, float]:
    """Calculate moving averages for the close price series.
    Returns {period: ma_value} using the latest data point.
    """
    if periods is None:
        periods = config.ma_periods
    if df.empty:
        return {}

    close = df["收盘"].astype(float)
    result = {}
    for p in periods:
        if len(close) >= p:
            result[p] = round(float(close.rolling(p).mean().iloc[-1]), 3)
    return result


def calculate_atr(df: pd.DataFrame, period: int = None) -> float:
    """Calculate Average True Range."""
    if period is None:
        period = config.atr_period
    if len(df) < period + 1:
        return 0.0

    high = df["最高"].astype(float)
    low = df["最低"].astype(float)
    close = df["收盘"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().iloc[-1]
    return round(float(atr), 3)


def calculate_rsi(df: pd.DataFrame, period: int = None) -> float:
    """Calculate Relative Strength Index."""
    if period is None:
        period = config.rsi_period
    if len(df) < period + 1:
        return 50.0

    close = df["收盘"].astype(float)
    delta = close.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_bollinger_bands(df: pd.DataFrame, period: int = None, std_dev: float = None) -> dict:
    """Calculate Bollinger Bands. Returns {upper, middle, lower}."""
    if period is None:
        period = config.bollinger_period
    if std_dev is None:
        std_dev = config.bollinger_std
    if len(df) < period:
        return {}

    close = df["收盘"].astype(float)
    middle = close.rolling(period).mean().iloc[-1]
    std = close.rolling(period).std().iloc[-1]

    return {
        "upper": round(float(middle + std_dev * std), 3),
        "middle": round(float(middle), 3),
        "lower": round(float(middle - std_dev * std), 3),
    }


def calculate_support_resistance(df: pd.DataFrame, lookback: int = None) -> dict:
    """Find support and resistance levels from recent price action.
    Uses pivot point detection: a point is a local high/low if it's
    the extreme within a window of +-2 bars.
    Returns {support: [levels], resistance: [levels]} sorted by proximity to current price.
    """
    if lookback is None:
        lookback = config.support_resistance_lookback
    if len(df) < 5:
        return {"support": [], "resistance": []}

    recent = df.tail(lookback)
    high = recent["最高"].astype(float).values
    low = recent["最低"].astype(float).values
    close_now = float(df["收盘"].iloc[-1])

    window = 2
    supports = []
    resistances = []

    for i in range(window, len(low) - window):
        # Local minimum (support)
        if low[i] == min(low[i - window:i + window + 1]):
            supports.append(float(low[i]))
        # Local maximum (resistance)
        if high[i] == max(high[i - window:i + window + 1]):
            resistances.append(float(high[i]))

    # Cluster nearby levels (within 1%)
    supports = _cluster_levels(supports, threshold=0.01)
    resistances = _cluster_levels(resistances, threshold=0.01)

    # Filter: supports below current price, resistances above
    supports = sorted([s for s in supports if s < close_now], reverse=True)
    resistances = sorted([r for r in resistances if r > close_now])

    return {
        "support": [round(s, 3) for s in supports[:5]],
        "resistance": [round(r, 3) for r in resistances[:5]],
    }


def _cluster_levels(levels: list[float], threshold: float = 0.01) -> list[float]:
    """Cluster nearby price levels, returning the average of each cluster."""
    if not levels:
        return []

    levels = sorted(levels)
    clusters = [[levels[0]]]

    for level in levels[1:]:
        if abs(level - clusters[-1][-1]) / clusters[-1][-1] < threshold:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    # Return the mean of each cluster, weighted by count (more touches = stronger)
    return [np.mean(c) for c in clusters]


def calculate_vwap(intraday_df: pd.DataFrame) -> float | None:
    """Calculate Volume-Weighted Average Price from intraday data."""
    if intraday_df.empty:
        return None

    try:
        # Try different column name patterns
        close_col = None
        vol_col = None
        for c in intraday_df.columns:
            if "收盘" in c or "close" in c.lower():
                close_col = c
            if "成交量" in c or "volume" in c.lower():
                vol_col = c

        if close_col and vol_col:
            close = intraday_df[close_col].astype(float)
            volume = intraday_df[vol_col].astype(float)
            total_vol = volume.sum()
            if total_vol > 0:
                return round(float((close * volume).sum() / total_vol), 3)
    except Exception:
        pass
    return None


def analyze_volume(df: pd.DataFrame, lookback: int = 10) -> dict:
    """Analyze volume patterns.
    Returns:
      volume_ratio: today's volume / average volume (>1.5 = 放量, <0.7 = 缩量)
      volume_trend: 'high' / 'normal' / 'low'
      price_volume_divergence: True if price drops but volume spikes (potential reversal)
    """
    if len(df) < lookback + 1 or "成交量" not in df.columns:
        return {"volume_ratio": 1.0, "volume_trend": "normal", "price_volume_divergence": False}

    vol = df["成交量"].astype(float)
    close = df["收盘"].astype(float)
    today_vol = vol.iloc[-1]
    avg_vol = vol.iloc[-(lookback + 1):-1].mean()

    ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 1.0

    if ratio >= 1.5:
        trend = "high"
    elif ratio <= 0.7:
        trend = "low"
    else:
        trend = "normal"

    # Price-volume divergence: price down but volume up significantly
    price_change = close.iloc[-1] - close.iloc[-2] if len(close) >= 2 else 0
    divergence = price_change < 0 and ratio >= 1.3

    return {
        "volume_ratio": ratio,
        "volume_trend": trend,
        "price_volume_divergence": divergence,
    }


def detect_bb_squeeze(df: pd.DataFrame, period: int = 20) -> dict:
    """Detect Bollinger Band squeeze — bands narrowing signals upcoming volatility.
    Returns:
      squeeze: True if current BB width is in the lowest 20% of recent history
      bb_width_pct: current BB width as % of price
      expanding: True if BB is expanding from squeeze (breakout imminent)
    """
    if len(df) < period + 10:
        return {"squeeze": False, "bb_width_pct": 0, "expanding": False}

    close = df["收盘"].astype(float)
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    width = (std * 2 / ma * 100).dropna()  # BB width as % of MA

    if len(width) < 10:
        return {"squeeze": False, "bb_width_pct": 0, "expanding": False}

    current_width = float(width.iloc[-1])
    prev_width = float(width.iloc[-2])
    percentile = float((width < current_width).sum() / len(width) * 100)

    return {
        "squeeze": percentile < 25,  # in bottom 25% = squeeze
        "bb_width_pct": round(current_width, 2),
        "expanding": current_width > prev_width and percentile < 40,  # was squeezed, now expanding
    }


def score_support_resistance(df: pd.DataFrame, levels: list[float], lookback: int = 30) -> dict[float, int]:
    """Score S/R levels by how many times they've been tested and held.
    A level that was touched 3 times and held is stronger than one touched once.
    Returns {price_level: touch_count}.
    """
    if len(df) < 5 or not levels:
        return {}

    recent = df.tail(lookback)
    high = recent["最高"].astype(float).values
    low = recent["最低"].astype(float).values

    scores = {}
    for level in levels:
        threshold = level * 0.01  # 1% tolerance
        touches = 0
        for i in range(len(low)):
            # Price touched this level (within 1%) but didn't break through significantly
            if abs(low[i] - level) < threshold or abs(high[i] - level) < threshold:
                touches += 1
        scores[level] = touches
    return scores


def get_full_analysis(historical_df: pd.DataFrame, intraday_df: pd.DataFrame = None) -> dict:
    """Run all technical analysis and return a combined result dict."""
    mas = calculate_moving_averages(historical_df)
    atr = calculate_atr(historical_df)
    rsi = calculate_rsi(historical_df)
    bb = calculate_bollinger_bands(historical_df)
    sr = calculate_support_resistance(historical_df)
    vwap = calculate_vwap(intraday_df) if intraday_df is not None else None
    volume = analyze_volume(historical_df)
    bb_squeeze = detect_bb_squeeze(historical_df)

    # Score S/R levels by effectiveness
    all_levels = sr.get("support", []) + sr.get("resistance", [])
    sr_scores = score_support_resistance(historical_df, all_levels)

    return {
        "ma": mas,
        "atr": atr,
        "rsi": rsi,
        "bollinger": bb,
        "support_resistance": sr,
        "sr_scores": sr_scores,
        "vwap": vwap,
        "volume": volume,
        "bb_squeeze": bb_squeeze,
    }
