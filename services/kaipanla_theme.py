"""开盘啦月度热门题材榜(登录态): 本月资金最热的题材/板块排名。

补我们缺的"题材热度"维度 —— limit_up_pool 只按当日涨停给个股贴题材标签, 没有"这个月整体
哪些题材最热"的月度视角。用于判断当前市场主线题材、题材轮动到了哪一档。

接口: c=StockDataCenter / a=GetHotPlateByMonth, 走 apparticle host, 无参数。
响应: {list:[{title, code}, ...]}, 已按热度降序(title=题材名, code=板块码)。
登录态(用户自己账号)走 kaipanla_auth。
"""
from __future__ import annotations

from services.kaipanla_auth import call, KplAuthError


async def hot_themes() -> dict:
    """本月热门题材榜(开盘啦, 按热度降序)。

    没配登录态 / Token 失效 → {error, need_login}。
    """
    try:
        j = await call("apparticle", {"c": "StockDataCenter", "a": "GetHotPlateByMonth"})
    except KplAuthError as e:
        return {"error": str(e), "need_login": True}
    if not isinstance(j, dict) or str(j.get("errcode", "0")) != "0":
        code = (j or {}).get("errcode") if isinstance(j, dict) else None
        return {"error": "开盘啦月度热门题材未返回", "raw_errcode": code}

    rows = j.get("list") or []
    themes = []
    for i, it in enumerate(rows, 1):
        if isinstance(it, dict) and it.get("title"):
            themes.append({"排名": i, "题材": it.get("title"), "代码": it.get("code")})
    if not themes:
        return {"有数据": False, "note": "本月暂无热门题材数据。"}
    return {
        "有数据": True, "数量": len(themes), "题材榜": themes,
        "note": "开盘啦本月热门题材榜, 已按热度降序(排名越前=本月资金关注度越高)。"
                "反映当前市场主线题材, 可结合板块动能/涨停题材交叉看轮动。客观数据非买卖建议。",
    }
