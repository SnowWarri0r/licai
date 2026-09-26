"""分析插件(analyzer)挂载口。

与 provider(扩展数据源, 一次只激活一个)不同, analyzer 是**在已有行情上做计算**的插件:
输入某只股票的前复权日K, 输出对这段K线的结构化解读。可以同时装多个, 互不影响。

发现方式: Python entry point 组 `licai.analyzers`, 值指向一个类, 约定:
    class Analyzer:
        name: str            # 唯一标识
        display_name: str    # 界面上的标题
        min_bars: int        # 至少需要多少根日K
        context_symbols: list[str]   # 可选: 还需要哪些指数日K(如 "sh000852"), 路由取好经 context 传入
        def analyze(self, code, bars, stock_name="", live_last=False, context=None) -> dict: ...
  bars = [{date, open, high, low, close, volume}] 按日期升序;
  live_last = 末根是盘中未收盘的 bar(成交量只走了一部分, 插件应自行决定是否参与计算)。
  返回 dict 至少含 available: bool; 其余字段路由原样透传。前端只认下面这套**通用展示结构**
  (插件想怎么解读都行, 但要翻译成这几个字段才会被渲染; 插件自有字段前端忽略):
    headline:  {label, value, tone, title}      头部小标签(tone: pos|neg|muted|accent; A股 pos=红 neg=绿)
    chips:     [headline 同结构, ...]            可选, 多个头部标签(有则替代 headline)
    items:     [{title, desc, badge, badge_tone, stats: [[名称, 值, tone], ...]}]   当前命中的条目
    timeline:  [{date, labels: [str], tones: [str]}]                                近期出现过的日子
    state_text / pending_note / footnote: str                                       状态行 / 盘中提示 / 脚注

可选的复盘接口(买点回看 services/entry_review.py 用; 没实现的插件不参与):
    def review_points(self, code, bars, days, stock_name="", context=None) -> dict
        对每个买入日 D 给出 D 前一交易日收盘时的判定: {D: {signal_date, patterns: [形态id], risk_decile: int|None}}
    def review_catalog(self) -> dict
        {patterns: {形态id: {name, definition, grade, n, excess_20d_pct, win_rate_20d, adj_excess_20d_pct, tone}},
         risk: {deciles: [{decile, crash_rate, median_excess_pct, ...}], base_crash, base_rally} | None, note}

没装任何 analyzer = 列表为空, 路由返回空数组, 前端不渲染任何东西。
设 env LICAI_ANALYZERS=off 可整体关闭(排查问题时用)。单个插件报错只影响它自己。
"""
from __future__ import annotations

import logging
import os

_EP_GROUP = "licai.analyzers"
_instances: dict | None = None
_errors: dict[str, str] = {}
log = logging.getLogger(__name__)


def _entry_points() -> list:
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return []
    try:
        return list(entry_points(group=_EP_GROUP))
    except TypeError:
        return list((entry_points() or {}).get(_EP_GROUP, []))


def get_analyzers() -> dict:
    """name → 实例。首次调用时加载, 之后复用(插件无状态, 校准数据由插件自己缓存)。"""
    global _instances
    if os.environ.get("LICAI_ANALYZERS", "").lower() in ("off", "0", "false"):
        return {}
    if _instances is None:
        _instances = {}
        for ep in _entry_points():
            try:
                obj = ep.load()()
                _instances[getattr(obj, "name", ep.name)] = obj
            except Exception as e:  # noqa: BLE001  单个插件坏了不连累其它
                _errors[ep.name] = f"{type(e).__name__}: {e}"
                log.warning("analyzer %s 加载失败: %s", ep.name, e)
    return _instances


def status() -> list[dict]:
    got = get_analyzers()
    out = [{"name": n, "display_name": getattr(a, "display_name", n), "loaded": True} for n, a in got.items()]
    out += [{"name": n, "loaded": False, "error": err} for n, err in _errors.items()]
    return out


def bars_needed(default: int = 0) -> int:
    """所有已装插件里最大的 min_bars —— 路由据此决定取多少根日K。"""
    return max([int(getattr(a, "min_bars", 0) or 0) for a in get_analyzers().values()] + [default])


def context_symbols() -> list[str]:
    """所有已装插件声明需要的指数代码(去重)。"""
    out = []
    for a in get_analyzers().values():
        for s in getattr(a, "context_symbols", None) or []:
            if s not in out:
                out.append(s)
    return out


def run_all(code: str, bars: list[dict], stock_name: str = "", live_last: bool = False,
            context: dict | None = None) -> list[dict]:
    results = []
    for name, a in get_analyzers().items():
        try:
            if getattr(a, "context_symbols", None):
                r = a.analyze(code, bars, stock_name=stock_name, live_last=live_last, context=context or {})
            else:
                r = a.analyze(code, bars, stock_name=stock_name, live_last=live_last)
        except Exception as e:  # noqa: BLE001
            r = {"available": False, "note": f"计算出错: {type(e).__name__}: {e}"}
        r.setdefault("analyzer", name)
        r.setdefault("display_name", getattr(a, "display_name", name))
        results.append(r)
    return results


def review_all(code: str, bars: list[dict], days: list[str], stock_name: str = "",
               context: dict | None = None) -> dict:
    """{插件名: review_points 的结果}。没实现复盘接口或出错的插件跳过。"""
    out = {}
    for name, a in get_analyzers().items():
        fn = getattr(a, "review_points", None)
        if not fn:
            continue
        try:
            out[name] = fn(code, bars, days, stock_name=stock_name, context=context or {}) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("analyzer %s review_points(%s) 出错: %s", name, code, e)
    return out


def review_catalogs() -> dict:
    """{插件名: review_catalog()}。"""
    out = {}
    for name, a in get_analyzers().items():
        fn = getattr(a, "review_catalog", None)
        if not fn:
            continue
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            log.warning("analyzer %s review_catalog 出错: %s", name, e)
    return out
