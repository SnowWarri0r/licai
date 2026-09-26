"""每日收盘 · 大盘全景复盘 (客观编排, 非买卖信号)。

把已有的几份盘后数据拼成"今天全市场发生了什么"一段:
  情绪与广度(涨停跌停/炸板率/涨跌家数/赚钱效应) + 今日主线(板块/概念扎堆/风格) +
  领涨与吸金样本 + 龙虎榜今日机构净买/净卖 top。
我的持仓/自选那一段仍走 eod_summary + 前端逐票挂 恐慌/机构 标记, 这里只管大盘。

数据全部复用现成服务, 不新拉源: market_sentiment / scan_strong_stocks / inst_flow。
纯客观呈现, 不预测方向、不构成买卖建议 (见 [[stock_no_recs]] [[stock_boundary]])。
"""
from __future__ import annotations
import asyncio
import datetime


async def build_market_review() -> dict:
    """组装大盘全景。任一子块失败只丢那块, 不整体报错。"""
    senti: dict = {}
    strong: dict = {}
    inst: dict = {}

    async def _senti():
        try:
            from api.market_routes import market_sentiment
            return await market_sentiment()
        except Exception as e:
            return {"__err__": str(e)}

    async def _strong():
        try:
            from services.market_review import scan_strong_stocks
            return await asyncio.to_thread(scan_strong_stocks)
        except Exception as e:
            return {"__err__": str(e)}

    async def _inst():
        try:
            from services.inst_flow import inst_flow
            return await inst_flow(top=6)
        except Exception as e:
            return {"__err__": str(e)}

    senti, strong, inst = await asyncio.gather(_senti(), _strong(), _inst())

    today = datetime.date.today()
    out: dict = {
        "date": today.isoformat(),
        "weekday": "一二三四五六日"[today.weekday()],
    }

    # 情绪与广度
    if senti and "__err__" not in senti:
        b = senti.get("breadth") or {}
        out["情绪"] = {
            "涨停": senti.get("n_zt"), "跌停": senti.get("n_dt"),
            "炸板率%": senti.get("zbl_rate"),
            "上涨家数": b.get("上涨"), "下跌家数": b.get("下跌"),
            "赚钱效应": senti.get("mood"),
            "最高连板": senti.get("max_lianban") or senti.get("连板高度"),
        }

    # 今日主线 + 风格 + 领涨吸金(复用强势股画像)
    if strong and "__err__" not in strong:
        out["主线"] = {
            "涨停数": strong.get("涨停数"),
            "大涨数(≥5%)": strong.get("大涨数(≥5%)"),
            "板块扎堆": strong.get("板块扎堆"),
            "概念扎堆": strong.get("概念扎堆"),
            "风格": strong.get("风格"),
        }
        out["领涨样本"] = strong.get("领涨样本")
        out["吸金榜"] = strong.get("吸金榜(成交额前)")
    elif strong.get("__err__"):
        out["主线_note"] = "主线数据暂不可达(东财抖动), 稍后重试"

    # 龙虎榜今日机构净买/净卖
    if inst and "__err__" not in inst:
        out["机构龙虎榜"] = {
            "window_days": inst.get("window_days"),
            "净买top": inst.get("net_buy", [])[:6],
            "净卖top": inst.get("net_sell", [])[:6],
        }

    out["note"] = ("大盘全景=今天全市场客观定格(情绪广度+主线板块+领涨吸金+机构龙虎榜)。"
                   "主线只呈现资金扎堆在哪个方向, 不判断对错、不预测明天、不构成买卖建议。"
                   "机构龙虎榜为上榜日抽样、滞后, 非全量持仓。")
    return out
