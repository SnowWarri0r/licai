"""每日强势股扫描 → 市场画像(供 agent 复盘'今天什么风格的票在涨')。

思路: 不让 LLM 肉眼看 100 根 K线(贵/噪/不可复现), 而是用东财 clist 榜单一次拉全市场
涨幅榜 + 成交额榜(带行业 f100 / 概念 f103 / 换手 f8 / 量比 f10 / 市值 f20), 纯代码聚合成
结构化"市场画像": 涨停数、板块/概念扎堆、风格(大盘趋势 vs 小盘妖股)、领涨与吸金样本。
LLM 只把画像总结成人话 + 落到用户持仓, 便宜可复现。
"""
from __future__ import annotations
import time as _t
from collections import Counter

_cache: dict = {}
_TTL = 120  # 盘中 2 分钟

_FS_ALL_A = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:7,m:1 t:3"   # 沪深A股(不含北交所/B股)
_FIELDS = "f2,f3,f6,f8,f10,f12,f14,f20,f100,f103"
_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com", "1.push2.eastmoney.com"]


def _limit_pct(bare: str, name: str) -> float:
    if "ST" in (name or "").upper():
        return 5.0
    if bare[:3] == "688" or bare[:2] == "30":
        return 20.0
    return 10.0


def _clist(fid: str, pz: int) -> list[dict]:
    """拉一页 clist 榜单(fid=f3 涨幅 / f6 成交额), 降序。"""
    import requests as _rq
    s = _rq.Session(); s.trust_env = False
    params = {"pn": "1", "pz": str(pz), "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": fid, "fs": _FS_ALL_A, "fields": _FIELDS}
    for h in _HOSTS:
        try:
            d = s.get(f"https://{h}/api/qt/clist/get", params=params, timeout=7).json().get("data")
            if d and d.get("diff"):
                return d["diff"]
        except Exception:
            continue
    return []


def _row(x: dict) -> dict | None:
    code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
    try:
        pct = float(x.get("f3"))
    except (TypeError, ValueError):
        return None
    if not code or "ST" in name.upper() or "退" in name:   # 去噪: ST/退市不算强势风向
        return None
    mktcap = float(x.get("f20") or 0) / 1e8   # 亿
    return {"code": code, "name": name, "pct": round(pct, 2),
            "成交额亿": round(float(x.get("f6") or 0) / 1e8, 1),
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

    # 涨停数(按各自板块涨停幅度判, 含一字/触板回封都算)
    limit_up = [r for r in up if r["pct"] >= r["limit"] - 0.3]
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
                     "市值亿": r["市值亿"], "换手": r["换手"]} for r in up[:12]],
        "吸金榜(成交额前)": [{"name": r["name"], "code": r["code"], "成交额亿": r["成交额亿"],
                            "涨幅": r["pct"], "行业": r["行业"]} for r in amt[:10]],
        "note": "强势股结构化画像(全市场涨幅榜+成交额榜聚合, 非个股K线肉眼扫)。"
                "涨停数按各板块真实涨停幅度(科创/创业20、主板10)判。"
                "风格: 小盘高换手占比高=妖股/题材投机, 大盘低换手占比高=趋势/机构。"
                "板块/概念扎堆=今日资金主线。已剔除 ST/退市。",
    }
    _cache["scan"] = (out, _t.time())
    return out
