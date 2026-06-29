"""A股现金分红自动识别 → 摊薄成本(对齐券商口径)。

券商(招商等)显示的是摊薄成本: 持有期间每收到一次现金分红, 每股成本下调该次每股股息(含税)。
本模块拉东财分红送配数据, 算出某持仓"开仓日之后、已实施"的累计每股现金分红, 供成本计算扣减。

失败一律返回 0(不破成本计算): 数据源抖动时退回未摊薄成本, 不会让持仓盈亏算崩。
"""
from __future__ import annotations
import asyncio
import datetime as _dt
import time as _t

_cache: dict = {}          # code -> (rows, ts)
_TTL = 12 * 3600           # 分红方案很少变, 缓存 12h


def _today_cst() -> _dt.date:
    return (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()


def _fetch_dividends_sync(bare: str) -> list[dict]:
    """东财分红送配明细 → [{ex_date(date), per_share(每股现金,含税), progress}]。只留已实施的现金分红。"""
    import akshare as ak
    c = _cache.get(bare)
    if c and _t.time() - c[1] < _TTL:
        return c[0]
    out: list[dict] = []
    try:
        df = ak.stock_fhps_detail_em(symbol=bare)
        if df is not None and len(df):
            for _, r in df.iterrows():
                prog = str(r.get("方案进度") or "")
                if "实施" not in prog:                       # 只认已实施分配
                    continue
                ratio = r.get("现金分红-现金分红比例")          # 每 10 股派 X 元(含税)
                exd = str(r.get("除权除息日") or "")[:10]
                try:
                    per_share = float(ratio) / 10.0
                    exdate = _dt.date.fromisoformat(exd)
                except (TypeError, ValueError):
                    continue
                if per_share > 0:
                    out.append({"ex_date": exdate, "per_share": round(per_share, 4), "progress": prog})
    except Exception:
        return []                                            # 失败不缓存, 下次重试
    _cache[bare] = (out, _t.time())
    return out


async def cumulative_dividend_per_share(code: str, since_date: str | None) -> float:
    """开仓日 since_date 之后、且除权除息日已到(<=今天)的累计每股现金分红(含税)。
    用于摊薄成本: 摊薄成本 = 综合成本 - 本值。since_date 为空或解析失败 → 返 0。"""
    if not since_date:
        return 0.0
    bare = (code or "").split(".")[-1].lstrip("shz").strip()
    if not bare.isdigit():
        return 0.0
    try:
        since = _dt.date.fromisoformat(str(since_date)[:10])
    except (TypeError, ValueError):
        return 0.0
    today = _today_cst()
    rows = await asyncio.to_thread(_fetch_dividends_sync, bare)
    # 买在除权除息日之前(ex_date > 开仓日)才吃得到这次分红; ex_date 已到才生效
    total = sum(r["per_share"] for r in rows if since < r["ex_date"] <= today)
    return round(total, 4)


async def dilute_state(code: str, st: dict) -> dict:
    """对 compute_position_state 结果做分红摊薄(对齐券商): 按当前持仓段开仓日之后累计每股现金分红下调 cost_price。
    原地改 st 并返回; 拿不到分红/无持仓时原样返回。"""
    if not st or float(st.get("shares") or 0) <= 0:
        return st
    lots = st.get("lots") or []
    open_date = min((l.get("trade_date") for l in lots if l.get("trade_date")), default=None)
    div_ps = await cumulative_dividend_per_share(code, open_date)
    if div_ps > 0:
        raw = st.get("cost_price_raw")
        base = raw if raw is not None else st.get("cost_price")
        if base is not None:
            st["cost_price_raw"] = round(base, 4)
            st["cost_price"] = round(max(0.0, base - div_ps), 4)
        fc = st.get("fifo_cost_price")
        if fc is not None:
            st["fifo_cost_price"] = round(max(0.0, fc - div_ps), 4)
        st["div_per_share"] = div_ps
    return st
