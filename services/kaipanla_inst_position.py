"""开盘啦机构增仓榜(登录态): 季报口径下机构对各行业板块的增仓/减仓。

这是别处(东财/akshare)拿不到的开盘啦独家口径: 按最新季报持仓, 机构在哪些行业板块**增仓**
(加仓)、哪些**减仓**, 以及机构持仓市值/占流通比。区别于 inst_flow(龙虎榜机构专用席位=短线
盘口), 这里是**中线的季度持仓变化**, 判断机构中长期在往哪些方向搬仓。

接口: c=ZhuLiChiCang / a=GGList_BXZJ, Type=1(行业板块) / Order / Date(季报日) / Index / st。
响应: {List:[行], Sum_ZCJE(增仓金额合计), Sum_ZCC(持仓合计), Date, DateList:[可选季报日]}。
行=字符串数组, 列(按合计口径核对): [0]代码 [1]名称 [2]增仓金额(元,带符号) [4]机构持仓市值(元)
[6]机构持仓占流通% [7]板块流通市值(元)。金额单位元, 折亿。登录态走 kaipanla_auth。
"""
from __future__ import annotations

from services.kaipanla_auth import call, KplAuthError

_HOST = "apphis"
_TYPE_INDUSTRY = "1"   # 行业板块维度


def _yi(v) -> float | None:
    """元 → 亿, 保留符号(增仓金额可负=减仓)。"""
    try:
        return round(float(v) / 1e8, 2)
    except (TypeError, ValueError):
        return None


def _pct(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _row(r: list) -> dict | None:
    if not isinstance(r, list) or len(r) < 3:
        return None
    def at(i):
        return r[i] if len(r) > i else None
    return {"代码": r[0], "名称": r[1],
            "增仓金额亿": _yi(at(2)),
            "机构持仓市值亿": _yi(at(4)),
            "持仓占流通%": _pct(at(6))}


async def _fetch(date: str | None):
    params = {"c": "ZhuLiChiCang", "a": "GGList_BXZJ", "Type": _TYPE_INDUSTRY,
              "Order": "0", "Index": "0", "st": "300"}
    if date:
        params["Date"] = date
    return await call(_HOST, params)


async def inst_position(date: str | None = None, top: int = 15) -> dict:
    """机构增仓/减仓榜(开盘啦, 行业板块级, 最新季报口径)。

    date 可传季报日(YYYY-MM-DD), 默认自动取最新可用季报。返回按增仓金额排序的
    增仓榜(前 top)+ 减仓榜(前 top)。没配登录态 / Token 失效 → {error, need_login}。
    """
    try:
        j = await _fetch(date)
        # date 不是有效季报日时 List 会空, 但会带 DateList(可选季报日) → 回退到最新季报重取
        if isinstance(j, dict) and not (j.get("List") or []):
            dl = j.get("DateList") or []
            if dl and (not date or date not in dl):
                j = await _fetch(dl[0])
    except KplAuthError as e:
        return {"error": str(e), "need_login": True}
    if not isinstance(j, dict) or str(j.get("errcode", "0")) != "0":
        code = (j or {}).get("errcode") if isinstance(j, dict) else None
        return {"error": "开盘啦机构增仓未返回", "raw_errcode": code}

    rows = [d for d in (_row(r) for r in (j.get("List") or [])) if d]
    if not rows:
        return {"date": j.get("Date"), "有数据": False,
                "可选季报日": (j.get("DateList") or [])[:8],
                "note": "该季报日无机构增仓数据, 换一个季报日(见 可选季报日)再取。"}

    ranked = [r for r in rows if r["增仓金额亿"] is not None]
    ranked.sort(key=lambda r: r["增仓金额亿"], reverse=True)
    increased = [r for r in ranked if r["增仓金额亿"] > 0][:top]
    decreased = [r for r in ranked if r["增仓金额亿"] < 0]
    decreased = sorted(decreased, key=lambda r: r["增仓金额亿"])[:top]
    return {
        "date": j.get("Date"), "有数据": True,
        "维度": "行业板块",
        "全市场机构增仓合计亿": _yi(j.get("Sum_ZCJE")),
        "增仓榜": increased,
        "减仓榜": decreased,
        "可选季报日": (j.get("DateList") or [])[:8],
        "note": "开盘啦机构增仓榜(行业板块, 最新季报持仓口径): 增仓金额=机构本季相对上季持仓的净增减(带符号, 正=加仓)。"
                "反映机构中线搬仓方向, 与 get_inst_flow(龙虎榜机构席位=短线)互补。"
                "季报滞后, 是已披露的客观持仓数据, 不构成买卖建议。",
    }
