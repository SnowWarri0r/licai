"""把 get_trend 的 OHLCV 渲染成一张 K线+量能+均线 的 PNG, 并把已检测到的结构
(台阶支撑/颈线)标注在图上。图由我们自己的数据画 → 精确无幻觉; 既给用户看(更直观),
也作多模态模型的 gestalt 辅助(发现写死程序没编码的形态)。数字仍以结构化字段为准。

红涨绿跌(A股惯例), 配色对齐 App 深色主题。"""
from __future__ import annotations
import io
import os
import threading
import uuid

import matplotlib
matplotlib.use("Agg")   # 无界面后端, 服务端渲染
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib import font_manager       # noqa: E402
import pandas as pd                        # noqa: E402
import mplfinance as mpf                   # noqa: E402

from config import config                  # noqa: E402

_lock = threading.Lock()   # matplotlib/pyplot 非线程安全, 同一进程串行渲染
_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(config.db_path)) or ".", "ask_media")

# 深色主题配色(对齐前端 --color-*)
_BG = "#15171c"; _GRID = "#23262e"; _FG = "#cdd0d6"
_UP = "#cf5c5c"; _DOWN = "#5fa86c"          # 红涨绿跌
_ACCENT = "#c8a876"; _NECK = "#6f9fd8"      # 台阶支撑=金 / 颈线=蓝


def _setup_cjk_font():
    """注册一款系统中文字体, 返回 (字体名, FontProperties); 避免标题/标签出现豆腐块。"""
    matplotlib.rcParams["axes.unicode_minus"] = False
    for fp in ("/System/Library/Fonts/PingFang.ttc",
               "/System/Library/Fonts/STHeiti Light.ttc",
               "/System/Library/Fonts/Hiragino Sans GB.ttc",
               "/Library/Fonts/Arial Unicode.ttf",
               "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
        if os.path.exists(fp):
            try:
                font_manager.fontManager.addfont(fp)
                prop = font_manager.FontProperties(fname=fp)
                name = prop.get_name()
                matplotlib.rcParams["font.family"] = name
                matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
                return name, prop
            except Exception:
                continue
    return None, None


_CJK, _FP = _setup_cjk_font()
_TAG_KEYS = ("阶梯式上行", "抬高低点", "结构破位", "跌破近台阶",
             "双顶", "二次冲高未创新高", "跌破颈线",
             "头肩顶", "头肩底", "突破底颈线", "顶背离", "底背离", "收敛三角")


def _fractals(seq, low: bool, w: int = 3, gap: int = 3):
    """分型拐点(与 _structure_scan 同口径), 用于在图上标摆动高/低点。返回 [(idx, value), ...]。"""
    pts = []
    for i in range(w, len(seq) - w):
        seg = seq[i - w:i + w + 1]
        if seq[i] is None or any(x is None for x in seg):
            continue
        if (low and seq[i] == min(seg)) or (not low and seq[i] == max(seg)):
            pts.append((i, seq[i]))
    if not pts:
        return []
    out = [pts[0]]
    for idx, val in pts[1:]:
        if idx - out[-1][0] <= gap:
            if (low and val < out[-1][1]) or (not low and val > out[-1][1]):
                out[-1] = (idx, val)
        else:
            out.append((idx, val))
    return out


def render_trend_chart(bars: list, *, code: str = "", name: str = "",
                       structure: dict | None = None, display: int = 50) -> bytes | None:
    """bars: [(date, close, high, low, vol, open), ...] 升序。传入比展示窗口更长的序列(含前置数据),
    均线在完整序列上算好再裁切, 避免 MA 在窗口前段断开。只显示最后 display 根。返回 PNG bytes 或 None。"""
    structure = structure or {}
    idx, rows = [], []
    for d, c, h, l, v, o in bars:
        if not (o and h and l and c):
            continue
        idx.append(pd.Timestamp(str(d)[:10]))
        rows.append((float(o), float(h), float(l), float(c), float(v or 0)))
    if len(rows) < 5:
        return None
    df_full = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                           index=pd.DatetimeIndex(idx))
    # 均线在完整序列上算(含前置数据), 再裁到展示窗口 → MA 从第一根展示K线起就连续, 前段不断开
    closes_full = df_full["Close"]
    ma5, ma10, ma20 = (closes_full.rolling(w).mean() for w in (5, 10, 20))
    disp = min(display, len(df_full))
    start = len(df_full) - disp
    df = df_full.iloc[start:]

    mc = mpf.make_marketcolors(up=_UP, down=_DOWN, edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                               facecolor=_BG, figcolor=_BG, gridcolor=_GRID, edgecolor=_GRID,
                               rc={"font.size": 9, "axes.labelcolor": _FG,
                                   "xtick.color": _FG, "ytick.color": _FG,
                                   **({"font.family": _CJK} if _CJK else {})})

    # 结构线: (价位, 颜色, 标签) —— 横线 + 右端直接标名称价位
    line_specs = []
    if structure.get("台阶支撑"):
        line_specs.append((structure["台阶支撑"], _ACCENT, "台阶支撑"))
    if structure.get("颈线"):
        line_specs.append((structure["颈线"], _NECK, "颈线"))
    if structure.get("底颈线"):
        line_specs.append((structure["底颈线"], "#7bb37a", "底颈线"))

    # 均线裁到展示窗口后作 addplot 叠加(取代 mpf 自带 mav, 后者只在展示窗口内算会前段断开)
    aps = [mpf.make_addplot(ma5.iloc[start:], color="#e8b04a", width=0.9),
           mpf.make_addplot(ma10.iloc[start:], color="#4aa6e0", width=0.9),
           mpf.make_addplot(ma20.iloc[start:], color="#cf6bcf", width=0.9)]
    kwargs = dict(type="candle", volume=True, addplot=aps, style=style,
                  figsize=(10, 6.2), returnfig=True, tight_layout=True,
                  ylabel="", ylabel_lower="", datetime_format="%m-%d", xrotation=0,
                  update_width_config=dict(candle_linewidth=0.7, candle_width=0.62))
    if line_specs:
        kwargs["hlines"] = dict(hlines=[s[0] for s in line_specs], colors=[s[1] for s in line_specs],
                                linestyle="--", linewidths=1.0, alpha=0.9)

    title = (f"{name} {code}").strip()
    tags = [k for k in _TAG_KEYS if structure.get(k)]
    if tags:
        title += "   " + " · ".join(tags)

    # 摆动高/低点(结构骨架): 在完整序列上找分型, 平移到展示窗口坐标(只画落在窗口内的)
    Hs = [r[1] for r in rows]
    Ls = [r[2] for r in rows]
    shi = [(i - start, v) for i, v in _fractals(Hs, low=False) if i >= start]
    slo = [(i - start, v) for i, v in _fractals(Ls, low=True) if i >= start]

    with _lock:
        fig, axes = mpf.plot(df, **kwargs)
        ax = axes[0]
        n = len(df)
        _tkw = {"fontproperties": _FP} if _FP else {}
        fig.suptitle(title, color="#e8e6e1", fontsize=12, y=0.985, **_tkw)
        # 跳空缺口阴影带(价位区间)
        for key, col in (("向上跳空缺口", _UP), ("向下跳空缺口", _DOWN)):
            g = structure.get(key)
            if isinstance(g, list) and len(g) == 2 and all(g):
                lo_, hi_ = min(g), max(g)
                ax.axhspan(lo_, hi_, color=col, alpha=0.13, zorder=0)
                ax.text(0.6, (lo_ + hi_) / 2, "缺口", color=col, fontsize=7.5, va="center", **_tkw)
        # 摆动高/低点标记(稍偏出K线, 放大便于看清)
        span = float(df["High"].max() - df["Low"].min()) or 1
        off = span * 0.015
        for i, hv in shi:
            ax.scatter(i, hv + off, marker="v", s=70, color="#e88a8a", zorder=6, edgecolors="none")
        for i, lv in slo:
            ax.scatter(i, lv - off, marker="^", s=70, color="#74bd74", zorder=6, edgecolors="none")
        # 结构线右端标名称+价位(自解释, 不靠图例)
        for lv, col, lb in line_specs:
            ax.text(n - 0.5, lv, f" {lb} {lv}", color=col, fontsize=8, va="center", ha="left", **_tkw)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=160, facecolor=_BG, bbox_inches="tight")
        plt.close(fig)
    return buf.getvalue()


def save_png(png: bytes) -> str:
    """把 PNG 落盘到 ask_media, 返回前端可访问的 URL(/api/ask/image/<uuid>.png)。"""
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    name = f"chart_{uuid.uuid4().hex}.png"
    with open(os.path.join(_MEDIA_DIR, name), "wb") as f:
        f.write(png)
    return f"/api/ask/image/{name}"
