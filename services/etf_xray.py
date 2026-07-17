"""ETF 题材透视(避雷雷达): 用基金季报的真实成分股, 对照名称宣称的主题, 算"主题匹配权重"。

用途: 避雷"标榜红利/电器, 实际重仓别的"的挂羊头 ETF。
- 每个主题只对比规模最大的前 N 只(小规模 ETF 流动性/清盘风险另算, 不进对比池)
- 成分股行业用全 A 快照(EM clist f100)映射; 主题↔行业按包含+同义词表匹配
- 行业已知时只认行业, 股票名回避(名字挂羊头正是要抓的); 行业查不到才用名字兜底
- 宽基(科创50/沪深300…)与风格类(红利/低波/价值…)不适用行业口径, 明确标注只展示分布
数据 = 基金季报(滞后至上一季度末), 纯客观结构展示, 不构成任何买卖建议。
"""
from __future__ import annotations
import asyncio
import time

_cache: dict = {}
_TTL_UNIVERSE = 3600
_TTL_ANALYZE = 6 * 3600

_HOSTS = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]

# 主题 → 可匹配的行业/名称关键词(EM 二级行业口径); 包含匹配双向兜底
THEME_SYN: dict[str, list[str]] = {
    "半导体": ["半导体", "电子化学品"],
    "半导体设备": ["半导体"],
    "芯片": ["半导体"],
    "通信": ["通信设备", "通信服务"],
    "电器": ["白色家电", "黑色家电", "小家电", "厨卫电器", "家电零部件", "照明设备"],
    "家电": ["白色家电", "黑色家电", "小家电", "厨卫电器", "家电零部件", "照明设备"],
    "创新药": ["化学制药", "生物制品", "医疗服务"],
    "医药": ["化学制药", "生物制品", "中药", "医疗服务", "医药商业"],
    "医疗": ["医疗器械", "医疗服务"],
    "军工": ["航天装备", "航空装备", "地面兵装", "航海装备", "军工电子"],
    "国防军工": ["航天装备", "航空装备", "地面兵装", "航海装备", "军工电子"],
    "新能源": ["电池", "光伏设备", "风电设备", "能源金属", "电网设备"],
    "光伏": ["光伏设备"],
    "电池": ["电池", "能源金属"],
    "白酒": ["白酒"],
    "酒": ["白酒", "非白酒", "啤酒"],
    "食品饮料": ["白酒", "非白酒", "饮料乳品", "食品加工", "调味发酵品", "休闲食品"],
    "银行": ["银行"],
    "证券": ["证券", "多元金融"],
    "券商": ["证券"],
    "保险": ["保险"],
    "地产": ["房地产开发", "房地产服务"],
    "基建": ["基础建设", "专业工程", "房屋建设"],
    "煤炭": ["煤炭开采", "焦炭"],
    "钢铁": ["普钢", "特钢"],
    "有色": ["工业金属", "贵金属", "小金属", "能源金属", "金属新材料"],
    "游戏": ["游戏"],
    "传媒": ["游戏", "影视院线", "广告营销", "出版", "电视广播", "数字媒体"],
    "计算机": ["软件开发", "IT服务", "计算机设备"],
    "软件": ["软件开发", "IT服务"],
    "人工智能": ["软件开发", "IT服务", "计算机设备", "半导体", "通信设备"],
    "机器人": ["自动化设备", "通用设备", "机器人"],
    "汽车": ["乘用车", "商用车", "汽车零部件", "汽车服务"],
    "农业": ["种植业", "养殖业", "农产品加工", "饲料", "渔业", "农业综合"],
    "环保": ["环境治理", "环保设备"],
    "电力": ["电力"],
    "石油": ["炼化及贸易", "油气开采", "油服工程"],
}
# 宽基/指数类: 行业口径不适用
_WIDE_KW = ("科创50", "科创100", "科创板", "创业板", "创业50", "双创", "沪深300", "中证", "上证",
            "深证", "A50", "A500", "500", "1000", "2000", "全指", "国证", "50", "180", "380")
# 风格类: 按因子选股, 行业天然分散
_STYLE_KW = ("红利", "股息", "低波", "价值", "成长", "质量", "动量", "自由现金流", "基本面")
# 跨境/商品/债券: 成分不在 A 股行业表里
_XB_KW = ("恒生", "港股", "纳斯达克", "标普", "日经", "德国", "法国", "美国", "东南亚", "沙特",
          "黄金", "白银", "原油", "豆粕", "有色金属期货", "债", "货币", "添益", "日利")


def _clist(fs: str, fields: str, pages: int = 20) -> list[dict]:
    import requests
    s = requests.Session(); s.trust_env = False
    out = []
    for pn in range(1, pages + 1):
        got = None
        for h in _HOSTS:
            try:
                d = s.get(f"https://{h}/api/qt/clist/get",
                          params={"pn": str(pn), "pz": "100", "po": "1", "np": "1",
                                  "fltt": "2", "invt": "2", "fid": "f20", "fs": fs, "fields": fields},
                          timeout=8).json().get("data")
                if d and d.get("diff"):
                    got = d["diff"]; break
            except Exception:
                continue
        if not got:
            break
        out.extend(got)
        if len(got) < 100:
            break
    return out


def etf_universe() -> list[dict]:
    """全市场场内 ETF: code/name/规模亿/成交额亿(EM clist, 缓存1h)。"""
    c = _cache.get("universe")
    if c and time.time() - c[1] < _TTL_UNIVERSE:
        return c[0]
    rows = _clist("b:MK0021,b:MK0022,b:MK0023,b:MK0024", "f12,f14,f20,f6,f3")
    out = []
    for x in rows:
        try:
            out.append({"code": str(x["f12"]), "name": str(x["f14"]),
                        "规模亿": round(float(x.get("f20") or 0) / 1e8, 1),
                        "成交额亿": round(float(x.get("f6") or 0) / 1e8, 2),
                        "今日%": x.get("f3")})
        except (TypeError, ValueError, KeyError):
            continue
    if out:
        _cache["universe"] = (out, time.time())
    return out


def industry_map() -> dict:
    """全 A 股 code → (名称, 行业, 市值亿)。缓存1h。"""
    c = _cache.get("indmap")
    if c and time.time() - c[1] < _TTL_UNIVERSE:
        return c[0]
    rows = _clist("m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81", "f12,f14,f100,f20", pages=60)
    m = {}
    for x in rows:
        code = str(x.get("f12") or "")
        if code:
            try:
                cap = round(float(x.get("f20") or 0) / 1e8, 0)
            except (TypeError, ValueError):
                cap = 0
            m[code] = (str(x.get("f14") or ""), str(x.get("f100") or ""), cap)
    if m:
        _cache["indmap"] = (m, time.time())
    return m


def _fetch_holdings_sync(code: str) -> tuple[list[dict], str]:
    """基金季报股票持仓(天天基金 fundf10 直连)。返回 (rows, 季度标签); 拉不到返回 ([], "")。

    2026-07 起该接口必须带 Referer, 否则返回反爬页(akshare 版未带, 解析炸), 故自行请求。
    """
    import re
    from datetime import date
    from io import StringIO

    import pandas as pd
    import requests
    s = requests.Session()
    s.trust_env = False
    for year in (date.today().year, date.today().year - 1):
        for attempt in range(3):
            try:
                r = s.get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                          params={"type": "jjcc", "code": code, "topline": "10000",
                                  "year": str(year), "month": ""},
                          headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"},
                          timeout=10)
                r.encoding = "utf-8"
                m = re.search(r'content:"(.*?)",arryear', r.text, re.S)
                content = m.group(1) if m else ""
                if not content or "暂无数据" in content:
                    break
                quarters = re.findall(r"(\d{4}年\d季度)股票投资明细", content)
                tables = pd.read_html(StringIO(content), converters={"股票代码": str})
                if not tables:
                    break
                df = tables[0]                      # 第一张表 = 该年最新一期
                wcol = next((c for c in df.columns if "占净值" in str(c)), None)
                if wcol is None:
                    break
                rows = []
                for _, x in df.iterrows():
                    try:
                        w = float(str(x[wcol]).rstrip("%"))
                    except (TypeError, ValueError):
                        continue
                    rows.append({"code": str(x["股票代码"]), "name": str(x["股票名称"]), "weight": w})
                if rows:
                    return rows, (quarters[0] if quarters else str(year))
                break
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        # 该年份拿不到 → 试上一年
    return [], ""


def classify_theme(name: str) -> tuple[str, str]:
    """基金名 → (主题词, 主题类型)。类型: 行业主题 / 宽基指数 / 风格策略 / 跨境商品债。"""
    from services.external_assets import fund_theme_word
    theme = fund_theme_word(name) or name
    if any(k in name for k in _XB_KW):
        return theme, "跨境商品债"
    if any(k in name for k in _STYLE_KW):
        return theme, "风格策略"
    if any(k in name for k in _WIDE_KW):
        return theme, "宽基指数"
    return theme, "行业主题"


def _theme_kws(theme: str) -> list[str]:
    """主题词 → 可匹配关键词: 精确键 + 被主题词包含的键(科创创新药→创新药)。"""
    kws = list(THEME_SYN.get(theme, []))
    for k, v in THEME_SYN.items():
        if k != theme and k in theme:
            kws.extend(v)
    return kws


def _matches(theme: str, industry: str, stock_name: str) -> bool:
    """行业已知时行业说了算(名字挂羊头正是要抓的对象, 股票名回避);
    行业查不到(港股/北交所等不在 A 股行业表)才用股票名兜底。"""
    kws = _theme_kws(theme)
    if industry and industry != "非A股/未知":
        for k in kws:
            if k and (k in industry or industry in k):
                return True
        return bool(theme and (theme in industry or industry in theme))
    for k in kws:
        if k and k in stock_name:
            return True
    return bool(theme and theme in stock_name)


def analyze_etf(code: str, name: str = "", size_yi: float | None = None) -> dict:
    """单只 ETF 透视: 最新季报成分 → 行业分布 + 主题匹配权重 + 警示。同步(外层 to_thread)。"""
    ck = f"xray_{code}"
    c = _cache.get(ck)
    if c and time.time() - c[1] < _TTL_ANALYZE:
        return c[0]
    if not name or size_yi is None:
        u = {x["code"]: x for x in etf_universe()}
        info = u.get(code) or {}
        name = name or info.get("name") or code
        size_yi = size_yi if size_yi is not None else info.get("规模亿")
    theme, ttype = classify_theme(name)
    holdings, quarter = _fetch_holdings_sync(code)
    out: dict = {"code": code, "name": name, "规模亿": size_yi,
                 "主题": theme, "主题类型": ttype, "季报": quarter}
    if not holdings:
        out["note"] = "无股票持仓数据(商品/债券/货币类, 或季报未披露)"
        _cache[ck] = (out, time.time())
        return out
    imap = industry_map()
    ind_weight: dict[str, float] = {}
    matched_w = 0.0
    total_w = 0.0
    top = []
    for h in holdings:
        nm, ind, cap = imap.get(h["code"], (h["name"], "", 0))
        ind = ind or "非A股/未知"
        w = h["weight"]
        total_w += w
        ind_weight[ind] = ind_weight.get(ind, 0) + w
        ok = _matches(theme, ind, h["name"]) if ttype == "行业主题" else None
        if ok:
            matched_w += w
        top.append({"code": h["code"], "name": h["name"], "行业": ind,
                    "权重%": round(w, 2), "市值亿": cap, "贴题": ok})
    top.sort(key=lambda x: -x["权重%"])
    dist = sorted(({"行业": k, "权重%": round(v, 2)} for k, v in ind_weight.items()),
                  key=lambda x: -x["权重%"])
    out.update({"前十大": top[:10], "行业分布": dist[:8],
                "股票仓位%": round(total_w, 1)})
    if ttype == "行业主题":
        purity = round(matched_w / total_w * 100, 1) if total_w > 0 else None
        out["主题匹配权重%"] = purity
        out["警示"] = ("贴题" if purity is None or purity >= 70
                       else "有偏离" if purity >= 50 else "偏离显著")
        # 主题比行业分类更细(半导体设备/创新药…): 只能验证到大类, 说清天花板
        kws = [k for k in dict.fromkeys(_theme_kws(theme)) if k]
        if kws and not any(theme in k for k in kws):
            out["note"] = (f"「{theme}」比季报行业分类更细: 只验证了成分股属于"
                           f"{'、'.join(kws[:4])}大类, 大类内部更细的方向验证不了")
    else:
        out["主题匹配权重%"] = None
        out["警示"] = {"宽基指数": "宽基", "风格策略": "风格", "跨境商品债": "跨境/商品"}[ttype]
        out["note"] = {
            "宽基指数": "宽基指数本来就覆盖多行业, 没有单一主题要核对, 行业分布看个结构就行",
            "风格策略": "风格类按因子选股(红利/低波等), 行业分散是正常的, 看分布即可",
            "跨境商品债": "跨境/商品/债券类成分不在 A 股行业表里, 算不了贴题度, 行业分布仅供参考",
        }[ttype]
    _cache[ck] = (out, time.time())
    return out


async def theme_scan(theme: str, top: int = 5) -> dict:
    """按主题找规模最大的前 N 只 ETF 并逐只透视(避雷: 同主题里挑没挂羊头的)。"""
    theme = (theme or "").strip()
    if not theme:
        return {"error": "主题为空"}
    uni = await asyncio.to_thread(etf_universe)
    if not uni:
        return {"error": "ETF 列表暂不可达(东财抖动)"}
    hits = [x for x in uni if theme in x["name"]]
    hits.sort(key=lambda x: -(x["规模亿"] or 0))
    picked = hits[:top]
    if not picked:
        return {"error": f"没找到名称含'{theme}'的场内 ETF", "theme": theme}
    sem = asyncio.Semaphore(3)

    async def _one(x):
        async with sem:
            return await asyncio.to_thread(analyze_etf, x["code"], x["name"], x["规模亿"])

    rows = await asyncio.gather(*[_one(x) for x in picked], return_exceptions=True)
    rows = [r for r in rows if isinstance(r, dict)]
    return {"theme": theme, "总候选": len(hits), "rows": rows,
            "note": f"名称含'{theme}'的 ETF 共 {len(hits)} 只, 只对比规模最大的 {len(rows)} 只"
                    "(小规模 ETF 流动性/清盘风险另算)。数据=基金季报(滞后), 纯客观结构, 不构成买卖建议。"}


async def my_etf_scan() -> dict:
    """我的在持场内 ETF 逐只透视。"""
    from database import list_external_assets
    from services.external_assets import _is_onchain_etf
    held = []
    for x in await list_external_assets():
        code = str(x.get("code") or "")
        if x.get("asset_type") == "FUND" and _is_onchain_etf(code) and float(x.get("shares") or 0) > 0:
            held.append((code, x.get("name") or code))
    if not held:
        return {"rows": [], "note": "当前无在持场内 ETF"}
    sem = asyncio.Semaphore(3)

    async def _one(code, name):
        async with sem:
            return await asyncio.to_thread(analyze_etf, code, name)

    rows = await asyncio.gather(*[_one(c, n) for c, n in held], return_exceptions=True)
    return {"rows": [r for r in rows if isinstance(r, dict)],
            "note": "在持场内 ETF 的季报成分透视。数据滞后至上一季度末, 纯客观结构, 不构成买卖建议。"}
