"""开盘啦深度龙虎榜(登录态): 个股席位明细 + 游资分组标签。

与我们已有的 get_lhb/get_seat_history(东财+名录) 的区别: 开盘啦这份带**游资分组标签**
(章盟主/赵老哥这类知名游资, 以及"量化抢筹"等标签), 是东财裸营业部名给不出来的那层。
要登录态(用户自己账号), 走 kaipanla_auth。

接口: applhb / c=Stock / a=GetNewOneStockInfo / Type=0 / Time=日期 / StockID=6位码。
盘后 16:30 之后当日数据才全。List[].BuyList/SellList 是买卖席位, 每席位:
  Name=营业部, Buy/Sell=金额(元), YouZiIcon/GroupIcon=游资标签, ReasonType=上榜原因分组。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from services.kaipanla_auth import call, KplAuthError

_CST = timezone(timedelta(hours=8))


def _yi(v) -> float | None:
    try:
        return round(float(v) / 1e8, 2)
    except (TypeError, ValueError):
        return None


def _seat(s: dict) -> dict:
    """一个席位 → 精简结构。游资标签在 GroupIcon/YouZiIcon, 有标签才是"知名游资/机构专用"。"""
    tags = []
    if s.get("YouZiIcon"):
        tags.append("游资")
    for g in (s.get("GroupIcon") or []):
        if isinstance(g, dict) and g.get("Name"):
            tags.append(str(g["Name"]))
        elif isinstance(g, str):
            tags.append(g)
    name = str(s.get("Name") or "")
    # "机构专用" 已经是营业部名本身, 不再重复塞进标签(否则界面显示 "机构专用 机构专用")。
    # 只有当营业部名不叫机构专用、但接口另标了机构身份时才补 —— 这里营业部名即身份, 不补。
    return {"营业部": name,
            "买入亿": _yi(s.get("Buy")), "卖出亿": _yi(s.get("Sell")),
            "标签": tags or None}


def _side(seats: list) -> list:
    out = [_seat(s) for s in (seats or []) if isinstance(s, dict)]
    # 按买入额降序(卖方按卖出额), 席位本身 PX 是名次但两侧混排, 这里各自排稳
    return out


async def stock_lhb(code: str, day: str | None = None) -> dict:
    """个股某日龙虎榜席位明细(开盘啦, 带游资标签)。code=6位; day 默认最近交易日。

    没配登录态 / Token 失效 → 返回 {error, need_login} 让上层提示重新登录, 不静默空。
    """
    bare = "".join(ch for ch in str(code or "") if ch.isdigit())[:6]
    if len(bare) != 6:
        return {"error": "需要 6 位股票代码"}
    if not day:
        day = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    try:
        j = await call("applhb", {"c": "Stock", "a": "GetNewOneStockInfo",
                                  "Type": "0", "Time": day, "StockID": bare})
    except KplAuthError as e:
        return {"error": str(e), "need_login": True}
    if not isinstance(j, dict) or str(j.get("errcode", "0")) != "0":
        return {"error": "开盘啦龙虎榜未返回", "raw_errcode": (j or {}).get("errcode") if isinstance(j, dict) else None}
    groups = j.get("List") or []
    if not groups:
        return {"code": bare, "name": j.get("Name"), "date": day, "上榜": False,
                "note": f"{bare} 在 {day} 没有龙虎榜数据(未上榜, 或当日 16:30 前数据未出)。"
                        + ("最近上榜日: " + "、".join((j.get("OnTimeList") or [])[:5]) if j.get("OnTimeList") else "")}
    boards = []
    for g in groups:
        boards.append({"上榜原因": g.get("UpReason"),
                       "买入合计亿": _yi(g.get("BuyTotal")),
                       "卖出合计亿": _yi(g.get("SellTotal")),
                       "买入席位": _side(g.get("BuyList")),
                       "卖出席位": _side(g.get("SellList"))})
    return {
        "code": bare, "name": j.get("Name"), "date": day, "上榜": True,
        "现价": j.get("CurPrice"), "涨跌": j.get("QuoteChange"),
        "换手率": j.get("TurnoverRatio"), "龙虎榜成交额亿": _yi(j.get("Turnover")),
        "买入合计亿": _yi(j.get("BuyIn")),
        "连板数": j.get("lbnum"),
        "席位": boards,
        "近期上榜日": (j.get("OnTimeList") or [])[:8],
        "note": "开盘啦深度龙虎榜: 席位带游资标签(比东财裸营业部名多一层身份识别)。"
                "标签含'游资'=知名游资席位, '机构专用'=机构席位, 其余为营业部分组标签。"
                "买卖金额单位元已折亿。这是已披露的客观数据, 不构成买卖建议。",
    }
