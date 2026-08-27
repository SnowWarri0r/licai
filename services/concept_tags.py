"""概念标签的清洗与聚合。

东财给每只票的概念串(clist f103)里, 真概念和"市场状态/指数成分/地域/汇率"标签是混在一起的。
实测中天科技挂了 47 个: 从「CPO概念/算力概念」一路到「长江三角/贬值受益/创投」。不清洗就聚合,
排在前面的会是「融资融券」「深股通」这种全市场都有的标签 —— 那对"今天什么在轮动"一点信息都
没有, 反而把 CPO 这种真主线挤下去。

清洗规则从 stock_agent 那份 F10 过滤器搬过来(同一套噪声, 两处用), 另外补了 clist 特有的:
  · 纯地域(长江三角/西部大开发/东北振兴…): 它说的是注册地, 不是这家做什么
  · 汇率(贬值受益/升值受益): 全行业通吃的宏观标签
  · 上市年龄(次新股): 属性不是题材, 榜单里已经有 is_new
留下来的都是"这家做什么/跟着哪条主线走"的标签 —— 包括 华为概念/英伟达概念 这类供应链概念,
它们恰恰是 A 股轮动的主要形式。
"""
from __future__ import annotations

# 市场状态 / 指数成分 / 风格规模 / 持股机构: 不是"这只票做什么的"
NOISE = {
    "题材股", "趋势股", "融资融券", "沪股通", "深股通", "标准普尔", "富时罗素", "机构重仓",
    "小盘成长", "小盘股", "中盘股", "大盘股", "白马股", "绩优股", "预盈预增", "MSCI中国",
    "上证180", "上证380", "上证50", "沪深300", "中证500", "创业板综", "深证成指",
    "东方财富热股", "央企改革", "国企改革", "央国企改革",
    # 风格/规模/持股标签: 它们不是"这只票做什么的", 却和真概念混在一条列表里,
    # 把定义性的那个(风华高科的 MLCC 排在第 16 个, 卡在 20 条上限边上)往后挤。
    "基金重仓", "深成500", "深证100R", "中小100", "AH股", "转债标的",
    "周期股", "百元股", "低价股", "证金持股", "汇金持股", "社保重仓", "养老金", "QFII重仓",
    "创业成份", "中证100", "科创50", "权重股",
    # clist 特有: 地域 / 汇率 / 上市年龄
    "长江三角", "珠江三角", "西部大开发", "东北振兴", "中部崛起", "海南自贸",
    "雄安新区", "京津冀", "粤港澳", "自由贸易港", "自贸区",
    "贬值受益", "升值受益", "次新股", "新股与新兴产业",
}
# 「成长/价值/风格」这类后缀一律当噪声: 中盘成长 / 大盘价值 / 科技风格 …
NOISE_KW = ["板块", "新高", "新低", "涨停", "跌停", "首板", "多板", "振幅", "换手",
            "昨日", "今日", "近期", "连板", "风格", "成长", "价值", "指数样本"]


def is_noise(name: str) -> bool:
    nm = (name or "").strip()
    if not nm or nm in NOISE:
        return True
    # 东财给指数成分的标签一律带下划线后缀(上证180_ / HS300_ / 央视50_), 拿它当判据
    # 比逐个枚举可靠 —— 指数名年年变, 枚举必漏
    if nm.endswith("_"):
        return True
    return any(k in nm for k in NOISE_KW)


def clean(names) -> list[str]:
    """去噪 + 去重保序。"""
    out, seen = [], set()
    for nm in (names or []):
        nm = (nm or "").strip()
        if not nm or nm in seen or is_noise(nm):
            continue
        seen.add(nm)
        out.append(nm)
    return out


def parse(raw: str) -> list[str]:
    """东财 f103 是逗号串。"""
    return clean(str(raw or "").split(","))


_LIMIT_NEAR = 99.7      # 涨停占比到这个数就算封板(报价跳动会差个零头)


def group_rows(rows: list[dict], key: str = "概念", top: int = 12, min_n: int = 2) -> list[dict]:
    """把一张榜单按概念/行业聚成堆: 几家上榜、合起来多少成交额、平均涨多少、谁在领。

    这是"轮动"要的视角 —— 单看榜单是 100 只票, 看不出钱堆在哪条线上。
    min_n=2: 只有一只票的标签不叫轮动, 那是个股故事。
    """
    buckets: dict = {}
    for r in rows:
        if key == "概念":
            names = r.get("概念") or []
        else:
            names = [r.get("行业")] if r.get("行业") else []
        for nm in names:
            b = buckets.setdefault(nm, {"name": nm, "n": 0, "amt": 0.0, "pct_sum": 0.0,
                                        "up_n": 0, "limit_n": 0, "rows": []})
            b["n"] += 1
            b["amt"] += float(r.get("成交额亿") or 0)
            b["pct_sum"] += float(r.get("pct") or 0)
            if float(r.get("pct") or 0) > 0:
                b["up_n"] += 1
            lp = r.get("涨停占比%")
            if lp is not None and lp >= _LIMIT_NEAR:
                b["limit_n"] += 1
            b["rows"].append(r)
    out = []
    for b in buckets.values():
        if b["n"] < min_n:
            continue
        tops = sorted(b["rows"], key=lambda r: -float(r.get("成交额亿") or 0))[:3]
        out.append({
            "name": b["name"], "n": b["n"], "amt_yi": round(b["amt"], 1),
            "avg_pct": round(b["pct_sum"] / b["n"], 2),
            "up_n": b["up_n"], "limit_n": b["limit_n"],
            "codes": [r.get("code") for r in b["rows"]],
            "tops": [{"code": t.get("code"), "name": t.get("name"), "pct": t.get("pct"),
                      "amt_yi": t.get("成交额亿")} for t in tops],
        })
    out.sort(key=lambda g: -g["amt_yi"])
    return _dedupe(out)[:top]


# Jaccard 到这个程度就当同一堆票的两个叫法。阈值是量出来的: 8-27 成交额榜里唯一一对真同义
# (通信技术42家 vs 5G概念37家, 交集35)是 0.80, 而下一对(CPO概念 vs 光通信模块)只有 0.57 ——
# 后者不该并, 「光通信模块」比「CPO」更具体, 并了就把信息磨平。0.75 卡在这条缝里。
_SAME_J = 0.75


def _dedupe(groups: list[dict]) -> list[dict]:
    """把"同一堆票的不同叫法"并成一行, 别名列出来。

    实测 8-27 的成交额榜前十里, 通信技术(42家)/5G概念(37)/CPO概念(25)/光通信模块(22) 说的
    基本是同一批光模块票 —— 四行占掉半个榜, 看着像四条主线, 其实是一条。用 Jaccard 判同名:
    ≥0.85 才并(存储芯片 23 家 vs 国产芯片 38 家 只有 0.6, 是两条线, 不能并)。
    保留成交额最大的那个当主名(它覆盖最全), 其余进 aliases —— 名字没被丢掉, 只是不再各占一行。
    """
    kept: list[dict] = []
    for g in groups:
        s = set(g["codes"])
        merged = False
        for k in kept:
            ks = set(k["codes"])
            inter = len(s & ks)
            if inter and inter / len(s | ks) >= _SAME_J:
                k.setdefault("aliases", []).append(g["name"])
                merged = True
                break
        if not merged:
            kept.append(g)
    return kept
