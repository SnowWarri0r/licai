"""恐慌逃离指数 (per-stock, 0-100, 盘后硬数据)。

纯客观描述"今天卖压有多急/是不是一路被砸着夺路而逃", 只用盘后 OHLCV:
  跌幅 + 收盘位置(收在最低端) + 放量下跌 + 破位/N日新低 + 连跌。
配一个**自身历史分位**("今天的卖压比过去 N 个交易日里 X% 的日子更急"), 让绝对分有参照。

⚠️ 这是描述不是信号: 高分只表示今天砸得急, **不表示"该抄底"**。择时留给用户
(见 [[stock_no_recs]] [[stock_boundary]])。成分全部透出, 不做黑箱。
"""
from __future__ import annotations


def _ma(vals: list[float], upto: int, win: int) -> float | None:
    """vals[upto-win:upto] 的均值(不含 upto 当天), 数据不足返回 None。"""
    if upto < win:
        return None
    seg = vals[upto - win:upto]
    return sum(seg) / len(seg) if seg else None


def _day_score(bars: list[dict], i: int, limit: float,
               vol_win: int = 5, ma_win: int = 20, low_win: int = 20) -> dict | None:
    """第 i 根(需 i>=1, 用前一日昨收)的恐慌分与成分。数据不足返回 None。"""
    if i < 1:
        return None
    b, prev = bars[i], bars[i - 1]
    close = float(b.get("close") or 0)
    high = float(b.get("high") or 0)
    low = float(b.get("low") or 0)
    vol = float(b.get("volume") or 0)
    pc = float(prev.get("close") or 0)
    if close <= 0 or pc <= 0:
        return None

    drop = (pc - close) / pc                              # 跌幅(下跌为正)
    rng = high - low
    close_pos = (high - close) / rng if rng > 0 else 0.0  # 1=收在当日最低端
    closes = [float(x.get("close") or 0) for x in bars]
    vols = [float(x.get("volume") or 0) for x in bars]
    ma_vol = _ma(vols, i, vol_win)
    vol_ratio = (vol / ma_vol) if (ma_vol and ma_vol > 0) else 1.0
    ma20 = _ma(closes, i, ma_win)                         # 20日均线(不含今日)
    low_n = min([float(x.get("low") or 0) for x in bars[max(0, i - low_win):i]] or [low]) if i >= 1 else low

    clamp = lambda v: 0.0 if v < 0 else 1.0 if v > 1 else v
    down = drop > 0.0015                                  # 是否下跌日(过滤平盘噪声)
    c_drop = clamp(drop / limit) if limit > 0 else 0.0    # 跌幅占该股跌停幅度
    c_pos = close_pos if down else 0.0                    # 收在低端(仅下跌日算恐慌)
    c_vol = clamp((vol_ratio - 1) / 2) if down else 0.0   # 放量: 1x→0, 3x→1
    c_break = 1.0 if (down and ma20 and close < ma20) else 0.0
    c_low = 1.0 if (down and low <= low_n * 1.0009) else 0.0   # 触及/刷新 N 日最低
    # 连跌天数(含今日)
    streak = 0
    for j in range(i, 0, -1):
        if float(bars[j].get("close") or 0) < float(bars[j - 1].get("close") or 0):
            streak += 1
        else:
            break
    c_streak = clamp(streak / 5) if down else 0.0
    limit_down = drop >= (limit - 0.005)                  # 触/跌停

    score = 100 * clamp(
        0.38 * c_drop
        + 0.22 * c_pos
        + 0.18 * c_vol
        + 0.12 * max(c_break, c_low)
        + 0.10 * c_streak
    )
    if limit_down:                                        # 跌停/触板: 恐慌上限拔高
        score = max(score, 82.0)

    return {
        "score": round(score, 1),
        "drop_pct": round(drop * 100, 2),
        "close_pos": round(close_pos, 2),
        "vol_ratio": round(vol_ratio, 2),
        "down_streak": streak if down else 0,
        "below_ma20": bool(c_break),
        "new_low": bool(c_low),
        "limit_down": bool(limit_down),
    }


def compute(bars: list[dict], limit: float = 0.10, window: int = 120) -> dict | None:
    """bars: 日线升序 [{date,open,close,high,low,volume}], 末根=最新交易日。
    limit: 该股涨跌停幅度(主板0.10/创业科创0.20/北交0.30)。
    返回今日恐慌分 + 成分 + 自身近 window 日分位。数据不足返回 None。"""
    bars = [b for b in (bars or []) if b and (b.get("close") or 0) > 0]
    if len(bars) < 6:
        return None
    n = len(bars)
    today = _day_score(bars, n - 1, limit)
    if today is None:
        return None
    # 历史分位: 对窗口内每天算同一分数, 看今天排在多少百分位
    lo = max(1, n - window)
    hist = [s["score"] for i in range(lo, n)
            if (s := _day_score(bars, i, limit)) is not None]
    pct = None
    if len(hist) >= 10:
        below = sum(1 for x in hist if x < today["score"])
        pct = round(below / len(hist) * 100)
    lvl = ("剧烈" if today["score"] >= 75 else "明显" if today["score"] >= 50
           else "温和" if today["score"] >= 25 else "平静")
    return {
        **today,
        "percentile": pct,               # 今天卖压 > 过去 window 日里 pct% 的日子
        "level": lvl,
        "sample_days": len(hist),
        "note": "恐慌逃离指数=当日卖压强度的客观刻画(跌幅+收盘位置+放量+破位/新低+连跌), "
                "0-100 越高砸得越急; percentile 是与该股自身近期比的分位。"
                "仅描述卖压强度, 不预测方向、不构成买卖建议。",
    }
