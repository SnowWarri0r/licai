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
             "双顶", "二次冲高未创新高", "跌破颈线")


def render_trend_chart(bars: list, *, code: str = "", name: str = "",
                       structure: dict | None = None) -> bytes | None:
    """bars: [(date, close, high, low, vol, open), ...] 升序(已截到展示窗口)。返回 PNG bytes 或 None。"""
    structure = structure or {}
    idx, rows = [], []
    for d, c, h, l, v, o in bars:
        if not (o and h and l and c):
            continue
        idx.append(pd.Timestamp(str(d)[:10]))
        rows.append((float(o), float(h), float(l), float(c), float(v or 0)))
    if len(rows) < 5:
        return None
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                      index=pd.DatetimeIndex(idx))

    mc = mpf.make_marketcolors(up=_UP, down=_DOWN, edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                               facecolor=_BG, figcolor=_BG, gridcolor=_GRID, edgecolor=_GRID,
                               rc={"font.size": 9, "axes.labelcolor": _FG,
                                   "xtick.color": _FG, "ytick.color": _FG,
                                   **({"font.family": _CJK} if _CJK else {})})

    hlines, hcolors = [], []
    if structure.get("台阶支撑"):
        hlines.append(structure["台阶支撑"]); hcolors.append(_ACCENT)
    if structure.get("颈线"):
        hlines.append(structure["颈线"]); hcolors.append(_NECK)

    kwargs = dict(type="candle", volume=True, mav=(5, 10, 20), style=style,
                  figsize=(10, 6.2), returnfig=True, tight_layout=True,
                  ylabel="", ylabel_lower="量", datetime_format="%m-%d", xrotation=0,
                  update_width_config=dict(candle_linewidth=0.7, candle_width=0.62))
    if hlines:
        kwargs["hlines"] = dict(hlines=hlines, colors=hcolors, linestyle="--", linewidths=1.0, alpha=0.9)

    title = (f"{name} {code}").strip()
    tags = [k for k in _TAG_KEYS if structure.get(k)]
    if tags:
        title += "   " + " · ".join(tags)
    legend = []
    if structure.get("台阶支撑"):
        legend.append(f"台阶支撑 {structure['台阶支撑']}")
    if structure.get("颈线"):
        legend.append(f"颈线 {structure['颈线']}")

    with _lock:
        fig, axes = mpf.plot(df, **kwargs)
        _tkw = {"fontproperties": _FP} if _FP else {}
        fig.suptitle(title, color="#e8e6e1", fontsize=12, y=0.985, **_tkw)
        if legend:
            axes[0].text(0.01, 0.02, "  ".join(legend), transform=axes[0].transAxes,
                         color=_FG, fontsize=8.5, va="bottom", **_tkw,
                         bbox=dict(facecolor=_BG, edgecolor=_GRID, boxstyle="round,pad=0.3", alpha=0.85))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, facecolor=_BG, bbox_inches="tight")
        plt.close(fig)
    return buf.getvalue()


def save_png(png: bytes) -> str:
    """把 PNG 落盘到 ask_media, 返回前端可访问的 URL(/api/ask/image/<uuid>.png)。"""
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    name = f"chart_{uuid.uuid4().hex}.png"
    with open(os.path.join(_MEDIA_DIR, name), "wb") as f:
        f.write(png)
    return f"/api/ask/image/{name}"
