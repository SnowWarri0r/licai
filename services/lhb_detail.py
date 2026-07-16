"""龙虎榜席位明细: 某股某上榜日的买卖前五营业部(名称/金额/占成交比) + 席位画像标签。

数据 = 交易所披露(东财转发, akshare stock_lhb_stock_detail_em)。
席位标签是近似画像: 机构专用/沪深股通是官方口径; "常见量化通道"来自公开常识的知名席位
小表(会漂移, 仅供参考); 其余席位不贴标签。纯客观数据, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time

_cache: dict = {}
_TTL = 6 * 3600

# 公开常识里高频出现的量化/机构通道席位(子串匹配, 保守小表, 宁缺勿滥)
_QUANT_SEATS = (
    "华鑫证券有限责任公司上海分公司",
    "中信证券股份有限公司上海分公司",
    "瑞银证券有限责任公司上海花园石桥路",
    "高盛(中国)证券有限责任公司上海浦东新区世纪大道",
    "摩根士丹利证券(中国)有限公司上海世纪大道",
    "中国国际金融股份有限公司上海分公司",
)


def seat_tag(name: str) -> str:
    n = (name or "").strip()
    if n == "机构专用":
        return "机构"
    if "沪股通" in n or "深股通" in n:
        return "北向"
    for q in _QUANT_SEATS:
        if q[:10] in n:
            return "常见量化通道"
    return ""


def _fetch_sync(code: str, date: str) -> dict:
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    d8 = str(date).replace("-", "")
    out = {"code": code, "date": f"{d8[:4]}-{d8[4:6]}-{d8[6:]}", "买入": [], "卖出": [], "上榜原因": ""}
    for flag, key, amt_col in (("买入", "买入", "买入金额"), ("卖出", "卖出", "卖出金额")):
        df = None
        for attempt in range(3):
            try:
                df = ak.stock_lhb_stock_detail_em(symbol=code, date=d8, flag=flag)
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        if df is None or not len(df):
            continue
        for _, r in df.iterrows():
            nm = str(r.get("交易营业部名称") or "").strip()
            try:
                amt = float(r.get(amt_col) or 0) / 1e4          # 万元
                pct = float(r.get(f"{amt_col}-占总成交比例") or 0)
            except (TypeError, ValueError):
                continue
            if not nm or amt <= 0:
                continue
            out[key].append({"席位": nm, "金额万": round(amt, 1),
                             "占成交%": round(pct * 100, 2) if pct < 1 else round(pct, 2),
                             "标签": seat_tag(nm)})
            if not out["上榜原因"]:
                out["上榜原因"] = str(r.get("类型") or "")[:40]
    out["买入总计万"] = round(sum(x["金额万"] for x in out["买入"]), 1)
    out["卖出总计万"] = round(sum(x["金额万"] for x in out["卖出"]), 1)
    return out


async def lhb_seat_detail(code: str, date: str) -> dict:
    """某股某日席位明细。当日未上榜 → 买卖为空 + note。缓存6h。"""
    ck = f"{code}_{str(date).replace('-', '')}"
    c = _cache.get(ck)
    if c and time.time() - c[1] < _TTL:
        return c[0]
    r = await asyncio.to_thread(_fetch_sync, code, date)
    if not r["买入"] and not r["卖出"]:
        r["note"] = "该日未上龙虎榜(涨跌幅/换手未触发披露条件), 无席位数据。"
    else:
        r["note"] = ("交易所披露的买卖前五席位。'常见量化通道'为公开常识近似画像(会漂移, 仅供参考);"
                     "无标签≠游资。纯客观数据, 不构成任何买卖建议。")
        _cache[ck] = (r, time.time())
    return r
