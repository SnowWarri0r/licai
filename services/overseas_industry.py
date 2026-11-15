"""港美股 → 申万二级行业, 供穿透敞口按行业归并。

A 股的行业表是申万二级(「半导体」「软件开发」…), 港美股没有这一套。东财 F10 给的是 GICS:
美股的所属行业就是 GICS 子行业中文名(英伟达 = 半导体产品), 港股的行业分类按
「大类-行业组-行业-子行业」排列, 取最后一级(中芯国际 = 半导体产品)。两边口径一致, 再用下表
接到申万二级上, 台积电与 A 股半导体才会并成同一堆。

映射只收一一对应的子行业。跨好几个申万行业的(电脑硬件与周边: 苹果是消费电子, 戴尔是计算机
设备; 多品类零售: 电商与百货混在一起; 互动媒体与服务)不映射, 归入「海外·<子行业>」, 名字照样
给出来, 只是不跟 A 股并堆。归错堆会凭空造出同源, 比少并一堆更误导。
"""
from __future__ import annotations

import threading

# GICS 子行业(东财中文名) → 申万二级(与 etf_xray.industry_map 的取值一致)
GICS_TO_SW: dict[str, str] = {
    "半导体产品": "半导体",
    "半导体材料与设备": "半导体",
    "半导体设备": "半导体",
    "系统软件": "软件开发",
    "应用软件": "软件开发",
    "信息科技咨询与其它服务": "IT服务Ⅱ",
    "数据处理与外包服务": "IT服务Ⅱ",
    "通信设备": "通信设备",
    "电子元件": "元件",
    "互动家庭娱乐": "游戏Ⅱ",
    "广告": "广告营销",
    "汽车制造商": "乘用车",
    "汽车零配件与设备": "汽车零部件",
    "综合性银行": "银行Ⅱ",
    "区域性银行": "银行Ⅱ",
    "人寿与健康保险": "保险Ⅱ",
    "财产与意外伤害保险": "保险Ⅱ",
    "多元化保险": "保险Ⅱ",
    "投资银行业与经纪业": "证券Ⅱ",
    "制药": "化学制药",
    "生物科技": "生物制品",
    "医疗保健设备": "医疗器械",
    "医疗保健用品": "医疗器械",
    "生命科学工具和服务": "医疗服务",
    "石油与天然气的勘探与生产": "油气开采Ⅱ",
    "综合性石油与天然气企业": "炼化及贸易",
    "石油与天然气的炼制和销售": "炼化及贸易",
    "酿酒商": "非白酒",
    "餐馆": "酒店餐饮",
    "酒店、度假村与豪华游轮": "酒店餐饮",
    "电力公用事业": "电力",
    "可再生电力": "电力",
    "服装、服饰与奢侈品": "服装家纺",
    "黄金": "贵金属",
    "贵金属与矿石": "贵金属",
    "铝": "工业金属",
    "铜": "工业金属",
    "煤与消费用燃料": "煤炭开采",
    "海运": "航运港口",
    "航空货运与物流": "物流",
    "航空公司": "航空机场",
}

_cache: dict[tuple[str, str], str | None] = {}
_lock = threading.Lock()


def _bare(code: str) -> str:
    """HK.00700 / us.nvda / 00700 → 00700 / NVDA"""
    return str(code or "").split(".")[-1].strip().upper()


def gics_sub(code: str, market: str) -> str | None:
    """取港美股的 GICS 子行业中文名; 查不到返回 None。结果按进程缓存(行业几乎不变)。"""
    mkt = str(market or "").upper()
    sym = _bare(code)
    if mkt not in ("HK", "US") or not sym:
        return None
    key = (mkt, sym)
    with _lock:
        if key in _cache:
            return _cache[key]
    sub = None
    try:
        from services import foreign_f10 as f
        if mkt == "US":
            sub = (f.us_profile_f10(sym) or {}).get("industry") or None
        else:
            p = f.hk_profile(sym.zfill(5)) or {}
            levels = [x for x in str(p.get("行业分类") or "").split("-") if x]
            sub = levels[-1] if levels else (p.get("industry") or None)
    except Exception:
        return None          # 网络抖动不缓存, 下次再查
    with _lock:
        _cache[key] = sub
    return sub


def to_sw(sub: str | None) -> str | None:
    return GICS_TO_SW.get(sub or "")


def label_for(code: str, market: str) -> tuple[str, str | None]:
    """(穿透用的行业名, GICS 子行业)。能映射的给申万二级; 不能映射的给「海外·<子行业>」;
    子行业都查不到的给「海外(<市场>·未归行业)」。"""
    sub = gics_sub(code, market)
    sw = to_sw(sub)
    if sw:
        return sw, sub
    if sub:
        return f"海外·{sub}", sub
    return f"海外({market or '?'}·未归行业)", None
