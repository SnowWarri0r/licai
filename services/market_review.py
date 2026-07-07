"""每日强势股扫描 → 市场画像(供 agent 复盘'今天什么风格的票在涨')。

思路: 不让 LLM 肉眼看 100 根 K线(贵/噪/不可复现), 而是用东财 clist 榜单一次拉全市场
涨幅榜 + 成交额榜(带行业 f100 / 概念 f103 / 换手 f8 / 量比 f10 / 市值 f20), 纯代码聚合成
结构化"市场画像": 涨停数、板块/概念扎堆、风格(大盘趋势 vs 小盘妖股)、领涨与吸金样本。
LLM 只把画像总结成人话 + 落到用户持仓, 便宜可复现。
"""
from __future__ import annotations
import re as _re
import time as _t
from collections import Counter

_cache: dict = {}
_TTL = 120  # 盘中 2 分钟

_FS_ALL_A = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:7,m:1 t:3"   # 沪深A股(不含北交所/B股)
_FIELDS = "f2,f3,f6,f8,f10,f12,f14,f20,f100,f103"
_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com", "1.push2.eastmoney.com"]


def _is_new_stock(name: str) -> bool:
    """N=上市首日 / C=次日至第5日: 注册制下前5个交易日无涨跌幅限制, 涨停占比对它们无意义。"""
    return bool(_re.match(r"^[NC][一-鿿]", name or ""))


def _limit_pct(bare: str, name: str) -> float:
    """A股/场内基金当日涨跌幅限制(%)。板块优先: 创业/科创/北交 20/30%。
    主板 ST 与普通股一致为 10%(2026 规则, 用户以盘面实测确认: ST星农+10.08/*ST瑞茂精确停+10.00)。
    场内 ETF: 588xxx(科创板ETF专段)与跟踪创业板/科创/双创指数的基金为 20%, 其余 10%。"""
    nm = (name or "").upper()
    if bare[:1] in ("8", "4") or bare[:3] == "920":       # 北交所(含 920 新段)
        return 30.0
    if bare[:3] in ("688", "689") or bare[:2] == "30":    # 科创 / 创业 股票
        return 20.0
    if bare[:1] == "5" or bare[:2] in ("15", "16", "18"):   # 场内基金(沪5x / 深15,16,18)
        if bare[:3] == "588" or any(k in nm for k in ("创业板", "双创", "科创")):
            return 20.0
        return 10.0
    return 10.0                                           # 主板(含 ST): 10


def _clist(fid: str, pz: int) -> list[dict]:
    """拉 clist 榜单(fid=f3 涨幅 / f6 成交额), 降序。东财单页上限100, 需要更多时自动翻页(pn)。"""
    import requests as _rq
    s = _rq.Session(); s.trust_env = False
    per = 100
    pages = max(1, (int(pz) + per - 1) // per)
    out: list[dict] = []
    for pn in range(1, pages + 1):
        params = {"pn": str(pn), "pz": str(per), "po": "1", "np": "1", "fltt": "2", "invt": "2",
                  "fid": fid, "fs": _FS_ALL_A, "fields": _FIELDS}
        got = None
        for h in _HOSTS:
            try:
                d = s.get(f"https://{h}/api/qt/clist/get", params=params, timeout=7).json().get("data")
                if d and d.get("diff"):
                    got = d["diff"]; break
            except Exception:
                continue
        if not got:
            break
        out.extend(got)
        if len(got) < per:        # 末页, 后面没了
            break
    return out[:pz]


def _row(x: dict) -> dict | None:
    def _f(key):
        try:
            return float(x.get(key))
        except (TypeError, ValueError):
            return None      # 停牌/盘后缺数字段是 '-'
    code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
    pct = _f("f3")
    if pct is None:
        return None
    if not code or "ST" in name.upper() or "退" in name:   # 去噪: ST/退市不算强势风向
        return None
    mktcap = (_f("f20") or 0) / 1e8   # 亿
    return {"code": code, "name": name, "pct": round(pct, 2),
            "成交额亿": round((_f("f6") or 0) / 1e8, 1),
            "换手": x.get("f8"), "量比": x.get("f10"),
            "市值亿": round(mktcap, 0), "行业": x.get("f100") or "",
            "概念": [c for c in str(x.get("f103") or "").split(",") if c],
            "limit": _limit_pct(code, name)}


def scan_strong_stocks() -> dict:
    """扫描全市场涨幅榜 + 成交额榜, 聚合成当日市场画像。失败返回 {error}。"""
    c = _cache.get("scan")
    if c and _t.time() - c[1] < _TTL:
        return c[0]
    up = [r for r in (_row(x) for x in _clist("f3", 120)) if r]
    amt = [r for r in (_row(x) for x in _clist("f6", 40)) if r]
    if not up:
        return {"error": "榜单源暂不可达(东财抖动)"}

    # 涨停数(按各自板块涨停幅度判, 含一字/触板回封都算; 新股前5日无涨跌停, 只计入大涨梯队)
    limit_up = [r for r in up if not _is_new_stock(r["name"]) and r["pct"] >= r["limit"] - 0.3]
    # 大涨梯队
    strong = [r for r in up if r["pct"] >= 5]
    # 板块扎堆(涨幅榜里行业出现次数)
    ind_cnt = Counter(r["行业"] for r in up if r["行业"])
    # 概念扎堆
    con_cnt = Counter(c for r in up for c in r["概念"])
    # 风格: 市值结构 + 换手
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    small = [r for r in strong if r["市值亿"] and r["市值亿"] < 50]
    big = [r for r in strong if r["市值亿"] and r["市值亿"] > 500]
    turns = [_f(r["换手"]) for r in strong if _f(r["换手"]) is not None]
    avg_turn = round(sum(turns) / len(turns), 1) if turns else None

    out = {
        "as_of": _t.strftime("%Y-%m-%d %H:%M", _t.localtime()),
        "涨停数": len(limit_up),
        "大涨数(≥5%)": len(strong),
        "板块扎堆": [{"行业": k, "上榜数": v} for k, v in ind_cnt.most_common(6)],
        "概念扎堆": [{"概念": k, "上榜数": v} for k, v in con_cnt.most_common(8)],
        "风格": {
            "强势股均换手%": avg_turn,
            "小盘(<50亿)占比%": round(len(small) / len(strong) * 100) if strong else 0,
            "大盘(>500亿)占比%": round(len(big) / len(strong) * 100) if strong else 0,
        },
        "领涨样本": [{"name": r["name"], "code": r["code"], "涨幅": r["pct"], "行业": r["行业"],
                     "市值亿": r["市值亿"], "换手": r["换手"],
                     "该股涨停幅度%": r["limit"],
                     "涨停": (not _is_new_stock(r["name"])) and r["pct"] >= r["limit"] - 0.3} for r in up[:12]],
        "吸金榜(成交额前)": [{"name": r["name"], "code": r["code"], "成交额亿": r["成交额亿"],
                            "涨幅": r["pct"], "行业": r["行业"],
                            "该股涨停幅度%": r["limit"],
                            "涨停": (not _is_new_stock(r["name"])) and r["pct"] >= r["limit"] - 0.3} for r in amt[:10]],
        "note": "强势股结构化画像(全市场涨幅榜+成交额榜聚合, 非个股K线肉眼扫)。"
                "涨停数按各板块真实涨停幅度(科创/创业20、主板10)判。"
                "样本行的 涨停 字段即该票是否涨停的判定结果, 描述个股时以它为准; "
                "涨幅高但 涨停=false 的(如创业板+10%)描述为'大涨'。"
                "风格: 小盘高换手占比高=妖股/题材投机, 大盘低换手占比高=趋势/机构。"
                "板块/概念扎堆=今日资金主线。已剔除 ST/退市。",
    }
    _cache["scan"] = (out, _t.time())
    return out


def _rank_row(x: dict) -> dict | None:
    """榜单行(保留 ST, 打 is_st 标记; 比 _row 宽松, 给 top100 用)。
    停牌/盘后部分数字段是 '-', 逐字段安全转换。"""
    def _f(key):
        try:
            return float(x.get(key))
        except (TypeError, ValueError):
            return None
    code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
    pct = _f("f3")
    if pct is None or not code:
        return None
    new = _is_new_stock(name)
    lim = None if new else _limit_pct(code, name)
    return {"code": code, "name": name, "pct": round(pct, 2),
            "成交额亿": round((_f("f6") or 0) / 1e8, 2),
            "换手": x.get("f8"), "量比": x.get("f10"),
            "市值亿": round((_f("f20") or 0) / 1e8, 0),
            "行业": x.get("f100") or "",
            "limit": lim,
            "涨停占比%": round(pct / lim * 100, 1) if lim else None,   # 涨幅占该板块涨停幅度的比例(100=涨停), 跨板块可比; 新股无涨跌停→None
            "is_new": new,
            "is_st": ("ST" in name.upper() or "退" in name)}


def top_rankings(limit: int = 100) -> dict:
    """全市场 涨幅榜(按涨停占比排)top-N + 成交额榜 top-N。缓存 120s。失败返回 {error}。"""
    c = _cache.get("rankings")
    if c and _t.time() - c[1] < _TTL and c[2] >= limit:
        out = c[0]
        return {**out, "gainers": out["gainers"][:limit], "by_amount": out["by_amount"][:limit]}
    pz = max(int(limit), 1)
    # 涨幅榜按"涨停占比"(涨幅/该板块涨停幅度)排: 主板涨停10%与创业板涨停20%都算100%, 跨板块公平。
    # 必须拉大池子(只按 raw 涨幅取 top, 10%涨停的主板会被20%板挤光), 再用占比重排。
    pool = [r for r in (_rank_row(x) for x in _clist("f3", max(pz * 8, 800))) if r]
    # 占比封顶100(涨停因报价跳动会微过线100.x%, 不封顶则各板块涨停按零头排, 又把主板挤掉);
    # 涨停统一并列, 二级用成交额(板块中性), 让主板涨停与创业/科创涨停真正平起。
    # 新股(N/C, 前5日无涨跌停)没有占比, 用 raw 涨幅同尺度封顶参与排序: 大涨新股与涨停股并列后拼成交额。
    def _key(r):
        base = r["涨停占比%"] if r.get("涨停占比%") is not None else r["pct"]
        return (min(base, 100.0), r["成交额亿"])
    up = sorted([r for r in pool if r.get("涨停占比%") is not None or r.get("is_new")],
                key=_key, reverse=True)[:pz]
    amt = [r for r in (_rank_row(x) for x in _clist("f6", pz)) if r][:pz]
    if not up and not amt:
        return {"error": "榜单源暂不可达(东财抖动)"}
    out = {"as_of": _t.strftime("%Y-%m-%d %H:%M", _t.localtime()),
           "gainers": up, "by_amount": amt,
           "note": "涨幅榜按涨停占比(涨幅/该板块涨停幅度)排, 跨板块可比; 成交额榜按成交额。东财 clist 实时。is_st=ST/退市。"}
    _cache["rankings"] = (out, _t.time(), pz)
    return out
