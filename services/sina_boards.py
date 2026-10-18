"""新浪板块(行业 + 概念)行情: 东财 clist 被限流时的备用源。

东财 clist 会按来源 IP 限流脚本客户端(浏览器能拉、脚本被 RST, 冷却 30-60 分钟才恢复)。
一限流, 热门概念 / 板块成分股这两个工具就一起失效, 复盘时恰好最需要它们。

新浪的口径与东财不同: 行业是新浪自己的分类(约 50 个), 概念约 350 个, 没有主力净流入。
所以只做备用, 返回里注明数据源, 让上层说得清哪个数是谁给的。
"""
from __future__ import annotations
import json
import re
import time

import requests

_LIST_URLS = {
    "行业": "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
    "概念": "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class",
}
_NODE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
_TTL = 300
_cache: dict = {}

SOURCE = "新浪财经(东财限流时的备用源, 板块口径与东财不同, 无主力净流入)"


def _parse_list(text: str, kind: str) -> list[dict]:
    """`var X = {"node":"node,名称,家数,均价,涨跌额,涨跌幅,量,额,领涨symbol,领涨涨幅,领涨价,领涨涨跌额,领涨名"}`"""
    m = re.search(r"=\s*(\{.*\})", text, re.S)
    if not m:
        return []
    out = []
    for v in json.loads(m.group(1)).values():
        f = v.split(",")
        if len(f) < 13:
            continue
        try:
            out.append({"node": f[0], "name": f[1], "kind": kind, "count": int(float(f[2] or 0)),
                        "pct": round(float(f[5] or 0), 2), "amount_yi": round(float(f[7] or 0) / 1e8, 2),
                        "leader": f[12], "leader_code": f[8][2:], "leader_pct": round(float(f[9] or 0), 2)})
        except ValueError:
            continue
    return out


def boards(kind: str) -> list[dict]:
    hit = _cache.get(kind)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    r = requests.get(_LIST_URLS[kind], headers=_HDR, timeout=8)
    rows = _parse_list(r.content.decode("gbk", errors="ignore"), kind)
    if rows:
        _cache[kind] = (rows, time.time())
    return rows


def hot_concepts(top: int = 15) -> list[dict]:
    rows = sorted(boards("概念"), key=lambda x: -x["pct"])[:top]
    return [{"概念": r["name"], "涨跌幅": r["pct"], "成分股数": r["count"], "成交额亿": r["amount_yi"],
             "领涨": f"{r['leader']}({r['leader_code']}) {r['leader_pct']:+.2f}%"} for r in rows]


def find_board(name: str) -> dict | None:
    """精确名优先, 其次包含关系里名字最短的(最贴近)。行业和概念一起找。"""
    q = (name or "").strip()
    if not q:
        return None
    allb = []
    for kind in ("行业", "概念"):
        try:
            allb += boards(kind)
        except Exception:
            continue
    for b in allb:
        if b["name"] == q:
            return b
    hits = [b for b in allb if q in b["name"] or b["name"] in q]
    return min(hits, key=lambda b: len(b["name"])) if hits else None


def board_stocks(node: str, top: int = 12) -> list[dict]:
    r = requests.get(_NODE_URL, headers=_HDR, timeout=8,
                     params={"page": 1, "num": max(top, 12), "sort": "changepercent", "asc": 0,
                             "node": node, "symbol": ""})
    out = []
    for x in (r.json() or [])[:top]:
        try:
            out.append({"name": x.get("name"), "code": x.get("code"),
                        "涨跌幅": round(float(x.get("changepercent") or 0), 2),
                        "现价": float(x.get("trade") or 0),
                        "换手%": round(float(x.get("turnoverratio") or 0), 2),
                        "成交额亿": round(float(x.get("amount") or 0) / 1e8, 2)})
        except (ValueError, TypeError):
            continue
    return out
