"""东财资金流页面的数据接口(data.eastmoney.com/dataapi/bkzj): push2 被限流时的板块备用源。

东财限流按主机来: push2 / push2delay / push2his 整台被拦(clist、ulist、K线都不通)时,
data.eastmoney.com 的资金流接口照常可用, 而且板块口径与 clist 完全一致(同一套 BK 代码,
行业约 500 个、概念约 500 个) —— 比新浪那套老分类贴得多, "半导体""影视院线"都在。

接口一次只回一个指标(key 指定), 按该指标降序返回全量:
  code=m:90+t:2 行业 / m:90+t:3 概念 / b:BKxxxx 某板块成分股
  key=f3 涨跌幅(×100 的整数) / key=f62 主力净流入(元)
所以要涨幅和资金两个维度就拉两次按代码合并。
"""
from __future__ import annotations
import time

import requests

_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/bkzj/"}
_TTL = 120
_cache: dict = {}
KINDS = {"行业": "m:90 t:2", "概念": "m:90 t:3"}
SOURCE = "东财资金流接口(行情主机限流时的备用通道, 板块口径与东财一致)"


def _get(code: str, key: str) -> list[dict]:
    ck = (code, key)
    hit = _cache.get(ck)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    r = requests.get(_URL, params={"key": key, "code": code}, headers=_HDR, timeout=8)
    rows = ((r.json() or {}).get("data") or {}).get("diff") or []
    if rows:
        _cache[ck] = (rows, time.time())
    return rows


def _merged(code: str) -> list[dict]:
    """涨跌幅 + 主力净流入 按代码合并, 保持涨跌幅降序。"""
    pct = _get(code, "f3")
    flow = {x.get("f12"): x.get("f62") for x in _get(code, "f62")}
    out = []
    for x in pct:
        c = x.get("f12")
        try:
            out.append({"code": c, "name": x.get("f14"),
                        "pct": round(float(x.get("f3")) / 100, 2) if x.get("f3") is not None else None,
                        "net_yi": round(float(flow[c]) / 1e8, 2) if flow.get(c) is not None else None})
        except (TypeError, ValueError):
            continue
    return out


def boards(kind: str) -> list[dict]:
    return _merged(KINDS[kind])


def find_board(name: str) -> dict | None:
    """精确名优先, 其次包含关系里名字最短的。行业和概念一起找。"""
    q = (name or "").strip()
    if not q:
        return None
    allb = []
    for kind in KINDS:
        try:
            allb += [{**b, "kind": kind} for b in boards(kind)]
        except Exception:
            continue
    for b in allb:
        if b["name"] == q:
            return b
    hits = [b for b in allb if q in (b["name"] or "")]
    return min(hits, key=lambda b: len(b["name"])) if hits else None


def hot_concepts(top: int = 15) -> list[dict]:
    # 东财概念里混着"昨日涨停/昨日打二板以上表现"这类按昨日表现临时编的统计板块, 不是题材
    real = [b for b in boards("概念") if not (b["name"] or "").startswith("昨日")]
    return [{"概念": b["name"], "涨跌幅": b["pct"], "主力净流入亿": b["net_yi"]} for b in real[:top]]


def board_stocks(bk: str, top: int = 12) -> list[dict]:
    return _merged(f"b:{bk}")[:top]
