"""开盘啦板块竞价异动(登录态): 早盘集合竞价(9:15-9:25)阶段的板块异动 + 领涨个股。

这是**实时/当日**信号: 集合竞价里哪些板块被资金抢筹、对应龙头个股的竞价换手/涨幅/主力净额。
用途是开盘前后判断当天资金主攻方向 —— 竞价异动板块往往是全天最强主线的先手。

接口: c=StockBidYiDong / a=GetBKJJ_W36, 走实时行情 host(apphq)。
  响应: {Day, State, List1, List2, List3}, 每个 List 是"板块行"数组, 行=字符串数组:
    [0]=代码 [1]=名称 [2]=竞价换手 [3]=竞价涨幅(%) [5]=竞价主力净额(元)
  三段: List1 今日新增竞价异动 / List2 昨日爆发板块延续异动 / List3 其他异动板块

**只有实时、没有历史**: 该接口的历史 host 已下线, 传 Day 也被实时 host 忽略,
永远返回当日。数据只在集合竞价前后(约 9:15-9:31)才有, 其余时段 List 全空("暂无数据")——
这不是 Token 失效, 是这个功能本身按交易时段产出。登录态(用户自己账号)走 kaipanla_auth。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from services.kaipanla_auth import call, KplAuthError

_CST = timezone(timedelta(hours=8))

_SECTIONS = [("List1", "今日新增竞价异动"),
             ("List2", "昨日爆发板块延续异动"),
             ("List3", "其他异动板块")]


def _yi(v) -> float | None:
    try:
        return round(float(v) / 1e8, 2)
    except (TypeError, ValueError):
        return None


def _num(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _row(r: list) -> dict | None:
    """一行板块 → 精简结构。字段按接口列位取, 越界安全返回可得部分。"""
    if not isinstance(r, list) or len(r) < 2:
        return None
    def at(i):
        return r[i] if len(r) > i else None
    return {"代码": r[0], "名称": r[1],
            "竞价换手": at(2),
            "竞价涨幅": _num(at(3)),
            "竞价主力净额亿": _yi(at(5))}


def _in_bidding_window(day_str: str) -> bool:
    """当日 9:15-9:31(集合竞价产出窗口)。窗口外空数据属正常, 不是登录态问题。"""
    now = datetime.now(tz=_CST)
    if now.strftime("%Y-%m-%d") != day_str:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 9 * 60 + 31


async def plate_bidding() -> dict:
    """当日板块竞价异动(开盘啦, 实时)。无历史 —— 接口只产出当日集合竞价数据。

    没配登录态 / Token 失效 → {error, need_login} 让上层提示重新登录, 不静默空。
    非竞价时段(数据未产出)→ 上榜=False + note 说明是时段问题而非故障。
    """
    try:
        j = await call("apphq", {"c": "StockBidYiDong", "a": "GetBKJJ_W36"})
    except KplAuthError as e:
        return {"error": str(e), "need_login": True}
    if not isinstance(j, dict) or str(j.get("errcode", "0")) != "0":
        code = (j or {}).get("errcode") if isinstance(j, dict) else None
        return {"error": "开盘啦竞价异动未返回", "raw_errcode": code}

    day = j.get("Day") or datetime.now(tz=_CST).strftime("%Y-%m-%d")
    sections = []
    total = 0
    for key, title in _SECTIONS:
        rows = [d for d in (_row(r) for r in (j.get(key) or [])) if d]
        total += len(rows)
        sections.append({"分组": title, "板块数": len(rows), "板块": rows})

    if total == 0:
        computing = _in_bidding_window(day)
        return {
            "date": day, "有数据": False,
            "note": ("正在集合竞价, 竞价异动数据计算中(约 9:25 后陆续产出), 稍后再取。"
                     if computing else
                     f"{day} 当前无竞价异动数据。该功能只在早盘集合竞价前后(约 9:15-9:31)产出当日数据, "
                     "且**只有当日、没有历史**。非该时段取到空属正常, 不是登录态失效。"),
        }
    return {
        "date": day, "有数据": True,
        "分组": sections,
        "note": "开盘啦板块竞价异动: 集合竞价阶段被资金抢筹的板块及领涨个股(竞价换手/涨幅/主力净额)。"
                "'今日新增'=当天新起的异动, '昨日爆发延续'=昨日强势今日竞价继续, '其他'=次级异动。"
                "金额单位元已折亿。只有当日数据、无历史。这是已披露客观数据, 不构成买卖建议。",
    }
