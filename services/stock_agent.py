"""问股票为什么涨/跌 — 挂工具的小 agent。

LLM 自己决定调哪些工具(查行情/走势/新闻/持仓/大盘情绪)拿数据, 再总结涨跌原因。
自由问答。硬规则: 仅做客观解读, 买卖/操作决策交给用户。
"""
from __future__ import annotations
import asyncio
import base64 as _b64
import json as _json
import os
import re as _re
import time as _time

import services.llm_client as _llm
import services.answer_guard as _guard
# 系统提示拆成常驻核心 + 按需技能包, 见 services/agent_guides.py
from services.agent_guides import (CORE as _SYSTEM, ALL_TEXT as _PROMPT_ALL,  # noqa: F401
                                   LOAD_GUIDE_TOOL as _LOAD_GUIDE_TOOL, load_guide as _load_guide)

_MODEL = "claude-opus-5-5"
_MAX_ROUNDS = 14
# 自适应思考 + effort: 简单题少查少写, 难题多想。effort 是整体投入档位(low/medium/high)。
_THINKING = os.environ.get("STOCK_AGENT_THINKING", "1").lower() not in ("0", "false", "no")
_EFFORT = os.environ.get("STOCK_AGENT_EFFORT", "medium") or None
# 开思考时 max_tokens 含思考 token, 4096 会被思考吃掉一大半
_ROUND_MAX_TOKENS = 16000 if _THINKING else 4096

# A 股 代码↔名称 表 (akshare, 缓存 12h, 供按名字解析)
_code_name_cache: tuple[dict, dict, float] | None = None


_etf_codes: set = set()      # 全表里属于场内基金的代码, 搜索排序用(个股优先于 ETF)


def _load_a_code_name_sync():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_info_a_code_name()
    name2code, code2name = {}, {}
    for _, r in df.iterrows():
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        if code and name:
            name2code[name] = code
            code2name[code] = name
    # 并入场内 ETF/LOF: stock_info_a_code_name 只有个股, ETF 此前只能靠"6位数字代码"
    # 兜底分支精确命中, 按名称/前缀/拼音一律查不到(如 588710 科创半导体设备ETF华泰柏瑞)。
    # 并进同一张表后, 名称匹配/子串/拼音索引(从这张表建)对 ETF 一并生效。
    # 走新浪源: 东财 fund_etf_spot_em 在本机被网关拦。失败则跳过, 不影响个股。
    try:
        edf = ak.fund_etf_category_sina(symbol="ETF基金")
        for _, r in edf.iterrows():
            code = str(r.get("代码") or "").strip()[-6:]      # 源带 sh/sz 前缀
            name = str(r.get("名称") or "").strip()
            if not (len(code) == 6 and code.isdigit() and name):
                continue
            _etf_codes.add(code)
            code2name.setdefault(code, name)
            name2code.setdefault(name, code)                  # 不覆盖同名个股
    except Exception as e:
        print(f"[stock_agent] ETF 全表并入失败(个股不受影响): {e}")
    return name2code, code2name


_dict_refreshing = False


async def _refresh_symbol_dict_bg():
    """后台重拉全表并落盘。只在库里数据偏旧时触发, 不阻塞当次搜索。"""
    global _dict_refreshing, _code_name_cache
    if _dict_refreshing:
        return
    _dict_refreshing = True
    try:
        from database import save_symbol_dict
        n2c, c2n = await asyncio.to_thread(_load_a_code_name_sync)
        if c2n:
            await save_symbol_dict([(cd, nm, 1 if cd in _etf_codes else 0)
                                    for cd, nm in c2n.items()])
            _code_name_cache = (n2c, c2n, _time.time())
            print(f"[stock_agent] 代码字典已刷新落盘: {len(c2n)} 条")
    except Exception as e:
        print(f"[stock_agent] 代码字典后台刷新失败: {e}")
    finally:
        _dict_refreshing = False


async def _code_name_maps():
    """代码↔名称全表。内存缓存 → SQLite → 现拉。

    现拉要 20s+(stock_info_a_code_name 17 个分页约 21s + ETF 表 2.4s), 每次重启都
    重付会让首次搜索卡几十秒。落盘后重启直接用库里的, 只有库为空才同步拉;
    库里超过 3 天则照常返回旧数据, 同时后台刷新, 用户不等。
    """
    global _code_name_cache
    if _code_name_cache and _time.time() - _code_name_cache[2] < 43200:
        return _code_name_cache[0], _code_name_cache[1]
    # 1) 库里有就先用(顺带恢复 ETF 标记, 搜索排序要用)
    try:
        from database import load_symbol_dict
        rows, ts = await load_symbol_dict()
        if rows:
            n2c, c2n = {}, {}
            for cd, nm, is_etf in rows:
                c2n[cd] = nm
                n2c.setdefault(nm, cd)
                if is_etf:
                    _etf_codes.add(cd)
            _code_name_cache = (n2c, c2n, _time.time())
            stale = True
            try:
                from datetime import datetime as _dt
                stale = (_dt.utcnow() - _dt.fromisoformat(str(ts)[:19])).days >= 3
            except Exception:
                pass
            if stale:
                asyncio.create_task(_refresh_symbol_dict_bg())
            return n2c, c2n
    except Exception as e:
        print(f"[stock_agent] 读代码字典失败, 回退现拉: {e}")
    # 2) 库为空(首次运行): 只能同步拉一次, 之后落盘
    try:
        n2c, c2n = await asyncio.to_thread(_load_a_code_name_sync)
        _code_name_cache = (n2c, c2n, _time.time())
        try:
            from database import save_symbol_dict
            await save_symbol_dict([(cd, nm, 1 if cd in _etf_codes else 0)
                                    for cd, nm in c2n.items()])
        except Exception:
            pass
        return n2c, c2n
    except Exception:
        return ({}, {}) if not _code_name_cache else (_code_name_cache[0], _code_name_cache[1])


_pinyin_cache: tuple | None = None   # (rows, 源表id) —— rows: [(首字母串, 全拼串, 名称, 代码)]


async def _pinyin_rows() -> list:
    """A股全表 → 名称拼音索引(懒建一次, 跟随 _code_name_maps 缓存周期)。
    每行 (首字母串, 全拼串, 名称, 代码): 首字母 zgpa→中国平安; 全拼 zhongguopingan
    用于同音错别字匹配(输错字但读音对, 转拼音仍中)。非汉字原样并入小写。"""
    global _pinyin_cache
    n2c, _ = await _code_name_maps()
    if not n2c:
        return []
    if _pinyin_cache and _pinyin_cache[1] == id(n2c):
        return _pinyin_cache[0]

    def _build():
        from pypinyin import lazy_pinyin, Style
        rows = []
        for nm, cd in n2c.items():
            try:
                ini = "".join(lazy_pinyin(nm, style=Style.FIRST_LETTER)).lower()
                full = "".join(lazy_pinyin(nm, style=Style.NORMAL)).lower()
            except Exception:
                continue
            rows.append((ini, full, nm, cd))
        return rows

    rows = await asyncio.to_thread(_build)
    _pinyin_cache = (rows, id(n2c))
    return rows


def _to_full_pinyin(s: str) -> str:
    """任意串 → 全拼小写(汉字转拼音, 非汉字原样)。用于把用户输入(可能带错别字)归一到读音。"""
    try:
        from pypinyin import lazy_pinyin, Style
        return "".join(lazy_pinyin(s, style=Style.NORMAL)).lower()
    except Exception:
        return (s or "").lower()


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein, 超过 cap 提前退出(返回 cap+1)。用于近似匹配的容错阈值。"""
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[lb]


# ---------------------------------------------------------------------------
# 工具执行器 (都返回可 JSON 序列化的 dict/list)
# ---------------------------------------------------------------------------

def _norm_code(code: str) -> str:
    """LLM 常给 A 股带 sh/sz 前缀(如 sh600171), 但行情接口要裸 6 位代码; 这里剥掉前缀。
    HK./US. 这类保持原样。"""
    c = (code or "").strip()
    m = _re.match(r"^(?:sh|sz|SH|SZ)(\d{6})$", c)
    return m.group(1) if m else c

async def _active_holdings() -> list[dict]:
    """真实在持的持仓: 按 ledger(综合成本法)现算 shares, 只留 shares>0。
    holdings 表的 shares 列对已清仓的票可能是陈旧非0值, 不能直接信; 这里跟 /api/portfolio 口径一致。"""
    from database import get_all_holdings, get_position_actions
    from services.position_ledger import ledger_state
    out = []
    for h in await get_all_holdings():
        code = h.get("stock_code")
        shares = float(h.get("shares") or 0)
        hold_days = open_date = cost = div_ps = None
        try:
            acts = await get_position_actions(code, limit=500)
            if acts:
                st = await ledger_state(code, acts, h.get("broker"))   # 费用/汇率口径同持仓列表, 含分红摊薄
                shares = float(st.get("shares") or 0)
                hold_days = st.get("weighted_days")
                cost = st.get("cost_price")
                div_ps = st.get("div_per_share")
                lots = st.get("lots") or []
                if lots:
                    open_date = min(l["trade_date"] for l in lots)  # 当前段最早一笔=开仓日
        except Exception:
            pass  # ledger 算不出就退回表里的 shares
        if shares > 0:
            row = {**h, "shares": shares, "hold_days": hold_days,
                   "open_date": _date_with_weekday(open_date)}
            if cost is not None:
                row["cost_price"] = cost
            if div_ps:
                row["每股已收分红"] = div_ps
            out.append(row)
    return out


def _date_with_weekday(d: str | None) -> str | None:
    """把 'YYYY-MM-DD' 标上星期, 省得 LLM 自己换算星期出错(如把周五说成周四)。"""
    if not d:
        return d
    import datetime
    try:
        dt = datetime.date.fromisoformat(d[:10])
        wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
        return f"{d}({wk})"
    except Exception:
        return d


async def _tool_coiled() -> dict:
    """横盘蓄势扫描(共享 10min 缓存)。"""
    from services.coiled_scanner import scan_coiled
    return await scan_coiled()


async def _tool_global_indices(query: str = "") -> dict:
    """全球指数/汇率/商品实时行情(宏观仪表盘同源): A股大盘、恒生系、道纳标、日经/KOSPI/富时、
    汇率、金属、能化。query 为空返回全部分组; 非空按 指标名/组名 子串过滤。"""
    from services.market_data import get_macro_quotes
    groups = await get_macro_quotes()
    if not groups:
        return {"error": "宏观行情暂不可达(源抖动), 稍后再试"}
    q = (query or "").strip().lower()
    out: dict = {}
    for grp, items in groups.items():
        rows = []
        for it in items:
            row = {"name": it["name"], "price": it["price"], "涨跌%": it["change_pct"]}
            # 成交额只有交易所自己披露才有: A股/港股/KOSPI 有真额(带币种), 美股三大指数
            # 与日经/富时没有 —— 有额给额, 没额给量并写明"无成交额", 免得模型拿量当额、
            # 或者干脆自己编一个数出来。
            if it.get("amount"):
                row["成交额"] = it["amount"]
                row["成交额币种"] = it.get("amount_ccy") or "元"
            elif it.get("volume"):
                row["成交量"] = it["volume"]
                row["成交额"] = "无(该指数不披露成交额)"
            rows.append(row)
        if q:
            rows = [r for r in rows if q in r["name"].lower() or q in grp.lower()]
        if rows:
            out[grp] = rows
    if not out:
        return {"error": f"没有匹配 {query} 的指标; 可用分组: {', '.join(groups.keys())}"}
    out["note"] = ("实时快照(Sina/东财), A股大盘/海外指数/汇率/商品同一时点; 涨跌%相对昨收。"
                   "成交额务必连币种一起表述(恒生=港元, KOSPI=韩元, A股=人民币)。")
    return out


# 常见美股中文名 → ticker(resolve_stock 用; 生僻的靠用户/LLM 直接给 ticker)
_US_NAME_MAP = {
    "苹果": "AAPL", "微软": "MSFT", "英伟达": "NVDA", "特斯拉": "TSLA",
    "谷歌": "GOOGL", "亚马逊": "AMZN", "脸书": "META", "META": "META",
    "奈飞": "NFLX", "网飞": "NFLX", "英特尔": "INTC", "超微": "AMD",
    "高通": "QCOM", "博通": "AVGO", "美光": "MU", "台积电": "TSM",
    "甲骨文": "ORCL", "特斯拉汽车": "TSLA", "拼多多": "PDD", "百度美股": "BIDU",
    "阿里美股": "BABA", "理想汽车": "LI", "蔚来": "NIO", "小鹏汽车": "XPEV",
    "微策略": "MSTR", "波音": "BA", "伯克希尔": "BRK.B",
}


async def _tool_resolve_stock(query: str) -> dict:
    """名字或代码 → 标准代码 + 名称。先查在持持仓(已清仓不算在持), 再查持仓表全部, 再 A 股全表,
    最后美股(纯字母 ticker / 常见中文名 → US.XXX)。"""
    from database import get_all_holdings
    q = (query or "").strip()
    if not q:
        return {"error": "空查询"}
    # 1) 在持持仓里匹配 (名字/代码 子串) → in_holdings=True
    try:
        for h in await _active_holdings():
            nm = h.get("stock_name") or ""
            cd = h.get("stock_code") or ""
            if q == nm or q == cd or (q in nm) or (q in cd):
                return {"code": cd, "name": nm, "in_holdings": True}
    except Exception:
        pass
    # 1a) 场外资产(基金/场内ETF/加密/理财等)在持匹配 → in_holdings=True
    # (场内ETF 这类持仓在外部资产账本, 不在 A股 holdings 表; 漏了这层会把真实持仓说成'没有')
    try:
        from database import list_external_assets
        held_hit, cleared_hit = None, None
        for x in await list_external_assets():
            nm = x.get("name") or ""
            cd = str(x.get("code") or "")
            if not (q == nm or (cd and q == cd) or (q in nm) or (cd and q in cd)):
                continue
            sh = float(x.get("shares") or 0)
            at = x.get("asset_type")
            has_bal = (x.get("manual_value") or x.get("cost_amount") or 0) and at not in ("FUND", "CRYPTO")
            hit = {"code": cd or nm, "name": nm,
                   "asset_class": _ASSET_CLASS_CN.get(at, "其他持仓")}
            if sh > 0 or has_bal:
                held_hit = held_hit or {**hit, "in_holdings": True, "note": "在持(资产看板账本)"}
            else:
                cleared_hit = cleared_hit or {**hit, "in_holdings": False, "note": "该标的已赎回/清仓"}
        if held_hit:
            return held_hit
        if cleared_hit:
            return cleared_hit
    except Exception:
        pass
    # 1b) 持仓表里有但已清仓 → 能解析出代码, 但标记 in_holdings=False
    try:
        for h in await get_all_holdings():
            nm = h.get("stock_name") or ""
            cd = h.get("stock_code") or ""
            if q == nm or q == cd or (q in nm) or (q in cd):
                return {"code": cd, "name": nm, "in_holdings": False, "note": "已清仓, 不在当前持仓"}
    except Exception:
        pass
    # 1c) 港股代码: 5 位数字(A 股是 6 位, 不会撞)或 HK./hk 前缀 → 先按港股解析
    from services.market_data import normalize_stock_code, search_hk_stocks
    hk_code = None
    if q.isdigit() and len(q) == 5:
        hk_code = f"HK.{q}"
    elif _re.fullmatch(r"(?i)hk\.?\d{1,5}", q):
        hk_code = normalize_stock_code(q)
    if hk_code:
        hits = await asyncio.to_thread(search_hk_stocks, hk_code[3:])
        hit = next((h for h in hits if h["code"] == hk_code), None)
        if hit:
            return {"code": hit["code"], "name": hit["name"], "market": "HK", "in_holdings": False}
    # 2) A 股全表 —— 精确名/代码直接命中(唯一, 置顶)
    n2c, c2n = await _code_name_maps()
    if q in c2n:
        return {"code": q, "name": c2n[q], "in_holdings": False}
    if q in n2c:
        return {"code": n2c[q], "name": q, "in_holdings": False}

    # 2') 汇总所有匹配法进一个候选池(不早退, 每个代码留最优 tier)——
    #   否则"华宏"命中华宏科技就 return, 同音的华虹永远出不来。
    #   tier: 0中文前缀 1中文子串 2首字母前缀 3首字母/全拼子串 4同音子串 5编辑距离近似。
    #   排序: 先 tier, 端点再按成交额(热度)重排, 所以热门自然靠前。
    rows = await _pinyin_rows()
    ql = q.lower()
    qpy = _to_full_pinyin(q)
    has_cjk = any("一" <= ch <= "鿿" for ch in q)
    is_letters = bool(_re.fullmatch(r"[A-Za-z0-9]{2,12}", q)) and not q.isdigit()
    # 数字代码前缀/子串: 此前代码只有"精确6位"一条路(纯数字被 is_letters 排除), 输一半
    # 代码一律查不到(6005 查不到 600519, 5887 查不到 588710)。在持票能中是因为持仓分支
    # 单独做了 q in cd 子串匹配, 全表没有 —— 这里补齐, 口径与持仓一致。
    is_digits = q.isdigit() and 2 <= len(q) <= 6
    pool: dict = {}   # code -> (tier, name)

    def _add(cd, nm, tier):
        if cd not in pool or tier < pool[cd][0]:
            pool[cd] = (tier, nm)

    for ini, full, nm, cd in rows:
        if is_digits:
            if cd.startswith(q):
                _add(cd, nm, 0)
            elif q in cd:
                _add(cd, nm, 2)
        if q in nm:
            _add(cd, nm, 0 if nm.startswith(q) else 1)
        if is_letters:
            if ini.startswith(ql):
                _add(cd, nm, 2)
            elif ql in ini or ql in full:
                _add(cd, nm, 3)
        if len(qpy) >= 4 and (qpy in full or (len(qpy) >= 6 and full in qpy)):
            _add(cd, nm, 4)
    # 港股按中文名补进同一个池(腾讯/美团/小米这类只在港股上市的, A 股全表里没有):
    # 名称前缀 tier0、子串 tier1、其余(拼音/简称命中) tier3, 与 A 股候选同一套排序
    if has_cjk:
        try:
            for h in await asyncio.to_thread(search_hk_stocks, q, 5):
                nm = h["name"]
                _add(h["code"], nm, 0 if nm.startswith(q) else 1 if q in nm else 3)
        except Exception:
            pass
    # 编辑距离近似仅中文输入且池仍空/很小时补(免误伤英文 ticker, 也省算力)
    if has_cjk and len(qpy) >= 4 and len(pool) < 3:
        cap = 1 if len(qpy) <= 6 else 2
        for _ini, full, nm, cd in rows:
            if cd in pool:
                continue
            if _edit_distance(qpy, full, cap) <= cap:
                _add(cd, nm, 5)

    if pool:
        # 池上限 20(端点还要逐个取行情)。数字代码前缀能一次命中上百只, 若仍按"名称长度"
        # 截断, 留下哪 20 只是任意的 —— 输 6005 会把 600519 茅台截掉, 后面按成交额重排
        # 也救不回来。数字查询改按代码升序截断: 结果确定, 且天然覆盖输入相邻的号段。
        key = ((lambda kv: (kv[1][0], kv[0])) if is_digits
               else (lambda kv: (kv[1][0], len(kv[1][1]))))
        ordered = sorted(pool.items(), key=key)[:20]
        return {"candidates": [{"name": nm, "code": cd, "_tier": tier}
                               for cd, (tier, nm) in ordered],
                "note": "候选按匹配度+热度(成交额)排序"}
    # 3) 6位数字代码但不在A股个股全表(如 ETF/LOF/可转债) → 直接当作代码, 实时查名
    if q.isdigit() and len(q) == 6:
        try:
            from services.market_data import get_stock_name
            nm = await get_stock_name(q)
        except Exception:
            nm = ""
        return {"code": q, "name": nm or "", "in_holdings": False}
    # 4) 美股: 纯字母 ticker(AAPL/NVDA) 或常见中文名 → US.XXX, 实时报价验一把顺便拿中文名
    ticker = None
    if _re.fullmatch(r"[A-Za-z]{1,5}", q):
        ticker = q.upper()
    elif q in _US_NAME_MAP:
        ticker = _US_NAME_MAP[q]
    if ticker:
        code = f"US.{ticker}"
        try:
            from services.market_data import get_realtime_quotes
            qq = (await get_realtime_quotes([code])).get(code)
        except Exception:
            qq = None
        if qq and qq.get("price"):
            return {"code": code, "name": qq.get("stock_name") or q, "market": "US", "in_holdings": False}
        if q in _US_NAME_MAP:   # 名字在映射里但行情暂不可达, 仍给出代码
            return {"code": code, "name": q, "market": "US", "in_holdings": False,
                    "note": "行情暂未验证(源抖动), 代码可直接用于 get_quote/get_trend"}
    return {"error": f"找不到 {q}"}


def _a_limit_pct(bare: str, name: str) -> float | None:
    """A股/场内基金当日涨跌停幅度(小数)。港美股无涨跌停→None。
    规则收口到 market_review._limit_pct 单一来源(板块优先、588科创ETF/创业板类基金=20%、北交30%)。"""
    if not (len(bare) == 6 and bare.isdigit()):
        return None
    from services.market_review import _limit_pct
    return _limit_pct(bare, name) / 100.0


async def _tool_get_quote(code: str) -> dict:
    from services.market_data import get_realtime_quotes, normalize_stock_code, get_stock_name
    code = normalize_stock_code(_norm_code(code))
    q = (await get_realtime_quotes([code])).get(code)
    if not q:
        return {"error": f"{code} 无行情"}
    name = ""
    try:
        name = await get_stock_name(code)
    except Exception:
        pass
    out = {
        "code": code, "name": name,
        "price": q.get("price"), "change_pct": q.get("change_pct"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "prev_close": q.get("prev_close"), "amount": q.get("amount"),
        "turnover_rate": q.get("turnover_rate"),
    }
    # 美股: 带上"这个价是什么时候的"和盘前/盘后。亚洲白天问美股时, price 是上一个交易
    # 时段的收盘, 不标时刻的话模型会当成"现在", 把昨天的涨跌说成今天的。
    # 港股同理带 quote_time(港交所当地时刻, 有它才分得清是实时还是上一交易日)。
    for k in ("price_as_of", "session", "ext_hours", "quote_time", "currency"):
        if q.get(k):
            out[k] = q[k]
    # 涨停/跌停 + 封板/炸板/冲高回落 判断 (仅 A 股)
    bare = code.split(".")[-1]
    pct = _a_limit_pct(bare, name)
    prev = float(q.get("prev_close") or 0)
    price = float(q.get("price") or 0)
    high = float(q.get("high") or 0)
    low = float(q.get("low") or 0)
    if pct and prev > 0 and price > 0:
        lu = round(prev * (1 + pct), 2)
        ld = round(prev * (1 - pct), 2)
        out["limit_up"], out["limit_down"] = lu, ld
        near = 0.005  # 容差
        touched_up = high >= lu - near
        sealed_up = price >= lu - near
        touched_dn = low <= ld + near and low > 0
        sealed_dn = price <= ld + near
        if sealed_up:
            out["盘口"] = "当前封涨停"
        elif touched_up:
            out["盘口"] = f"盘中触及涨停({lu})后炸板回落, 现价较最高回落{round((high - price) / high * 100, 2)}%"
        elif sealed_dn:
            out["盘口"] = "当前封跌停"
        elif touched_dn:
            out["盘口"] = f"盘中触及跌停({ld})后回升"
        else:
            out["盘口"] = "未触及涨跌停"
        out["日内振幅%"] = round((high - low) / prev * 100, 2) if high and low else None
    # TDX 数据源(可插拔)启用时, 补五档盘口 + 内外盘; 没配/连不上就跳过, 不影响主流程
    try:
        import services.tdx_client as _tdx
        if _tdx.is_enabled() and len(bare) == 6 and bare.isdigit():
            t = await _tdx.quote(bare)
            if t:
                out["五档"] = {"买盘": t.get("bids"), "卖盘": t.get("asks"),
                              "内盘手": t.get("内盘手"), "外盘手": t.get("外盘手")}
                # 封板时给出封单量(买一挂单)
                if out.get("盘口") == "当前封涨停" and t.get("bids"):
                    b1 = t["bids"][0]
                    out["盘口"] = f"当前封涨停, 封单 买一 {b1.get('手')}手@{b1.get('price')}"
                elif out.get("盘口") == "当前封跌停" and t.get("asks"):
                    a1 = t["asks"][0]
                    out["盘口"] = f"当前封跌停, 卖一砸单 {a1.get('手')}手@{a1.get('price')}"
                out["数据源"] = "盘口含TDX五档"
    except Exception:
        pass
    return out


async def _stock_display_name(code: str) -> str:
    """标的显示名(A股/ETF/港美股通用): 实时行情缓存里取, 拿不到返回空。"""
    try:
        from services.market_data import get_realtime_quotes, normalize_stock_code
        norm = normalize_stock_code(code)
        q = (await get_realtime_quotes([norm])).get(norm) or {}
        return q.get("stock_name") or ""
    except Exception:
        return ""


def _hkus(code: str) -> tuple[str, str] | None:
    """港美股 → (HK|US, symbol); 其余 None。裸 5 位数字只可能是港股(A 股 6 位), 补成 HK。"""
    from services.market_data import normalize_stock_code, split_stock_code
    raw = normalize_stock_code(_norm_code(code))
    if raw.isdigit() and len(raw) == 5:
        raw = f"HK.{raw}"
    m, sym = split_stock_code(raw)
    return (m, sym) if m in ("HK", "US") and sym else None


# 分时量能分段(交易所当地时刻, 左闭右开)
_SESSION_SEGS = {
    "HK": [("早盘首小时 09:30-10:30", "09:30", "10:30"), ("早盘后段 10:30-12:00", "10:30", "12:01"),
           ("午后 13:00-15:00", "13:00", "15:00"), ("尾盘 15:00-16:10(含收市竞价)", "15:00", "16:11")],
    "US": [("开盘30分 09:30-10:00", "09:30", "10:00"), ("上午 10:00-12:00", "10:00", "12:00"),
           ("午间 12:00-14:00", "12:00", "14:00"), ("下午 14:00-15:30", "14:00", "15:30"),
           ("尾盘30分 15:30-16:00(含收盘竞价)", "15:30", "16:01")],
}
_SESSION_TZ = {"HK": "香港时间(UTC+8, 与北京时间相同)",
               "US": "美东时间(America/New_York, 夏令时 UTC-4 / 冬令时 UTC-5)"}


def summarize_foreign_minute(market: str, m: dict, q: dict | None = None) -> dict:
    """港美股分时摘要(纯计算): 开高低收+时间、VWAP、振幅、量能分段占比、路径采样。
    q=同一交易日的实时行情时, 高低点以交易所行情为准(分时是逐分钟快照, 探不到分钟内的瞬时极值)。"""
    pts = [p for p in (m or {}).get("points") or [] if p.get("price")]
    if not pts:
        return {"error": "分时无有效数据"}
    prev = m.get("prev_close")
    hi = max(pts, key=lambda p: p["price"])
    lo = min(pts, key=lambda p: p["price"])
    hi_p, hi_t, lo_p, lo_t = hi["price"], hi["time"], lo["price"], lo["time"]
    if q:
        if q.get("high") and q["high"] >= hi_p:
            hi_p, hi_t = q["high"], min(pts, key=lambda p: abs(p["price"] - q["high"]))["time"]
        if q.get("low") and 0 < q["low"] <= lo_p:
            lo_p, lo_t = q["low"], min(pts, key=lambda p: abs(p["price"] - q["low"]))["time"]
    open_p, last_p = pts[0]["price"], pts[-1]["price"]
    vol = sum(p.get("手") or 0 for p in pts)
    amt = sum(p.get("额") or 0 for p in pts)
    if amt > 0 and vol > 0:
        vwap = amt / vol
    else:                                  # 美股分时不带成交额, 用逐分钟价×量近似
        pv = sum(p["price"] * (p.get("手") or 0) for p in pts)
        vwap = pv / vol if vol else None

    def pct(x):
        return round((x - prev) / prev * 100, 2) if prev and x is not None else None
    segs = []
    for name, a, b in _SESSION_SEGS.get(market, []):
        v = sum(p.get("手") or 0 for p in pts if a <= p["time"] < b)
        seg_pts = [p["price"] for p in pts if a <= p["time"] < b]
        if seg_pts or v:
            segs.append({"时段": name,
                         "成交占比%": round(v / vol * 100, 1) if vol else None,
                         "段末价": seg_pts[-1] if seg_pts else None})
    step = max(1, len(pts) // 8)
    path = [{"time": pts[i]["time"], "price": pts[i]["price"]} for i in range(0, len(pts), step)]
    if path[-1]["time"] != pts[-1]["time"]:
        path.append({"time": pts[-1]["time"], "price": last_p})
    return {"交易日": m.get("date"), "时区": _SESSION_TZ.get(market, ""),
            "昨收": prev, "开盘": open_p, "开盘涨跌%": pct(open_p),
            "最高": {"price": hi_p, "time": hi_t, "涨跌%": pct(hi_p)},
            "最低": {"price": lo_p, "time": lo_t, "涨跌%": pct(lo_p)},
            "现价/收盘": last_p, "涨跌%": pct(last_p),
            "VWAP": round(vwap, 3) if vwap else None,
            "现价相对VWAP%": round((last_p - vwap) / vwap * 100, 2) if vwap else None,
            "日内振幅%": round((hi_p - lo_p) / prev * 100, 2) if prev else None,
            "较最高回落%": round((hi_p - last_p) / hi_p * 100, 2) if hi_p else None,
            "高点先于低点": hi_t < lo_t,
            "成交量股": int(vol), "量能分段": segs, "路径采样": path}


async def _tool_intraday_foreign(market: str, sym: str) -> dict:
    from services.market_data import stock_minute_foreign, get_realtime_quotes, quote_session_date
    code = f"{market}.{sym}"
    try:
        m = await asyncio.to_thread(stock_minute_foreign, code)
    except Exception as e:  # noqa: BLE001
        return {"error": f"港美股分时源(腾讯)不可达: {type(e).__name__}"}
    if not m or not m.get("points"):
        return {"error": "港美股分时暂无数据(源抖动或该标的停牌)"}
    q = {}
    try:
        q = (await get_realtime_quotes([code])).get(code) or {}
    except Exception:
        pass
    same_day = bool(q) and quote_session_date(market, q) == m.get("date")
    out = summarize_foreign_minute(market, m, q if same_day else None)
    if out.get("error"):
        return out
    note = ("港股分时(腾讯, 逐分钟; 午休 12:00-13:00 无点); 交易日与时刻均为港交所当地时间。"
            if market == "HK" else
            "美股分时(腾讯, 逐分钟, 仅常规时段 09:30-16:00); 交易日与时刻均为美东当地时间, 给的是最近一个已开盘的交易日。"
            "VWAP 按逐分钟价×量近似(源不带成交额)。")
    return {"code": code, "name": q.get("stock_name") or await _stock_display_name(code), **out,
            "note": note + " 量能分段=各时段成交量占全天比例; 较最高回落% 大=冲高回落; 现价相对VWAP 为正=收在当日均价上方。"}


async def _tool_intraday(code: str) -> dict:
    """当日分时: 开盘/最高(及时间)/最低(及时间)/现价 + 是否冲高回落 + 关键点。
    A 股走 TDX(需启用); 港美股走腾讯分时, 另含 VWAP/振幅/量能分段。"""
    import services.tdx_client as _tdx
    from services.market_data import normalize_stock_code
    hu = _hkus(code)
    if hu:
        return await _tool_intraday_foreign(*hu)
    if not _tdx.is_enabled():
        return {"error": "分时需启用 TDX 数据源(设置→TDX base_url), 当前未配置"}
    bare = normalize_stock_code(_norm_code(code)).split(".")[-1]
    if not (len(bare) == 6 and bare.isdigit()):
        return {"error": "分时仅支持 A 股"}
    m = await _tdx.minute(bare)
    if not m or not m.get("points"):
        return {"error": "分时暂不可达(TDX 连不上或非交易日)"}
    pts = m["points"]
    prices = [(p["price"], p["time"]) for p in pts if p.get("price")]
    if not prices:
        return {"error": "分时无有效数据"}
    hi = max(prices, key=lambda x: x[0])
    lo = min(prices, key=lambda x: x[0])
    open_p, last_p = prices[0][0], prices[-1][0]
    # 分时点是逐分钟快照, 分钟内的瞬时极值探不到(会差一个 tick);
    # 当日数据以交易所行情的最高/最低为准, 时间取分时路径上最接近该价的点
    from datetime import date as _date
    if str(m.get("date") or "").replace("-", "") == _date.today().strftime("%Y%m%d"):
        try:
            from services.market_data import get_realtime_quotes
            q = (await get_realtime_quotes([bare])).get(bare) or {}
            if q.get("high"):
                hi = (q["high"], min(prices, key=lambda x: abs(x[0] - q["high"]))[1])
            if q.get("low"):
                lo = (q["low"], min(prices, key=lambda x: abs(x[0] - q["low"]))[1])
        except Exception:
            pass
    # 每 ~30 分钟取一个采样点, 给 LLM 看大致路径(避免 240 点刷屏)
    step = max(1, len(pts) // 8)
    path = [{"time": pts[i]["time"], "price": pts[i]["price"]} for i in range(0, len(pts), step)]
    return {"code": bare, "name": await _stock_display_name(bare),
            "date": m.get("date"), "开盘": open_p, "现价/收盘": last_p,
            "最高": {"price": hi[0], "time": hi[1]}, "最低": {"price": lo[0], "time": lo[1]},
            "较最高回落%": round((hi[0] - last_p) / hi[0] * 100, 2) if hi[0] else None,
            "路径采样": path,
            "note": "分时路径采样(约每30分钟一个点) + 高低点带时间; '较最高回落'大=冲高回落/炸板特征。"}


def _us_daily_k_sync(symbol: str, datalen: int) -> list:
    """美股个股日K(新浪 US_MinKService, 裸 symbol 如 AAPL; 返回升序 [{date,close}], 取末尾 N 条)。"""
    import requests as _rq
    url = (f"http://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20t=/"
           f"US_MinKService.getDailyK?symbol={symbol.upper()}&num={max(datalen + 5, 30)}")
    txt = _rq.get(url, headers={"Referer": "https://finance.sina.com.cn/stock/usstock/"}, timeout=6).text
    s = txt.find("=(")
    if s < 0:
        return []
    try:
        end = txt.rfind(");")
        arr = _json.loads(txt[s + 2:end if end > 0 else None])
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    return [{"date": str(d.get("d") or ""), "close": float(d["c"])}
            for d in arr[-(datalen + 1):] if d.get("c")]


def _candle_shape(o, c, h, l):
    """单根K线裸K形态(纯客观描述价格行为, 不含买卖判断)。o/c/h/l=开收高低。"""
    if None in (o, c, h, l) or h <= l or o <= 0:
        return None
    rng = h - l
    mid = (h + l) / 2
    if mid > 0 and rng / mid < 0.005:           # 振幅极小 ≈ 一字
        return "一字线"
    body = abs(c - o)
    bp = body / rng                              # 实体占振幅
    up = (h - max(o, c)) / rng                   # 上影占振幅
    lo = (min(o, c) - l) / rng                   # 下影占振幅
    color = "阳" if c > o else ("阴" if c < o else "平")
    if bp < 0.15:                                # 小实体 = 星线/十字
        if up > 0.4 and lo > 0.4:
            return "十字星(多空分歧)"
        if up > 0.55:
            return "长上影十字(冲高回落)"
        if lo > 0.55:
            return "长下影十字(探底回升)"
        return "小实体星线(分歧)"
    if bp >= 0.7:                                # 大实体
        if up < 0.08 and lo < 0.08:
            return f"光头光脚{color}线(实体饱满)"
        if up < 0.08:
            return f"光头{color}线(收在高点)"
        if lo < 0.08:
            return f"光脚{color}线(开在低点)"
        return f"大{color}线"
    if up > 0.45 and up > lo:
        return f"长上影{color}线(上方有压力)"
    if lo > 0.45 and lo > up:
        return f"长下影{color}线(下方有承接)"
    return f"{color}线"


def _pos_float(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _ma_arrange(c, m5, m10, m20, m60):
    """均线排列(趋势位置标签, 非择时信号): 价相对 MA5/10/20/60 的多空结构。"""
    if None in (m5, m10, m20):
        return None
    if m60 and c > m5 > m10 > m20 > m60:
        return "全多头"
    if c > m5 > m10 > m20:
        return "多头"
    if c > m5 and c > m10:
        return "短多头"
    if m60 and c < m5 < m10 < m20 < m60:
        return "全空头"
    if c < m5 < m10 < m20:
        return "空头"
    return "纠缠"


def _trend_summary(closes: list, vols: list) -> dict:
    """多周期量价摘要(产业链/横向对比用): 5d/20d/60d 涨幅 + 距20日高 + 均线排列 + 量能状态。"""
    n = len(closes)
    if n < 2:
        return {}
    last = closes[-1]
    s = {}
    for k, key in ((5, "pct_5d"), (20, "pct_20d"), (60, "pct_60d")):
        if n > k and closes[-1 - k]:
            s[key] = round((last / closes[-1 - k] - 1) * 100, 1)
    if n >= 20:
        hi20 = max(closes[-20:])
        if hi20 > 0:
            s["dist_20high"] = round((last / hi20 - 1) * 100, 1)   # 距20日高(<=0, 越接近0越靠近新高)
    def ma(k):
        return sum(closes[-k:]) / k if n >= k else None
    arr = _ma_arrange(last, ma(5), ma(10), ma(20), ma(60))
    if arr:
        s["ma"] = arr
    vv = [v for v in vols if v]
    if len(vv) >= 6:
        prior = vv[-6:-1]
        vr = vv[-1] / (sum(prior) / len(prior))
        s["vol"] = "放量" if vr > 1.4 else ("缩量" if vr < 0.7 else "平")
    return s


def _fractals(seq, low: bool, w: int = 3, gap: int = 3) -> list:
    """分型拐点: 某根为左右各 w 根内的最低(low=True)/最高(low=False)。相邻 gap 根内合并取更极端。
    返回 [(idx, value), ...] 升序(已确认, 末尾 w 根不计)。"""
    pts = []
    for i in range(w, len(seq) - w):
        seg = seq[i - w:i + w + 1]
        if seq[i] is None or any(x is None for x in seg):
            continue
        if (low and seq[i] == min(seg)) or (not low and seq[i] == max(seg)):
            pts.append((i, seq[i]))
    if not pts:
        return []
    out = [pts[0]]
    for idx, val in pts[1:]:
        if idx - out[-1][0] <= gap:
            if (low and val < out[-1][1]) or (not low and val > out[-1][1]):
                out[-1] = (idx, val)
        else:
            out.append((idx, val))
    return out


def _intraday_vol_factor():
    """A股盘中: 返回 (是否盘中未收盘, 已走交易时间占全天比例 0~1)。
    非交易日/开盘前/已收盘 → (False, 1.0)。交易时段 9:30-11:30 + 13:00-15:00 共240分钟。"""
    from datetime import datetime, timezone, timedelta
    cst = datetime.now(timezone.utc) + timedelta(hours=8)
    try:
        from services.market_data import _is_a_share_trading_day
        trading = _is_a_share_trading_day(cst.date())
    except Exception:
        trading = cst.weekday() < 5
    if not trading:
        return (False, 1.0)
    m = cst.hour * 60 + cst.minute
    if m < 570 or m >= 900:            # 开盘前 / 已收盘(15:00后)
        return (False, 1.0)
    if m < 690:                        # 9:30-11:30
        elapsed = m - 570
    elif m < 780:                      # 午休(上午120分钟已成交)
        elapsed = 120
    else:                              # 13:00-15:00
        elapsed = 120 + (m - 780)
    return (True, min(max(elapsed, 1) / 240.0, 0.999))


def _vol_at(vols: list, idx: int, w: int = 1):
    """某拐点附近的平均量能(idx 左右各 w 根), 供量价背离比较。"""
    seg = [v for v in vols[max(0, idx - w):idx + w + 1] if v]
    return sum(seg) / len(seg) if seg else None


def _structure_scan(closes: list, highs: list, lows: list, vols: list) -> dict:
    """阶梯式上行结构识别(进二退三框架, 纯客观描述, 不含买卖结论):
    抬高高点+抬高低点=阶梯式上行; 台阶支撑=最近确认的上行低点; 回调量能性质; 跌破抬高低点=结构破位。
    买/卖决策留给用户。"""
    n = len(closes)
    if n < 25:
        return {}
    H = [h if h is not None else closes[i] for i, h in enumerate(highs)]
    L = [l if l is not None else closes[i] for i, l in enumerate(lows)]
    slo = _fractals(L, low=True)
    shi = _fractals(H, low=False)
    last = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    out: dict = {}
    higher_lows = len(slo) >= 2 and slo[-1][1] > slo[-2][1]
    higher_highs = len(shi) >= 2 and shi[-1][1] > shi[-2][1]
    if higher_lows and higher_highs and last > ma20:
        out["阶梯式上行"] = True   # 抬高高点+抬高低点+价在MA20上方(进二退三式)
    elif higher_lows and last > ma20:
        out["抬高低点"] = True     # 仅低点逐级抬高(上行结构雏形)
    if slo:
        sup = slo[-1][1]
        out["台阶支撑"] = round(sup, 3)   # 最近确认的上行台阶(swing low)价位
        out["距支撑%"] = round((last / sup - 1) * 100, 1)
        if higher_lows and last < slo[-2][1]:
            out["结构破位"] = True   # 收盘跌破上一抬高低点, 阶梯式上行结构被破坏
        elif last < sup * 0.99:
            out["跌破近台阶"] = True   # 跌破最近台阶但尚未破坏整体抬高低点
    if shi:
        peak = shi[-1][1]
        vv = [v for v in vols if v]
        if last < peak * 0.99 and len(vv) >= 8:   # 处在近期高点下方=回调中
            recent = sum(vv[-3:]) / 3
            base = sum(vv[-8:-3]) / 5
            if base:
                r = recent / base
                if r < 0.75:
                    out["回调量能"] = "缩量"   # 抛压衰竭(洗盘特征)
                elif r > 1.4:
                    out["回调量能"] = "放量"   # 回调放量(分歧/抛压)
                else:
                    out["回调量能"] = "平量"
    hi_all = max([h for h in H if h is not None] or [0])
    lo_all = min(closes) if closes else 0
    # 头肩顶: 三高(肩-头-肩), 头最高、两肩相近, 高位且已回落; 颈线=头左右两谷较高者。优先于双顶判。
    if len(shi) >= 3:
        (il, ls), (ih, hd), (ir, rs) = shi[-3], shi[-2], shi[-1]
        elevated = hi_all > 0 and hd >= hi_all * 0.97 and lo_all > 0 and hd >= lo_all * 1.2
        rolled = hi_all > 0 and last < hi_all * 0.95
        shoulders_eq = max(ls, rs) > 0 and abs(ls - rs) / max(ls, rs) < 0.06
        head_top = hd > ls * 1.02 and hd > rs * 1.02
        if elevated and rolled and shoulders_eq and head_top:
            t1 = min([L[j] for j in range(il, ih + 1) if L[j] is not None] or [0])
            t2 = min([L[j] for j in range(ih, ir + 1) if L[j] is not None] or [0])
            neck = max(t1, t2)
            out["头肩顶"] = True               # 肩-头-肩, 头部资金共识见顶
            if neck:
                out["颈线"] = round(neck, 3)
                if last < neck:
                    out["跌破颈线"] = True
    # 顶部/派发(M头/双顶, 进二退三镜像): 两相近高点 + 中间回落 = 高位共识破裂。头肩顶已认时跳过。
    if len(shi) >= 2 and "头肩顶" not in out:
        i1, h1 = shi[-2]; i2, h2 = shi[-1]
        mid = [L[j] for j in range(i1, i2 + 1) if L[j] is not None]
        trough = min(mid) if mid else None
        elevated = (hi_all > 0 and max(h1, h2) >= hi_all * 0.97
                    and lo_all > 0 and max(h1, h2) >= lo_all * 1.2)
        rolled_over = hi_all > 0 and last < hi_all * 0.95   # 现价已离高点≥5%, 顶部才成立(贴着新高不算见顶)
        if trough and elevated and rolled_over and (max(h1, h2) - trough) / max(h1, h2) > 0.08:
            if abs(h2 - h1) / max(h1, h2) < 0.04:
                out["双顶"] = True
                out["颈线"] = round(trough, 3)
            elif h2 < h1 * 0.97:
                out["二次冲高未创新高"] = True
                out["颈线"] = round(trough, 3)
            if ("双顶" in out or "二次冲高未创新高" in out) and last < trough:
                out["跌破颈线"] = True
    # 头肩底(倒头肩): 三低(肩-头-肩), 头最低、两肩相近, 低位且已反弹; 颈线=头左右两峰较低者。已是阶梯式上行时不重复报。
    if len(slo) >= 3 and "阶梯式上行" not in out:
        (jl, lls), (jh, lhd), (jr, lrs) = slo[-3], slo[-2], slo[-1]
        depressed = lo_all > 0 and lhd <= lo_all * 1.03 and hi_all > 0 and lhd <= hi_all * 0.85
        bounced = lo_all > 0 and last > lo_all * 1.05   # 已离低点≥5%, 底部才成立
        shoulders_eq = max(lls, lrs) > 0 and abs(lls - lrs) / max(lls, lrs) < 0.06
        head_low = lhd < lls * 0.98 and lhd < lrs * 0.98
        if depressed and bounced and shoulders_eq and head_low:
            p1 = max([H[j] for j in range(jl, jh + 1) if H[j] is not None] or [0])
            p2 = max([H[j] for j in range(jh, jr + 1) if H[j] is not None] or [0])
            neck = min(p1, p2) if (p1 and p2) else max(p1, p2)
            out["头肩底"] = True               # 倒头肩, 底部资金共识企稳
            if neck:
                out["底颈线"] = round(neck, 3)
                if last > neck:
                    out["突破底颈线"] = True     # 站上颈线, 底部结构确认
    # 量价背离(纯量价, 不用 MACD): 价创新高但该高点量能明显小于前高 = 顶背离(上涨共识在透支)
    if len(shi) >= 2 and shi[-1][1] > shi[-2][1] * 1.005:
        v_now, v_prev = _vol_at(vols, shi[-1][0]), _vol_at(vols, shi[-2][0])
        if v_now and v_prev and v_now < v_prev * 0.8:
            out["顶背离"] = "价创新高量能萎缩"     # 新高无量配合, 上涨共识不足
    # 价创新低但量能萎缩 = 底背离(抛压衰竭)
    if len(slo) >= 2 and slo[-1][1] < slo[-2][1] * 0.995:
        v_now, v_prev = _vol_at(vols, slo[-1][0]), _vol_at(vols, slo[-2][0])
        if v_now and v_prev and v_now < v_prev * 0.8:
            out["底背离"] = "价创新低量能萎缩"     # 新低无量, 抛压衰竭
    # 2B法则(斯波朗迪·假突破/假破位): 上行中冲过上一 swing 前高但收盘已跌回前高之下
    # = 突破失败(2B顶, 趋势反转警讯); 镜像: 击穿前低后收盘收回前低之上 = 假破位(2B底)。
    # 只报最近发生的(突破/破位点在末 10 根内), 头肩顶/双顶已认时顶部不重复报。
    if (len(shi) >= 2 and not ({"头肩顶", "双顶"} & out.keys())
            and shi[-1][0] >= n - 10
            and shi[-1][1] > shi[-2][1] * 1.005 and last < shi[-2][1] * 0.998):
        out["2B假突破"] = {"前高": round(shi[-2][1], 3), "冲高到": round(shi[-1][1], 3)}
    if (len(slo) >= 2 and "头肩底" not in out
            and slo[-1][0] >= n - 10
            and slo[-1][1] < slo[-2][1] * 0.995 and last > slo[-2][1] * 1.002):
        out["2B假破位"] = {"前低": round(slo[-2][1], 3), "下探到": round(slo[-1][1], 3)}
    # 收敛三角(对称): 高点逐级降 + 低点逐级抬, 且振幅真的在收缩(近期波幅 < 前段 0.7); 与方向性/反转标签互斥, 避免过报
    _exclusive = {"阶梯式上行", "抬高低点", "结构破位", "跌破近台阶",
                  "双顶", "二次冲高未创新高", "头肩顶", "头肩底"}
    if (len(shi) >= 2 and len(slo) >= 2 and not (_exclusive & out.keys())
            and shi[-1][1] < shi[-2][1] * 0.99 and slo[-1][1] > slo[-2][1] * 1.01):
        rng_now = shi[-1][1] - slo[-1][1]
        rng_prev = shi[-2][1] - slo[-2][1]
        if rng_prev > 0 and rng_now < rng_prev * 0.7:
            out["收敛三角"] = True
    # 跳空缺口(前复权后, 分红除权缺口已抹平, 剩下的多为真实跳空): 取最近一个 >1.5% 的未回补缺口
    for i in range(len(closes) - 1, max(0, len(closes) - 11), -1):
        ph, pl = H[i - 1], L[i - 1]
        if H[i] is None or L[i] is None or ph is None or pl is None:
            continue
        if L[i] > ph * 1.015:        # 向上跳空: 今日最低 > 昨日最高
            if last >= L[i]:         # 未回补(现价仍在缺口上沿之上)
                out["向上跳空缺口"] = [round(ph, 3), round(L[i], 3)]
            break
        if H[i] < pl * 0.985:        # 向下跳空: 今日最高 < 昨日最低
            if last <= H[i]:
                out["向下跳空缺口"] = [round(H[i], 3), round(pl, 3)]
            break
    return out


async def _tool_get_trend(code: str, days: int = 20) -> dict:
    """近 N 日走势: 每日涨跌幅 + 累计。A 股走新浪历史; 港股走腾讯日K; 美股走新浪 US 日K。"""
    from services.market_data import (get_historical_data, normalize_stock_code, is_a_share,
                                       split_stock_code, _kline_tencent_hk)
    raw = normalize_stock_code(_norm_code(code))
    days = max(5, min(int(days or 20), 60))
    need = max(days, 78)   # 多取些历史: 供 60d 涨幅 + MA60 摘要 + 图上 MA 前置数据(展示50根需+20根算MA20)
    market, symbol = split_stock_code(raw)
    _ff = _pos_float
    allbars: list = []  # [(date_str, close, high|None, low|None, vol|None, open|None)] 升序
    vol_kind = "成交量(手)"     # 图下方那栏画的到底是什么(A股默认手; 源退到成交额时改写)
    if is_a_share(raw):
        df = await get_historical_data(raw, need + 5)
        if df is None or df.empty:
            return {"error": "无历史数据"}
        n = len(df)
        dcol = df["日期"].tolist() if "日期" in df.columns else [""] * n
        hcol = df["最高"].tolist() if "最高" in df.columns else [None] * n
        lcol = df["最低"].tolist() if "最低" in df.columns else [None] * n
        ocol = df["开盘"].tolist() if "开盘" in df.columns else [None] * n
        # 量这一列可能退到成交额(源缺成交量时), 两者差着"手×股价"的量级 —— 记下到底是哪个,
        # 图上要照实标, 不然看图的人和模型只能猜(实测茅台一天 2.2万手 / 28亿元, 差 5 个数量级)
        if "成交量" in df.columns:
            vcol, vol_kind = df["成交量"].tolist(), "成交量(手)"
        elif "成交额" in df.columns:
            vcol, vol_kind = df["成交额"].tolist(), "成交额(元)"
        else:
            vcol, vol_kind = [None] * n, ""
        allbars = [(str(d)[:10], float(c), _ff(h), _ff(l), _ff(v), _ff(o))
                   for d, c, h, l, v, o in zip(dcol, df["收盘"].tolist(), hcol, lcol, vcol, ocol)]
    elif market == "HK":
        rows = await asyncio.to_thread(_kline_tencent_hk, f"hk{symbol.zfill(5)}", need + 5)
        allbars = [(str(r.get("date") or "")[:10], float(r["close"]), _ff(r.get("high")), _ff(r.get("low")), _ff(r.get("volume")), _ff(r.get("open")))
                   for r in (rows or []) if r.get("close")]
    elif market == "US":
        rows = await asyncio.to_thread(_us_daily_k_sync, symbol, need)
        allbars = [(str(r.get("date") or "")[:10], float(r["close"]), _ff(r.get("high")), _ff(r.get("low")), _ff(r.get("volume")), _ff(r.get("open")))
                   for r in (rows or []) if r.get("close")]
    else:
        return {"error": "走势暂不支持该市场"}
    if len(allbars) < 2:
        return {"error": "无历史数据"}
    # 校正/补"今天"这根: 前复权日K源盘中常只到昨天(今天那根还没生成), 实时报价才是当日权威。
    # 末根已是今天 → 用实时 O/H/L/C 覆盖; 末根还是昨天而今天已开盘 → 用实时补一根今天(量未知留 None, 避免单位串号)。
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _cst = _dt.now(_tz.utc) + _td(hours=8)
    _today_cst = _cst.strftime("%Y-%m-%d")
    try:
        from services.market_data import _is_a_share_trading_day
        _td_today = _is_a_share_trading_day(_cst.date())
    except Exception:
        _td_today = _cst.weekday() < 5
    _opened = _td_today and (_cst.hour * 60 + _cst.minute) >= 570   # 9:30 起今天报价才有效
    if is_a_share(raw) and allbars and _opened:
        # 东财主源盘中缺今天那根时, 整条改用腾讯日K(含盘中今日bar+真实量)。必须整条换源:
        # 东财量是"股"、腾讯是"手"(差100倍), 混用会污染量比; 全序列同源则单位自洽, vol_ratio(比值)不受影响。
        if allbars[-1][0] != _today_cst:
            try:
                from services.market_data import _kline_tencent_a
                tdf = await asyncio.to_thread(_kline_tencent_a, raw, max(need, 80))
                if (tdf is not None and not tdf.empty and len(tdf) >= 20
                        and str(tdf["日期"].tolist()[-1])[:10] == _today_cst):
                    allbars = [(str(d)[:10], float(c), _ff(h), _ff(l), _ff(v), _ff(o))
                               for d, c, h, l, v, o in zip(tdf["日期"], tdf["收盘"], tdf["最高"],
                                                           tdf["最低"], tdf["成交量"], tdf["开盘"])]
            except Exception as e:
                print(f"[trend] tencent intraday refetch failed for {raw}: {e}")
        # 实时报价校正今天的 close(最权威, 比日K源更新): 末根是今天→覆盖close并扩H/L、保留当根真实量; 否则补一根(量留空兜底)
        try:
            from services.market_data import get_realtime_quotes
            rq = (await get_realtime_quotes([raw])).get(raw) or {}
            rc = _ff(rq.get("price"))
            if rc:
                ro, rh, rl = _ff(rq.get("open")), _ff(rq.get("high")), _ff(rq.get("low"))
                hi, lo = max(rh or rc, rc), min(rl or rc, rc)
                if allbars[-1][0] == _today_cst:
                    _, _, h0, l0, v0, o0 = allbars[-1]
                    allbars[-1] = (_today_cst, rc, max(hi, h0 or rc), min(lo, l0 or rc), v0, ro or o0)
                else:
                    allbars.append((_today_cst, rc, hi, lo, None, ro or rc))   # 腾讯也没有时最后兜底(量留空)
        except Exception as e:
            print(f"[trend] realtime graft/append failed for {raw}: {e}")
    # 盘中: 今天这根的量是"已成交累计"(不完整), 直接比昨天全天必然偏低 → 会误判缩量。
    # 按已走交易时间把末根量折算成全天预估量, 让量比可比; 收盘后 vfrac=1 不动。daily 末条会标"盘中预估"。
    is_intraday, vfrac = _intraday_vol_factor()
    intraday_last = bool(is_intraday and allbars and allbars[-1][0] == _today_cst and vfrac < 1)
    vols_calc = [b[4] for b in allbars]
    if intraday_last and vols_calc and vols_calc[-1]:
        vols_calc = vols_calc[:-1] + [vols_calc[-1] / vfrac]   # 末根→全天线性预估量
    summary = _trend_summary([b[1] for b in allbars], vols_calc)
    try:
        structure = _structure_scan([b[1] for b in allbars], [b[2] for b in allbars],
                                     [b[3] for b in allbars], vols_calc) if is_a_share(raw) else {}
    except Exception:
        structure = {}
    bars = allbars[-(days + 1):]
    code = raw
    closes = [b[1] for b in bars]
    vols = [b[4] for b in bars]
    if intraday_last and vols and vols[-1]:
        vols = vols[:-1] + [vols[-1] / vfrac]   # 末根(今天)量比按全天预估量算, 盘中不拿半天量误判缩量
    # 该股涨跌停幅度(按板块: 科创/创业20、北交所30、主板含ST 10), 让 agent 判封板别按 10% 猜
    bare6 = "".join(ch for ch in raw if ch.isdigit())[-6:]
    lp = _a_limit_pct(bare6, "") if is_a_share(raw) else None
    limit_pct = round(lp * 100, 1) if lp else None
    near = (limit_pct - 0.4) if limit_pct else None   # 容差(价格档位/四舍五入)
    # 每条逐日涨跌挂真实日期 + 当天最高/最低相对昨收的幅度(看历史某天日内摸没摸到涨停/封板还是冲高回落, 无需分时)
    # + 量比(当日量/前5日均量): >1.5 明显放量, <0.7 缩量 —— 配合 pct 看量价(放量上涨/放量滞涨/缩量回调)
    # + open_pct(开盘相对昨收) + shape(裸K形态: 光头光脚/长上影/十字星 等), 让 agent 读单根K线
    daily = []
    for i in range(1, len(bars)):
        pc = closes[i - 1]
        o, c, h, l = bars[i][5], bars[i][1], bars[i][2], bars[i][3]
        e = {"date": bars[i][0], "pct": round((c / pc - 1) * 100, 2)}
        if o and pc > 0:
            e["open_pct"] = round((o / pc - 1) * 100, 2)
        if h and pc > 0:
            e["high_pct"] = round((h / pc - 1) * 100, 2)
        if l and pc > 0:
            e["low_pct"] = round((l / pc - 1) * 100, 2)
        prior = [v for v in vols[max(0, i - 5):i] if v]
        if vols[i] and prior:
            e["vol_ratio"] = round(vols[i] / (sum(prior) / len(prior)), 2)
        if intraday_last and i == len(bars) - 1 and "vol_ratio" in e:
            e["量_盘中预估"] = True   # 今天未收盘, vol_ratio 是按已走时间折算的全天预估量比, 缩/放量待收盘确认
        shape = _candle_shape(o, c, h, l)
        if shape:
            e["shape"] = shape
        # 涨跌停标记(用该股真实涨停幅度判, 非默认10%)
        if near and "high_pct" in e:
            if e["pct"] >= near:
                e["板"] = "收在涨停(封板)"
            elif e["high_pct"] >= near:
                e["板"] = "盘中触及涨停后回落"
        if near and "low_pct" in e and "板" not in e:
            if e["pct"] <= -near:
                e["板"] = "收在跌停"
            elif e["low_pct"] <= -near:
                e["板"] = "盘中触及跌停后回升"
        daily.append(e)
    cum = round((closes[-1] / closes[0] - 1) * 100, 2)
    up = sum(1 for d in daily if d["pct"] > 0)
    out = {
        "code": code, "name": await _stock_display_name(code),
        "days": len(daily), "limit_pct": limit_pct,   # 涨跌停幅度% (科创/创业=20, 主板含ST=10, 北交=30)
        "cum_pct": cum, "up_days": up, "down_days": len(daily) - up,
        "last_date": bars[-1][0], "last_close": round(closes[-1], 3),
        # 多周期量价摘要: pct_5d/pct_20d/pct_60d 涨幅、dist_20high 距20日高、ma 均线排列、vol 量能
        "summary": summary,
        # 阶梯式上行结构(进二退三框架, 客观事实): 阶梯式上行/抬高低点、台阶支撑(价位)+距支撑%、回调量能(缩量=洗盘/放量=分歧)、结构破位(跌破抬高低点)
        "structure": structure,
        # 最近 10 日逐日涨跌, 每条带 date + high_pct/low_pct + 板(涨跌停标记, 已按该股真实涨停幅度判)。最后一条即最新交易日。
        "daily_pct": daily[-min(10, len(daily)):],
    }
    if intraday_last:
        out["盘中"] = True
        out["盘中提示"] = (f"今天({_today_cst})尚未收盘, 已走约{round(vfrac * 100)}%交易时间。"
                          "末根的量比/量能已按已走时间线性折算为全天预估量(粗估), 标 量_盘中预估=true; "
                          "缩量/放量只是当前节奏的预估, 收盘前别下定论, 表述为'按当前节奏预计…/盘中暂…'。")
    # 渲染一张 K线+量能+均线图(结构已标注), 给用户看 + 作模型 gestalt 辅助。图由我方数据画→精确;
    # 数字仍以上面结构化字段为准。chart_url=前端展示; _chart_png_b64=喂给模型(由 _result_content 转 image 块)。
    if is_a_share(raw) and len(allbars) >= 20:
        try:
            from services import chart_render
            png = await asyncio.to_thread(
                chart_render.render_trend_chart, allbars[-78:],
                code=bare6, name=out.get("name") or "", structure=structure,
                vol_label=vol_kind)
            if png:
                out["chart_url"] = chart_render.save_png(png)
                out["_chart_png_b64"] = _b64.b64encode(png).decode()
                out["_chart_media"] = "image/png"
                # 图轴上写了口径, 这里再用文字说一遍: 模型读文字比读轴标签稳
                out["图说明"] = (f"附图下方柱子是{vol_kind}, 不是另一个口径; "
                                "图只用来看形态(量的相对高低/背离), 具体数值一律引用上面的结构化字段")
        except Exception as e:
            print(f"[chart] render failed for {code}: {e}")
    return out


async def _tool_chain_quote(stocks: list) -> dict:
    """批量取一组票的多周期量价摘要(产业链全景/横向对比用): 一次拿整条链每只的 5d/20d/60d/距20高/均线排列/量能。
    stocks: 名称或代码列表。仅 A 股给量价(链上标的基本是 A 股)。"""
    from services.market_data import get_historical_data, is_a_share, normalize_stock_code, get_realtime_quotes
    from datetime import datetime as _dt
    if not isinstance(stocks, list) or not stocks:
        return {"error": "需要传 stocks 列表(股票名称或代码)"}
    stocks = [str(s).strip() for s in stocks if str(s).strip()][:24]

    # 先并发解析代码, 再一次性批量取实时报价(校正各票"今天"那根 close: 日K源当日可能是盘中/延迟脏值)
    async def resolve(s: str):
        try:
            r = await _tool_resolve_stock(s)
            return s, r.get("code"), (r.get("name") or s)
        except Exception:
            return s, None, s
    resolved = await asyncio.gather(*[resolve(s) for s in stocks])
    a_raws = [normalize_stock_code(c) for _, c, _ in resolved
              if c and is_a_share(normalize_stock_code(c))]
    rt = {}
    if a_raws:
        try:
            rt = await get_realtime_quotes(a_raws)
        except Exception:
            rt = {}
    today = _dt.now().strftime("%Y-%m-%d")

    async def one(s: str, code, name: str) -> dict:
        try:
            if not code:
                return {"input": s, "name": name, "error": "未解析到代码"}
            raw = normalize_stock_code(code)
            bare = raw.split(".")[-1] if "." in raw else raw
            if not is_a_share(raw):
                return {"input": s, "code": bare, "name": name, "error": "仅A股给量价"}
            df = await get_historical_data(raw, 70)
            if df is None or df.empty:
                return {"input": s, "code": bare, "name": name, "error": "无历史数据"}
            closes = [float(c) for c in df["收盘"].tolist() if c]
            vcol = df["成交量"].tolist() if "成交量" in df.columns else []
            vols = [_pos_float(v) for v in vcol] if vcol else []
            # 用实时报价覆盖今天的 close(实时为当日权威); 仅当最后一根是今天
            dts = df["日期"].tolist() if "日期" in df.columns else []
            rc = _pos_float((rt.get(raw) or {}).get("price"))
            if rc and closes and dts and str(dts[-1])[:10] == today:
                closes[-1] = rc
            summ = _trend_summary(closes, vols)
            return {"input": s, "code": bare, "name": name, **summ}
        except Exception as e:
            return {"input": s, "error": str(e)[:60]}

    rows = await asyncio.gather(*[one(s, c, n) for s, c, n in resolved])
    return {"stocks": rows,
            "note": "每只: pct_5d/pct_20d/pct_60d 涨幅%、dist_20high 距20日高%(<=0)、ma 均线排列(全多头/多头/短多头/纠缠/空头)、vol 量能(放量/平/缩量)。"
                    "产业链全景按上游→下游环节排列各标的, 量价强弱横向比。当日 close 已用实时报价校正。"}


_readurl_cache: dict = {}


def _scrape_firecrawl(url: str, proxies) -> dict | None:
    """Firecrawl 免 key /v1/scrape。配额(约5000/月)用完会非200, 返 None 让上层转备用源。"""
    import requests as _rq
    try:
        r = _rq.post("https://api.firecrawl.dev/v1/scrape",
                     json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                     timeout=45, proxies=proxies)
        if r.status_code != 200:
            return None
        data = (r.json() or {}).get("data") or {}
        md = (data.get("markdown") or "").strip()
        if not md:
            return None
        return {"title": (data.get("metadata") or {}).get("title") or "", "markdown": md, "via": "firecrawl"}
    except Exception:
        return None


def _scrape_jina(url: str, proxies) -> dict | None:
    """Jina Reader 免 key 备用源 (r.jina.ai), 正文较干净。"""
    import requests as _rq
    try:
        r = _rq.get("https://r.jina.ai/" + url, timeout=50, proxies=proxies, allow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return None
        txt = r.text
        title = ""
        if txt.startswith("Title:"):
            title = txt.split("\n", 1)[0][6:].strip()
        body = txt.split("Markdown Content:", 1)[-1].strip()
        return {"title": title, "markdown": body or txt.strip(), "via": "jina"}
    except Exception:
        return None


def _fetch_url_markdown_sync(url: str) -> dict:
    """抓网页正文 markdown: Firecrawl 主(免key, 约5000/月), 用完/报错自动转 Jina Reader 备用(均免key, 走代理)。"""
    import time as _t
    c = _readurl_cache.get(url)
    if c and _t.time() - c[1] < 600:
        return c[0]
    from services import proxy_config
    px = proxy_config.get_proxy()
    proxies = {"http": px, "https": px} if px else None
    res = _scrape_firecrawl(url, proxies) or _scrape_jina(url, proxies)
    if not res:
        return {"error": "两个抓取源都失败(配额/网络/反爬), 退回用 web_search 摘要"}
    md = res["markdown"]
    out = {"title": res.get("title") or "", "markdown": md[:7000],
           "truncated": len(md) > 7000, "via": res.get("via")}
    _readurl_cache[url] = (out, _t.time())
    return out


async def _tool_read_url(url: str) -> dict:
    """抓取指定网页的正文(干净 markdown), 用于把 web_search 找到的某篇文章读全。"""
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "需要 http(s) 链接"}
    out = await asyncio.to_thread(_fetch_url_markdown_sync, url)
    out["url"] = url
    out["note"] = "网页正文(markdown), 来源以该 url 为准, 引用按 [联网] 标来源+日期。"
    return out


async def _tool_get_news(code: str) -> dict:
    """个股最近新闻。A 股/港股走东财, 美股走 Yahoo。

    只有美股换源: 东财的 stock_news_em 对美股是按关键词搜中文新闻, 实测 MRVL 十条里
    最新 8-16 且一半跟本股无关。港股反过来 —— 东财又准又是中文当天的, 而 Yahoo 对
    0700.HK 返回的是整片市场的泛新闻, 换过去是变差。
    """
    from api.news_routes import _fetch_holding_news_sync   # 按市场选源, 持仓新闻聚合同一份逻辑
    items = await asyncio.to_thread(_fetch_holding_news_sync, _norm_code(code))
    if not items:
        return {"news": [], "note": "暂无个股新闻"}
    news = [{"title": it["title"], "summary": (it.get("content") or "")[:140],
             "time": it["time"], "source": it["source"]} for it in items[:10]]
    # 把"最新一条是什么时候"显式给出来: 模型光看列表分不清 10 条齐全代表消息面已覆盖,
    # 还是最新那条其实早于它要解释的那次异动。
    out = {"news": news}
    latest = max((n["time"] for n in news if n["time"]), default="")
    if latest:
        out["latest_time"] = latest
    return out


def _em_secid(code: str) -> str:
    """A 股 6 位代码 → 东财 secid。沪(6/9/5)=1., 深(0/2/3/1)=0.。"""
    code = _norm_code(code)
    return ("1." if code[:1] in ("6", "9", "5") else "0.") + code


_fflow_cache: dict = {}


def _fetch_realtime_fund_flow(secid: str, headers: dict, hosts: list) -> dict | None:
    """今日主力资金流(东财 push2 实时, ulist.np/get?secids=): 与榜单 clist f62 同源, 盘中实时滚动。
    f62 主力净 = f66 超大单净 + f72 大单净; f78 中单净; f84 小单净 (单位元)。
    注意: stock/get 不填这些资金流字段, 必须走 ulist.np。"""
    import requests as _rq
    import time as _t
    from datetime import datetime, timezone, timedelta
    params = {"secids": secid, "invt": "2", "fltt": "2",
              "fields": "f12,f14,f62,f66,f72,f78,f84,f184"}
    for i in range(6):
        try:
            diff = (_rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/ulist.np/get", params=params,
                            timeout=7, headers=headers).json().get("data") or {}).get("diff") or []
            if diff and diff[0].get("f62") not in (None, "", "-"):
                d = diff[0]
                today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
                _g = lambda k: round(float(d.get(k) or 0) / 1e8, 2)
                return {"date": today, "main": _g("f62"), "xlarge": _g("f66"),
                        "big": _g("f72"), "mid": _g("f78"), "small": _g("f84")}
        except Exception:
            _t.sleep(0.3)
    return None


def _fetch_fund_flow_sync(code: str) -> dict:
    """个股主力资金流: 今日各单类净额走 push2 实时(stock/get f62/f66/f72/f78/f84, 与榜单 f62 同源, 盘中实时);
    近几日主力净流入趋势走 fflow/kline 日线。两个口径统一: today 永远是实时值, 不再用滞后的历史末根。
    fflow/kline 每行: 日期,主力净,小单净,中单净,大单净,超大单净 (单位元)。"""
    import requests as _rq
    import time as _t
    ck = f"ff_{code}"
    c = _fflow_cache.get(ck)
    if c and _t.time() - c[1] < 120:  # 实时口径, 缓存收紧到 2 分钟
        return c[0]
    secid = _em_secid(code)
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
    his_hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "push2his.eastmoney.com"]
    rt = _fetch_realtime_fund_flow(secid, hdr, hosts)  # 今日实时主力资金流(权威)
    # 历史趋势序列(近几日), 末根可能滞后, 仅用于画趋势
    params = {"lmt": "8", "klt": "101", "secid": secid,
              "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56"}
    rows = []
    for i in range(9):
        host = his_hosts[i % len(his_hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/stock/fflow/kline/get", params=params, timeout=7, headers=hdr)
            kl = (r.json().get("data") or {}).get("klines")
            if kl:
                for ln in kl[-6:]:
                    p = ln.split(",")
                    if len(p) < 6:
                        continue
                    rows.append({"date": p[0], "main": round(float(p[1]) / 1e8, 2),
                                 "small": round(float(p[2]) / 1e8, 2), "mid": round(float(p[3]) / 1e8, 2),
                                 "big": round(float(p[4]) / 1e8, 2), "xlarge": round(float(p[5]) / 1e8, 2)})
                break
        except Exception:
            _t.sleep(0.3)
    # today 优先用实时值; 实时拿不到才回退历史末根
    today = rt or (rows[-1] if rows else None)
    if today is None:
        return {"error": "资金流暂不可达(东财源抖动)"}
    series = [{"date": r["date"], "主力净流入亿": r["main"]} for r in rows]
    # 用实时今日值覆盖/补上趋势序列里的同日点, 保持序列末尾与 today 一致
    if rt:
        if series and series[-1]["date"] == rt["date"]:
            series[-1]["主力净流入亿"] = rt["main"]
        else:
            series.append({"date": rt["date"], "主力净流入亿": rt["main"]})
    out = {"unit": "亿元", "today": today, "today_realtime": bool(rt),
           "main_net_series": series}
    _fflow_cache[ck] = (out, _t.time())
    return out


async def _tool_fund_flow(code: str) -> dict:
    """个股主力资金流: 今日主力/超大单/大单/中单/小单净额 + 近几日主力净流入趋势(判断谁在买/在卖)。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "资金流仅支持 A 股"}
    out = await asyncio.to_thread(_fetch_fund_flow_sync, raw)
    if "error" not in out:
        t = out["today"]
        out["note"] = (f"最新交易日主力净{'流入' if t['main'] >= 0 else '流出'}{abs(t['main'])}亿"
                       f"(超大单{t['xlarge']}/大单{t['big']}/中单{t['mid']}/小单{t['small']}亿); "
                       "主力=超大单+大单, 正=资金净买入; 该值具体属哪个交易日见系统提示的交易日状态。")
    return out


_lhb_cache: dict = {}


def _fetch_lhb_sync(code: str = "", days: int = 12) -> dict:
    """龙虎榜(东财, akshare stock_lhb_detail_em): 近 N 日上榜明细。
    code 给定→该股上榜记录; 否则→最近交易日净买额排序(游资/机构在打哪些票)。"""
    import time as _t
    import datetime as _dt
    ck = f"lhb_{code}_{days}"
    c = _lhb_cache.get(ck)
    if c and _t.time() - c[1] < 1800:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    end = _dt.date.today()
    start = end - _dt.timedelta(days=days)
    df = ak.stock_lhb_detail_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    if df is None or df.empty:
        return {"error": "近期无龙虎榜数据"}
    bare = _norm_code(code)
    if bare:
        df = df[df["代码"].astype(str) == bare]
        if df.empty:
            return {"code": bare, "on_list": False, "note": f"{bare} 近{days}日未上龙虎榜"}
    else:
        # 市场视图: 取最近上榜日, 按净买额绝对值排序的活跃票
        df = df.sort_values("上榜日", ascending=False)
        latest = df["上榜日"].iloc[0]
        df = df[df["上榜日"] == latest].copy()
        df["_abs"] = df["龙虎榜净买额"].abs()
        df = df.sort_values("_abs", ascending=False).head(15)

    def row(r):
        return {"code": str(r["代码"]), "name": r["名称"], "date": str(r["上榜日"]),
                "change_pct": round(float(r["涨跌幅"]), 2) if r["涨跌幅"] == r["涨跌幅"] else None,
                "净买额亿": round(float(r["龙虎榜净买额"]) / 1e8, 2) if r["龙虎榜净买额"] == r["龙虎榜净买额"] else None,
                "解读": r.get("解读"), "上榜原因": r.get("上榜原因")}
    recs = [row(r) for _, r in df.iterrows()]
    out = ({"code": bare, "on_list": True, "records": recs} if bare
           else {"latest_date": recs[0]["date"] if recs else None, "top_by_net_buy": recs,
                 "note": "净买额正=游资/机构净买入(资金做多), 负=净卖出; 看解读/上榜原因辨别机构席位还是游资。"})
    _lhb_cache[ck] = (out, _t.time())
    return out


async def _tool_lhb(code: str = "", date: str = "") -> dict:
    """龙虎榜: code+date→该股该上榜日的席位明细(买卖前五营业部+金额+画像);
    仅 code→该股近期上榜汇总; 都不给→最近交易日资金净买额榜。仅 A 股。"""
    if code:
        from services.market_data import normalize_stock_code, is_a_share
        if not is_a_share(normalize_stock_code(_norm_code(code))):
            return {"error": "龙虎榜仅支持 A 股"}
    try:
        if code and date:
            from services.lhb_detail import lhb_seat_detail
            return await lhb_seat_detail(_norm_code(code), date)
        return await asyncio.to_thread(_fetch_lhb_sync, code or "", 12)
    except Exception as e:
        return {"error": f"龙虎榜获取失败: {e}"}


# ssbk 里混了一堆"市场状态/指数成分"标签, 不是真正的行业/概念, 过滤掉
_SSBK_NOISE = {
    "题材股", "趋势股", "融资融券", "沪股通", "深股通", "标准普尔", "富时罗素", "机构重仓",
    "小盘成长", "小盘股", "中盘股", "大盘股", "白马股", "绩优股", "预盈预增", "MSCI中国",
    "上证180", "上证380", "上证50", "沪深300", "中证500", "创业板综", "深证成指",
    "东方财富热股", "央企改革", "国企改革", "央国企改革",
    # 风格/规模/持股标签: 它们不是"这只票做什么的", 却和真概念混在一条列表里,
    # 把定义性的那个(风华高科的 MLCC 排在第 16 个, 卡在 20 条上限边上)往后挤。
    "基金重仓", "深成500", "深证100R", "中小100", "AH股", "转债标的",
    "周期股", "百元股", "低价股", "证金持股", "汇金持股", "社保重仓", "养老金", "QFII重仓",
    "创业成份", "中证100", "科创50", "权重股",
}
# 「成长/价值/风格」这类后缀一律当噪声: 中盘成长 / 大盘价值 / 科技风格 …
_SSBK_NOISE_KW = ["板块", "新高", "新低", "涨停", "跌停", "首板", "多板", "振幅", "换手",
                  "昨日", "今日", "近期", "连板", "风格", "成长", "价值", "指数样本"]
_concepts_cache: dict = {}


def _fetch_stock_concepts_sync(code: str) -> dict:
    """个股所属板块/概念 + 核心题材(东财 F10 CoreConception)。code=裸6位 → SZ/SH 前缀。"""
    import requests as _rq
    import time as _t
    bare = _norm_code(code)
    ck = f"cc_{bare}"
    c = _concepts_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    em_code = ("SH" if bare[:1] in ("6", "9", "5") else "SZ") + bare
    try:
        j = _rq.get("https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax",
                    params={"code": em_code}, timeout=8, headers={"User-Agent": "Mozilla/5.0"}).json()
    except Exception:
        return {"error": "所属概念暂不可达"}
    boards = []
    for b in (j.get("ssbk") or []):
        nm = (b.get("BOARD_NAME") or "").strip()
        # 东财给指数成分的标签一律带下划线后缀(上证180_ / HS300_ / 央视50_), 拿它当判据
        # 比逐个枚举可靠 —— 指数名年年变, 枚举必漏
        if not nm or nm.endswith("_") or nm in _SSBK_NOISE \
                or any(k in nm for k in _SSBK_NOISE_KW):
            continue
        boards.append(nm)
    themes = []
    for t in (j.get("hxtc") or []):
        kw = (t.get("KEYWORD") or "").strip()
        if kw and kw != "经营范围" and kw not in themes:
            themes.append(kw)
    out = {"code": bare, "boards": boards[:30], "core_themes": themes[:8],
           "note": "boards=所属行业/概念板块(已滤掉指数成分等噪声标签); core_themes=核心题材/主营。可与热门概念榜交叉看是不是踩在资金主线上。"}
    _concepts_cache[ck] = (out, _t.time())
    return out


_profile_cache: dict = {}


def _fetch_company_profile_sync(bare: str) -> dict:
    """公司是做什么的: 东财 F10 公司简介(ORG_PROFILE) + 细分行业(EM2016) + 主营构成(产品/地区收入占比+毛利率)。"""
    import requests as _rq
    import time as _t
    ck = f"prof_{bare}"
    c = _profile_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    pfx = "SH" if bare[:1] in ("6", "9", "5") else "SZ"
    hdr = {"User-Agent": "Mozilla/5.0"}
    out: dict = {"code": bare}
    # 1) 公司简介 + 行业
    try:
        d = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={pfx}{bare}",
                    timeout=9, headers=hdr).json()
        jb = (d.get("jbzl") or [{}])[0]
        prof = (jb.get("ORG_PROFILE") or "").strip()
        out.update({
            "name": jb.get("ORG_NAME") or jb.get("SECURITY_NAME_ABBR"),
            "industry": jb.get("EM2016") or jb.get("INDUSTRYCSRC1"),
            # 这里存全文。原来在取数层就截到 400 字, 而这个函数同时喂给 agent 工具和
            # /api/market/company(K线头部的公司简介) —— 前端点开"完整简介"看到的也是
            # 切一半的(石药创新 647 字丢 247, 中国电信 688 字丢 288, 且切在句子中间)。
            # token 预算是 agent 那一侧的事, 由 _tool_company_profile 按句截。
            "profile": prof or None,
            "h_share": jb.get("STR_CODEH"),
            "employees": jb.get("EMP_NUM"),
        })
    except Exception:
        pass
    # 2) 主营构成(优先按产品, 其次地区, 再行业); ITEM_NAME 占比 毛利率
    try:
        d2 = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code={pfx}{bare}",
                     timeout=9, headers=hdr).json()
        rows = d2.get("zygcfx") or []
        # 只取最新报告期(zygcfx 含多期, 跨期会重复)
        if rows:
            latest = max(str(r.get("REPORT_DATE") or "") for r in rows)
            rows = [r for r in rows if str(r.get("REPORT_DATE") or "") == latest]
        # 同一期里优先按产品(2), 其次地区(3), 再行业(1)
        for prefer in ("2", "3", "1"):
            sub = [r for r in rows if str(r.get("MAINOP_TYPE")) == prefer]
            if sub:
                rows = sub
                break
        comp = []
        for r in rows[:6]:
            ratio = r.get("MBI_RATIO")
            gross = r.get("GROSS_RPOFIT_RATIO")
            comp.append({
                "项目": r.get("ITEM_NAME"),
                "营收占比%": round(float(ratio) * 100, 1) if ratio not in (None, "") else None,
                "毛利率%": round(float(gross) * 100, 1) if gross not in (None, "") else None,
            })
        if comp:
            out["main_business"] = comp
            out["report_date"] = str((rows[0].get("REPORT_DATE") or ""))[:10]
    except Exception:
        pass
    # 3) 控股/实控背景(判断国资/央企/地方国企/中科院系/民营/外资 等性质)
    try:
        d3 = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={pfx}{bare}",
                     timeout=9, headers=hdr).json()
        sj = (d3.get("sjkzr") or [{}])[0]
        ctrl = (sj.get("HOLDER_NAME") or "").strip()
        if ctrl and ctrl != "None":
            out["controller"] = ctrl
        sdgd = d3.get("sdgd") or []
        if sdgd:
            latest = max(str(r.get("END_DATE") or "") for r in sdgd)
            top = []
            for r in [x for x in sdgd if str(x.get("END_DATE") or "") == latest][:3]:
                nm = (r.get("HOLDER_NAME") or "").strip()
                if not nm or nm == "None":
                    continue
                ratio = r.get("HOLD_NUM_RATIO")
                top.append({"股东": nm,
                            "持股%": round(float(ratio), 2) if ratio not in (None, "", "None") else None})
            if top:
                out["top_holders"] = top
    except Exception:
        pass
    if not out.get("profile") and not out.get("main_business"):
        return {"error": "公司简介暂不可达(东财F10抖动)"}
    out["note"] = ("profile=公司简介(做什么); industry=细分行业; main_business=主营构成(收入占比+毛利率); "
                   "controller=实际控制人, top_holders=第一/前三大股东(据股东名判断国资/央企/地方国企/中科院系/民营/外资等公司性质)。仅 A 股。")
    _profile_cache[ck] = (out, _t.time())
    return out


_PROFILE_TOOL_CHARS = 400      # 喂模型的简介长度上限; UI 那侧要全文, 见 clip_profile


def clip_profile(text: str, limit: int = _PROFILE_TOOL_CHARS) -> str:
    """按句截断给模型看的公司简介。

    为什么不直接切字符: 东财的简介动辄 600-700 字, 硬切会停在半句上("公司为百事可乐、
    可口可" ), 模型读到的是残句。这里在 limit 之前的最后一个句号处断开, 并显式标注
    "已截断", 让模型知道后面还有内容而不是当成全文。
    找不到句号(整段没有标点)才退回硬切。
    """
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    cut = max(head.rfind("。"), head.rfind("；"), head.rfind("!"), head.rfind("?"))
    if cut >= limit // 2:                      # 句号太靠前就不用它, 否则丢掉大半
        head = head[:cut + 1]
    return head.rstrip("，,、 ") + f"…(简介共 {len(t)} 字, 已截断)"


async def _tool_company_profile(code: str) -> dict:
    """公司是做什么的: 公司简介 + 细分行业 + 主营构成。A 股另含控股/实控人; 港美股另含上市日期/总部/员工/财报币种/财年。"""
    from services.market_data import normalize_stock_code, is_a_share
    hu = _hkus(code)
    if hu:
        from services import foreign_f10 as ff
        fn = ff.hk_profile if hu[0] == "HK" else ff.us_profile_f10
        out = await asyncio.to_thread(fn, hu[1])
        if isinstance(out, dict) and not out.get("error"):
            out = {**out}
            if out.get("profile"):
                out["profile"] = clip_profile(out["profile"])
            if out.get("主营业务"):
                out["主营业务"] = clip_profile(out["主营业务"], 300)
        return out
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "公司简介支持 A 股、港股、美股"}
    bare = raw.split(".")[-1] if "." in raw else raw
    out = await asyncio.to_thread(_fetch_company_profile_sync, bare)
    # 取数层存全文(K线头部的"完整简介"要用), 只在喂模型这一侧按句截
    if isinstance(out, dict) and out.get("profile"):
        out = {**out, "profile": clip_profile(out["profile"])}
    return out


async def _tool_stock_concepts(code: str) -> dict:
    """个股所属行业/概念板块 + 核心题材。回答'这只票属于哪个概念、是不是踩在资金主线上'时用。
    A 股给概念板块与题材; 港股给东财行业与分类层级、港股通资格; 美股给 板块/行业(Yahoo 分类) 与曾用名。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if raw.upper().startswith("US.") or (not is_a_share(raw) and _re.fullmatch(r"[A-Za-z.]{1,6}", raw)):
        from services import us_fundamentals as usf
        return await asyncio.to_thread(usf.us_profile, raw)
    hu = _hkus(code)
    if hu and hu[0] == "HK":
        from services import foreign_f10 as ff
        return await asyncio.to_thread(ff.hk_concepts, hu[1])
    if not is_a_share(raw):
        return {"error": "所属概念支持 A 股、港股、美股"}
    return await asyncio.to_thread(_fetch_stock_concepts_sync, code)


_val_cache: dict = {}


def _fetch_valuation_sync(code: str) -> dict:
    """估值快照(东财 stock/get): PE(TTM)/PB/总市值/流通市值/行业。f162=PE×100, f167=PB×100, f116=总市值元。"""
    import requests as _rq
    import time as _t
    ck = f"val_{code}"
    c = _val_cache.get(ck)
    if c and _t.time() - c[1] < 600:
        return c[0]
    secid = _em_secid(code)
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
    for i in range(9):
        host = hosts[i % len(hosts)]
        try:
            d = _rq.get(f"https://{host}/api/qt/stock/get", timeout=7,
                        params={"secid": secid, "fields": "f58,f43,f162,f167,f116,f117,f127"},
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}).json().get("data")
            if d and d.get("f58"):
                pe = d.get("f162"); pb = d.get("f167"); mcap = d.get("f116")
                out = {"行业": d.get("f127"),
                       "PE_TTM": round(pe / 100, 2) if isinstance(pe, (int, float)) and pe not in (0, None) else None,
                       "PB": round(pb / 100, 2) if isinstance(pb, (int, float)) and pb not in (0, None) else None,
                       "总市值亿": round(mcap / 1e8, 1) if isinstance(mcap, (int, float)) and mcap else None,
                       "流通市值亿": round(d.get("f117") / 1e8, 1) if isinstance(d.get("f117"), (int, float)) and d.get("f117") else None}
                _val_cache[ck] = (out, _t.time())
                return out
        except Exception:
            _t.sleep(0.3)
    return {}


_fin_cache: dict = {}


def _fetch_financials_sync(code: str) -> dict:
    """财务摘要(东财, akshare stock_financial_abstract): 取最新报告期的关键指标。"""
    import time as _t
    ck = f"fin_{code}"
    c = _fin_cache.get(ck)
    if c and _t.time() - c[1] < 3600:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_financial_abstract(symbol=code)
    if df is None or df.empty:
        return {}
    date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    date_cols.sort(reverse=True)  # 最新在前
    by_ind = {}
    for _, r in df.iterrows():
        by_ind[str(r["指标"]).strip()] = r

    def latest(ind):
        row = by_ind.get(ind)
        if row is None:
            return None, None
        for dc in date_cols:
            v = row.get(dc)
            if v is not None and v == v and str(v) != "":  # 非 NaN 非空
                try:
                    return float(v), dc
                except (ValueError, TypeError):
                    return None, None
        return None, None

    def yi(ind):  # 元 → 亿
        v, dt = latest(ind)
        return (round(v / 1e8, 2) if v is not None else None), dt

    rev, rdt = yi("营业总收入")
    profit, _ = yi("归母净利润")
    rev_g, _ = latest("营业总收入增长率")
    profit_g, _ = latest("归属母公司净利润增长率")
    roe, _ = latest("净资产收益率(ROE)")
    gross, _ = latest("毛利率")
    netm, _ = latest("销售净利率")
    debt, _ = latest("资产负债率")
    eps, _ = latest("基本每股收益")
    bvps, _ = latest("每股净资产")
    out = {"报告期": rdt,
           "营业总收入亿": rev, "营收同比增长%": round(rev_g, 1) if rev_g is not None else None,
           "归母净利润亿": profit, "净利同比增长%": round(profit_g, 1) if profit_g is not None else None,
           "ROE%": round(roe, 2) if roe is not None else None,
           "毛利率%": round(gross, 1) if gross is not None else None,
           "净利率%": round(netm, 1) if netm is not None else None,
           "资产负债率%": round(debt, 1) if debt is not None else None,
           "每股收益元": round(eps, 3) if eps is not None else None,
           "每股净资产元": round(bvps, 2) if bvps is not None else None}
    _fin_cache[ck] = (out, _t.time())
    return out


def _fetch_us_financials_sync(symbol: str) -> dict:
    """美股最近年报财务(东财, akshare em 指标): 营收/净利及同比、毛利率/净利率/ROE/负债率/EPS。金额→亿(原币种)。"""
    import time as _t
    ck = f"fin_US_{symbol}"
    c = _fin_cache.get(ck)
    if c and _t.time() - c[1] < 3600:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    try:
        df = ak.stock_financial_us_analysis_indicator_em(symbol=symbol.upper(), indicator="年报")
    except Exception as e:
        return {"error": f"美股财务源不可达: {e}"}
    if df is None or df.empty:
        return {}
    r = df.iloc[0].to_dict()

    def f(k, nd=2):
        v = r.get(k)
        try:
            return round(float(v), nd)
        except (ValueError, TypeError):
            return None

    def yi(k):
        v = r.get(k)
        try:
            return round(float(v) / 1e8, 2)
        except (ValueError, TypeError):
            return None
    from services.foreign_f10 import _cur
    cur = _cur(r.get("CURRENCY_ABBR") or r.get("CURRENCY")) or "USD"
    out = {"报告期": str(r.get("REPORT_DATE") or "")[:10], "口径": "最近年报",
           "营业收入亿": yi("OPERATE_INCOME"), "营收同比增长%": f("OPERATE_INCOME_YOY", 1),
           "归母净利润亿": yi("PARENT_HOLDER_NETPROFIT"), "净利同比增长%": f("PARENT_HOLDER_NETPROFIT_YOY", 1),
           "毛利率%": f("GROSS_PROFIT_RATIO", 1), "净利率%": f("NET_PROFIT_RATIO", 1),
           "ROE%": f("ROE_AVG", 2), "资产负债率%": f("DEBT_ASSET_RATIO", 1),
           "基本EPS": f("BASIC_EPS", 3), "金额币种": cur}
    _fin_cache[ck] = (out, _t.time())
    return out


async def _tool_fundamentals_foreign(market: str, sym: str) -> dict:
    """港美股基本面 + 估值。估值按现价与(折算到报价币种的)每股数现算, 换算过程逐项列出。"""
    from services.market_data import get_realtime_quotes
    from services import foreign_f10 as ff
    code = f"{market}.{sym}"
    price = None
    try:
        price = ((await get_realtime_quotes([code])).get(code) or {}).get("price")
    except Exception:
        pass
    if market == "HK":
        out = await asyncio.to_thread(ff.hk_fundamentals, sym, price)
        if price is None and not out.get("error"):
            out["valuation"]["note"] = "现价暂不可得, PE/PB/市值未算"
        return out
    val, fin = await asyncio.gather(asyncio.to_thread(ff.us_fundamentals_valuation, sym, price),
                                    asyncio.to_thread(_fetch_us_financials_sync, sym), return_exceptions=True)
    val = val if isinstance(val, dict) else {}
    fin = fin if isinstance(fin, dict) else {}
    if not val and (not fin or fin.get("error")):
        return {"error": "美股基本面暂不可达(东财接口抖动或限流)"}
    cur = fin.get("金额币种") or val.get("财报披露币种") or "USD"
    return {"valuation": val, "financials": fin,
            "note": f"财务金额单位亿{cur}(原币), 取最近年报, 同比为 YoY; PE_TTM/PB 按现价与近四季 EPS、最新 BPS 现算"
                    "(非美元披露的已按即期汇率折成美元, 换算过程见 EPS_TTM/BPS 字段)。"}


async def _tool_fundamentals(code: str) -> dict:
    """个股基本面+估值。A 股: 营收/净利及同比、ROE/毛利率/净利率、负债率、EPS + PE(TTM)/PB/总市值/行业;
    港美股: 同类财务 + 按现价现算的 PE_TTM/PB/总市值, 财报币种与报价币种不同时逐项列出折算。"""
    from services.market_data import normalize_stock_code, is_a_share
    hu = _hkus(code)
    if hu:
        return await _tool_fundamentals_foreign(*hu)
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "基本面暂不支持该市场"}
    bare = _norm_code(code)
    val, fin = await asyncio.gather(
        asyncio.to_thread(_fetch_valuation_sync, bare),
        asyncio.to_thread(_fetch_financials_sync, bare),
        return_exceptions=True,
    )
    val = val if isinstance(val, dict) else {}
    fin = fin if isinstance(fin, dict) else {}
    if not val and not fin:
        return {"error": "基本面暂不可达(数据源抖动)"}
    return {"valuation": val, "financials": fin,
            "note": "营收/净利为报告期累计值, 同比增长是 YoY; PE_TTM/PB 为当前估值, 已是真实倍数(非百分比)。"}


_COMMODITY_CN = {"沪金": "黄金", "沪银": "白银", "沪铜": "铜", "沪铝": "铝", "沪锌": "锌",
                 "沪铅": "铅", "沪镍": "镍", "沪锡": "锡"}


async def _tool_commodity(code: str) -> dict:
    """个股关联的大宗商品期货价(有色: 铜/铝/金/锌/镍/锡 等走上期所连续合约)。看商品价能否解释有色股涨跌。"""
    from services.market_data import normalize_stock_code, is_a_share, get_commodity_for_stock
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "商品价仅支持 A 股"}
    try:
        c = await get_commodity_for_stock(raw)
    except Exception as e:
        return {"error": str(e)}
    if not c:
        return {"mapped": False, "note": "该票无对应交易所期货(钨/锑/稀土/锂等小金属无连续合约), 商品价不可得; 看现货价请结合新闻/板块。"}
    return {"mapped": True, "commodity": c.get("label"), "price": c.get("price"),
            "change_pct": c.get("change_pct"), "prev": c.get("prev"),
            "note": f"{c.get('label')}期货(上期所连续合约)现价; 有色股价常与对应金属价同步, 可佐证涨跌驱动。"}


_peers_cache: dict = {}


def _fetch_peers_sync(code: str) -> dict:
    """同行横向对比(东财): 先取个股主行业板块(f198=BKxxxx), 再拉该板块成分股的涨幅/PE/PB/主力净流入。"""
    import requests as _rq
    import time as _t
    ck = f"peers_{code}"
    c = _peers_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    secid = _em_secid(code)
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
    bk = ind = None
    for i in range(6):
        try:
            d = _rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/stock/get", timeout=7, headers=hdr,
                        params={"secid": secid, "fields": "f127,f198"}).json().get("data")
            if d and d.get("f198"):
                bk = d.get("f198"); ind = d.get("f127"); break
        except Exception:
            _t.sleep(0.3)
    if not bk:
        return {"error": "拿不到所属行业板块"}
    params = {"pn": "1", "pz": "30", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f62",
              "fs": f"b:{bk}", "fields": "f12,f14,f3,f9,f23,f62"}
    for i in range(9):
        try:
            diff = (_rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/clist/get", params=params, timeout=7,
                            headers=hdr).json().get("data") or {}).get("diff")
            if diff:
                rows = []
                for x in diff:
                    try:
                        rows.append({"code": x.get("f12"), "name": x.get("f14"),
                                     "涨跌幅": x.get("f3"),
                                     "PE": x.get("f9") if isinstance(x.get("f9"), (int, float)) and x.get("f9") not in (0, "-") else None,
                                     "PB": x.get("f23") if isinstance(x.get("f23"), (int, float)) and x.get("f23") not in (0, "-") else None,
                                     "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2)})
                    except (ValueError, TypeError):
                        continue
                # 按主力净流入排序, 取前12, 但确保目标票在内
                rows.sort(key=lambda r: (r["主力净流入亿"] or -999), reverse=True)
                top = rows[:12]
                if code not in [r["code"] for r in top]:
                    me = [r for r in rows if r["code"] == code]
                    top = (top[:11] + me) if me else top
                out = {"行业": ind, "板块": bk, "peers": top,
                       "note": f"同属【{ind}】板块, 按最新交易日主力净流入排序; PE/PB 横向比可看谁贵谁便宜, 涨跌幅看谁领涨。主力净流入亿为榜单快照, 用于榜内横向比较; 单只票的精确金额以 get_fund_flow 为准。"}
                _peers_cache[ck] = (out, _t.time())
                return out
        except Exception:
            _t.sleep(0.3)
    return {"error": "板块成分暂不可达"}


async def _tool_peers(code: str) -> dict:
    """同行横向对比: 看目标票在同业里贵不贵、强不强、资金偏好谁。
    A 股给板块成分股的涨跌幅/PE/PB/主力净流入; 港股给同行业市值前列 + PE/PB/市值/涨跌;
    美股给 Yahoo 的相似标的 + 各自涨跌(无资金流源)。"""
    from services.market_data import normalize_stock_code, is_a_share, get_realtime_quotes
    raw = normalize_stock_code(_norm_code(code))
    if raw.upper().startswith("US.") or (not is_a_share(raw) and _re.fullmatch(r"[A-Za-z.]{1,6}", raw)):
        from services import us_fundamentals as usf
        peers = await asyncio.to_thread(usf.us_peers, raw)
        if not peers:
            return {"error": f"Yahoo 没给出 {raw} 的相似标的"}
        codes = [p["code"] for p in peers]
        quotes = await get_realtime_quotes(codes + [raw])
        for p in peers:
            q = quotes.get(p["code"]) or {}
            p["name"] = q.get("stock_name") or ""
            p["price"] = q.get("price")
            p["涨跌幅%"] = q.get("change_pct")
            ext = q.get("ext_hours") or {}
            if ext:
                p[f"{ext['label']}%"] = ext.get("change_pct")
        me = quotes.get(raw) or {}
        return {"code": raw, "name": me.get("stock_name") or "",
                "涨跌幅%": me.get("change_pct"), "同行": peers,
                "note": "同行由 Yahoo 按相似度给出; 美股无资金流/主力净买源, 只给价与涨跌"}
    hu = _hkus(code)
    if hu and hu[0] == "HK":
        return await _tool_peers_hk(hu[1])
    if not is_a_share(raw):
        return {"error": "同行对比支持 A 股、港股、美股"}
    return await asyncio.to_thread(_fetch_peers_sync, _norm_code(code))


async def _tool_peers_hk(sym: str) -> dict:
    """港股同行: 东财港股 F10 同行业按市值排前 10 + 目标票, 套实时行情取涨跌。"""
    from services.market_data import get_realtime_quotes, fx_cross_rate
    from services import foreign_f10 as ff
    raw = await asyncio.to_thread(ff.hk_peers_raw, sym)
    if raw.get("error"):
        return raw
    rows = raw["rows"]
    quotes = {}
    try:
        quotes = await get_realtime_quotes([r["code"] for r in rows])
    except Exception:
        pass
    fx = await asyncio.to_thread(fx_cross_rate, "CNY", "HKD")
    me = f"HK.{sym.zfill(5)}"
    for r in rows:
        q = quotes.get(r["code"]) or {}
        r["price"] = q.get("price")
        r["涨跌幅%"] = q.get("change_pct")
        cny = r.pop("_mcap_cny", None)
        r["市值亿HKD"] = round(cny * fx["rate"] / 1e8, 1) if cny and fx else None
        if r["code"] == me:
            r["本股"] = True
    return {"code": me, "行业": raw.get("行业"), "行业成分数": raw.get("行业成分数"),
            "行业PE_TTM平均": raw.get("行业PE_TTM平均"), "行业PE_TTM中值": raw.get("行业PE_TTM中值"),
            "同行": rows,
            "note": f"同属东财港股【{raw.get('行业')}】行业, 按市值降序取前 10(已去掉双柜台的人民币柜台); "
                    f"市值为东财 {raw.get('市值截至')} 收盘口径(源按人民币列示, 已按即期汇率折成港元), PE_TTM/PB 为东财同口径; "
                    "涨跌幅%为最新行情。港股无主力资金流源。"}


_holders_cache: dict = {}


def _fetch_shareholders_sync(code: str) -> dict:
    """十大流通股东(akshare, 最近报告期) + 北向(香港中央结算)持股变动 + 未来解禁。"""
    import time as _t
    import datetime as _dt
    ck = f"hold_{code}"
    c = _holders_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    sym = ("sh" if code[:1] in ("6", "9", "5") else "sz") + code
    # 找最近一个有数据的报告期(往回试 5 个季度末)
    today = _dt.date.today()
    cands = []
    y = today.year
    for yy in (y, y - 1):
        for md in ("1231", "0930", "0630", "0331"):
            d = f"{yy}{md}"
            if d <= today.strftime("%Y%m%d"):
                cands.append(d)
    cands = sorted(set(cands), reverse=True)[:5]
    holders, rdate, north = [], None, None
    for d in cands:
        try:
            df = ak.stock_gdfx_free_top_10_em(symbol=sym, date=d)
            if df is not None and not df.empty:
                rdate = d
                for _, r in df.iterrows():
                    nm = str(r.get("股东名称") or "")
                    rec = {"name": nm, "type": r.get("股东性质"),
                           "占流通股%": round(float(r.get("占总流通股本持股比例") or 0), 2),
                           "增减": r.get("增减"), "变动比率%": round(float(r.get("变动比率")), 2) if r.get("变动比率") == r.get("变动比率") else None}
                    holders.append(rec)
                    if "香港中央结算" in nm:
                        north = rec
                break
        except Exception:
            continue
    # 未来解禁
    unlock = []
    try:
        dfq = ak.stock_restricted_release_queue_em(symbol=code)
        if dfq is not None and not dfq.empty:
            for _, r in dfq.iterrows():
                dt = r.get("解禁时间")
                if dt and hasattr(dt, "strftime") and dt >= today:
                    unlock.append({"date": dt.strftime("%Y-%m-%d"),
                                   "类型": r.get("限售股类型"),
                                   "占流通市值%": round(float(r.get("占流通市值比例") or 0) * 100, 2),
                                   "解禁数量万股": round(float(r.get("实际解禁数量") or 0) / 1e4, 1)})
            unlock.sort(key=lambda x: x["date"])
    except Exception:
        pass
    out = {"报告期": rdate, "top10_circulating": holders[:10],
           "north_bound": north or {"note": "前十大流通股东无北向(香港中央结算)身影"},
           "upcoming_unlock": unlock[:5] if unlock else {"note": "未来无限售解禁(基本全流通)"},
           "note": "增减看主要股东在加仓还是减持; 北向=香港中央结算; 解禁占流通市值比越大、时间越近, 潜在抛压越大。"}
    _holders_cache[ck] = (out, _t.time())
    return out


async def _tool_shareholders(code: str) -> dict:
    """筹码面。A 股: 十大流通股东及增减、北向持股、未来限售解禁; 港股: 港交所权益披露(主要股东/董事)+近期权益变动+回购;
    美股: 13F 前十大机构 + 内部人交易汇总。"""
    from services.market_data import normalize_stock_code, is_a_share
    hu = _hkus(code)
    if hu:
        from services import foreign_f10 as ff
        fn = ff.hk_shareholders if hu[0] == "HK" else ff.us_shareholders
        try:
            return await asyncio.to_thread(fn, hu[1])
        except Exception as e:
            return {"error": f"股东数据获取失败: {e}"}
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "股东数据支持 A 股、港股、美股"}
    try:
        return await asyncio.to_thread(_fetch_shareholders_sync, _norm_code(code))
    except Exception as e:
        return {"error": f"股东数据获取失败: {e}"}


# 红线关键词 → (命中词, 类别, 级别, 排除词)。扫公告标题, 命中且不含排除词=该风险有事实依据。
# 排除词用于剔除例行/反向公告(如 审核问询=IPO例行、终止减持=减持结束、专项说明=年报例行)。
_RED_LINE_RULES = [
    (("立案", "被调查", "行政处罚", "责令改正", "警示函", "监管措施", "纪律处分"), "监管处罚", "高", ()),
    (("*ST", "退市风险", "终止上市", "暂停上市", "其他风险警示"), "退市风险", "高", ()),
    (("预亏", "首亏", "续亏", "预减", "由盈转亏", "净利润大幅下降", "业绩大幅下滑"), "业绩预警", "中", ()),
    (("商誉减值", "计提.*减值", "大额减值"), "资产/商誉减值", "中", ("审计说明", "专项说明")),
    (("问询函", "关注函", "监管工作函"), "交易所问询", "中", ("审核问询", "保荐", "注册", "回复")),
    (("违规担保", "违规占用", "重大诉讼", "重大仲裁"), "违规/诉讼", "中", ("专项说明", "专项审计", "审计说明")),
    (("拟减持", "减持计划", "询价转让", "大宗交易减持"), "股东减持", "低", ("终止", "完成", "不减持", "届满", "结果")),
    (("平仓风险", "高比例质押", "质押.*预警"), "股权质押", "低", ()),
]


async def _tool_red_flags(code: str) -> dict:
    """客观红线清单: 扫公告(监管处罚/退市/业绩预警/减值/问询/减持等) + 解禁抛压 + 基本面健康度, 列出有事实依据的风险点。
    命中=该风险确有依据, 不是买卖建议; 把雷摆给用户自己判断。仅 A 股。"""
    import re as _re2
    from services.market_data import normalize_stock_code, is_a_share, get_stock_name
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "红线清单仅支持 A 股"}
    bare = raw.split(".")[-1] if "." in raw else raw
    from services.news import get_stock_announcements
    from services.fundamental_score import fetch_health_snapshot

    name = ""
    try:
        name = await get_stock_name(raw) or ""
    except Exception:
        pass
    anns, shholders, health = await asyncio.gather(
        get_stock_announcements(bare, limit=30),
        asyncio.to_thread(_fetch_shareholders_sync, raw),
        fetch_health_snapshot(raw, name),
        return_exceptions=True,
    )
    if isinstance(anns, Exception): anns = []
    if isinstance(shholders, Exception): shholders = {}
    if isinstance(health, Exception): health = None

    flags, hit_cats = [], set()
    # 1) 公告关键词扫描(取最近一条命中作依据)
    for a in (anns or []):
        title = a.get("title") or ""
        date = (a.get("date") or "")[:10]
        for kws, cat, lvl, excludes in _RED_LINE_RULES:
            if cat in hit_cats:
                continue
            if any(x in title for x in excludes):
                continue
            if any((_re2.search(k, title) if ".*" in k else k in title) for k in kws):
                flags.append({"类别": cat, "级别": lvl, "依据": f"[{date}] {title}"})
                hit_cats.add(cat)
    # 2) 解禁抛压
    unlock = (shholders or {}).get("upcoming_unlock")
    if isinstance(unlock, list) and unlock:
        u0 = unlock[0]
        pct = u0.get("占流通市值%") or 0
        lvl = "中" if pct and pct >= 5 else "低"
        flags.append({"类别": "解禁抛压", "级别": lvl,
                      "依据": f"{u0.get('date')} 解禁约占流通市值 {pct}% ({u0.get('类型') or ''})"})
    # 3) 基本面健康度
    # 只有红灯算命中。黄灯是"信号中性/混杂"这个区间(评分 -0.5~0.5), 既盖偏正也盖偏负,
    # 不是风险事实 —— 早先黄灯也挂 flag, 而绝大多数票本来就落在这一档, 于是每只票的
    # 红线清单里都恒定多出一条「健康度黄灯」, 它不区分任何东西, 纯噪音。
    if health and isinstance(health, dict):
        if health.get("level") == "red":
            flags.append({"类别": "基本面健康度", "级别": "中", "依据": f"健康度红灯 (评分 {health.get('score')})"})

    order = {"高": 0, "中": 1, "低": 2, "提示": 3}
    flags.sort(key=lambda f: order.get(f["级别"], 9))
    checked = ["监管处罚", "退市风险", "业绩预警", "资产/商誉减值", "交易所问询", "违规/诉讼", "股东减持", "解禁抛压", "基本面健康度"]
    clear = [c for c in checked if c not in {f["类别"] for f in flags}]
    return {"code": bare, "name": name, "red_flags": flags, "checked_clear": clear,
            "note": "客观红线扫描(近30条公告+解禁+健康度): red_flags=有事实依据的风险点(按高/中/低/提示排), "
                    "checked_clear=已扫描未命中的项。命中仅代表'存在该风险事实', 属信息呈现, 供用户自行判断。"
                    "公告源仅覆盖近期, 更早或非公告类风险可能未覆盖, 表述时限定为'近期公告/解禁/健康度未扫到明显红线'并说明覆盖范围。"}


_ann_cache: dict = {}
# 值得重点标注的公告类型(资金/事件驱动)
_ANN_KEY_TYPES = ["分红", "回购", "增持", "减持", "业绩", "预增", "预减", "重组", "收购", "中标",
                  "股权激励", "定增", "并购", "重大资产", "股份转让", "实控人", "破产", "退市", "问询", "立案"]


def _fetch_announcements_sync(code: str, limit: int = 12) -> dict:
    """个股公告(东财 np-anotice-stock): 标题/日期/类型。结构化, 比新闻更权威(分红回购/业绩/重组/股权激励等)。"""
    import requests as _rq
    import time as _t
    ck = f"ann_{code}"
    c = _ann_cache.get(ck)
    if c and _t.time() - c[1] < 1800:
        return c[0]
    try:
        j = _rq.get("https://np-anotice-stock.eastmoney.com/api/security/ann",
                    params={"sr": "-1", "page_size": str(max(limit, 15)), "page_index": "1",
                            "ann_type": "A", "client_source": "web", "stock_list": code},
                    timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}).json()
    except Exception:
        return {"error": "公告源不可达"}
    rows = (j.get("data") or {}).get("list") or []
    if not rows:
        return {"announcements": [], "note": "近期无公告"}
    out = []
    for a in rows[:limit]:
        types = [c.get("column_name") for c in (a.get("columns") or []) if c.get("column_name")]
        title = a.get("title") or ""
        key = any(any(kt in (t or "") for kt in _ANN_KEY_TYPES) for t in types) or any(kt in title for kt in _ANN_KEY_TYPES)
        out.append({"date": str(a.get("notice_date") or "")[:10], "title": title,
                    "类型": types, "key": key})
    res = {"announcements": out,
           "note": "结构化公告; key=true 是分红/回购/增减持/业绩/重组/股权激励等资金或事件驱动型, 优先看。"}
    _ann_cache[ck] = (res, _t.time())
    return res


async def _tool_announcements(code: str) -> dict:
    """个股公告(分红/回购/增减持/业绩/重组/股权激励/关联交易等), 比新闻权威。
    A 股走交易所公告; 港股走港交所披露易(东财转载); 美股走 SEC EDGAR 申报(8-K=重大事件, 4/144=内部人交易, 13D/G=大股东)。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if raw.upper().startswith("US.") or (not is_a_share(raw) and _re.fullmatch(r"[A-Za-z.]{1,6}", raw)):
        from services import us_fundamentals as usf
        return await asyncio.to_thread(usf.us_filings, raw)
    hu = _hkus(code)
    if hu and hu[0] == "HK":
        from services import foreign_f10 as ff
        return await asyncio.to_thread(ff.hk_announcements, hu[1])
    if not is_a_share(raw):
        return {"error": "公告支持 A 股、港股、美股"}
    return await asyncio.to_thread(_fetch_announcements_sync, _norm_code(code))


_ACTION_CN = {"BUY": "买入", "ADD": "加仓", "SELL": "卖出", "REDUCE": "减仓",
              "BONUS": "送转股", "DIVIDEND": "现金分红"}


async def _tool_trades(code: str = "", start: str = "", end: str = "") -> dict:
    """成交记录(含个股/场内ETF/场外基金): 传 code→该标的流水 + (A股)持仓状态(综合成本/已实现盈亏/持有天数);
    不传→最近全部成交(三类合并)。start/end (YYYY-MM-DD) 按成交日期筛选区间。"""
    from database import get_position_actions, get_all_holdings

    s, e = (start or "").strip()[:10], (end or "").strip()[:10]

    def in_range(d: str) -> bool:
        d = (d or "")[:10]
        if s and d < s:
            return False
        if e and d > e:
            return False
        return True

    def act_date(x) -> str:
        # trade_date 缺失时回退 created_at(同步/导入生成的 initial/add-lot 只有 created_at), 对齐前端口径
        return (x.get("trade_date") or str(x.get("created_at") or ""))[:10]

    from services.market_data import code_currency, code_fx, normalize_stock_code, split_stock_code
    _fx_memo: dict = {}

    def _fx(c: str) -> float:
        if c not in _fx_memo:
            _fx_memo[c] = code_fx(c)
        return _fx_memo[c]

    def _ccy_fields(c: str, amt: float | None) -> dict:
        """港美股: 金额是原币, 补 币种 + 金额(人民币, 按当前汇率折算)。A 股不加字段。"""
        if split_stock_code(c or "")[0] not in ("HK", "US"):
            return {}
        out = {"币种": code_currency(c)}
        if amt:
            out["金额(人民币,现汇率)"] = round(amt * _fx(c), 2)
        return out

    def fmt(a: dict) -> dict:
        amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
        return {"date": (a.get("trade_date") or str(a.get("created_at") or ""))[:10],
                "动作": _ACTION_CN.get(a.get("action_type"), a.get("action_type")),
                "price": a.get("price"), "shares": a.get("shares"),
                "金额": round(amt, 2) if amt else None,
                **_ccy_fields(a.get("stock_code") or "", amt),
                "fee": a.get("fee"), "note": a.get("note") or ""}

    if code:
        bare = _norm_code(code)
        acts = await get_position_actions(bare, limit=500)
        if not acts and normalize_stock_code(bare) != bare:      # hk00700 / 0700 写法 → 账本键 HK.00700
            acts = await get_position_actions(normalize_stock_code(bare), limit=500)
            if acts:
                bare = normalize_stock_code(bare)
        if not acts:
            # 不在个股账本 → 可能是基金/ETF(外部资产账本)
            try:
                from database import list_external_assets, list_external_actions
                q = (code or "").strip()
                for a in await list_external_assets():
                    ac, an = str(a.get("code") or ""), (a.get("name") or "")
                    atype = (a.get("asset_type") or "").upper()
                    # 按该资产自己的代码/名称匹配(q==bare 是自比恒真, 去掉; 否则任意基金都会误命中)
                    if atype in ("FUND", "ETF") and ac and (q == ac or bare == ac or (len(q) >= 2 and q in an)):
                        fa = {"BUY": "申购", "ADD": "加仓", "REDEEM": "赎回",
                              "DEPOSIT": "转入", "WITHDRAW": "转出", "SPLIT": "份额拆分"}
                        _IN, _OUT = {"BUY", "ADD", "DEPOSIT"}, {"REDEEM", "WITHDRAW"}
                        from datetime import datetime as _dt
                        today_str = _dt.now().strftime("%Y-%m-%d")
                        # 按时间排序, 算每笔后的净份额余额(只计已确认), 让"卖了多少/还剩多少/占比"有准数, 不靠脑算
                        allx = sorted(await list_external_actions(a["id"]),
                                      key=lambda r: (act_date(r), r.get("id") or 0))
                        # 每笔之后发生的拆分因子之积: 拆分前的价/份额 折算到现行标度,
                        # 与现价直接可比(算均价/盈亏用调整后的数)
                        fac_after = [1.0] * len(allx)
                        fac = 1.0
                        for i in range(len(allx) - 1, -1, -1):
                            fac_after[i] = fac
                            xi = allx[i]
                            if (xi.get("action_type") or "").upper() == "SPLIT" and \
                                    (xi.get("status") or "confirmed") == "confirmed":
                                f = float(xi.get("shares") or 0)
                                if f > 0:
                                    fac *= f
                        net = 0.0
                        today_in = today_out = 0.0
                        pre_today_bal = None                # 今日首笔动作前的余额(=盘前份额)
                        recs = []
                        for i, x in enumerate(allx):
                            at = (x.get("action_type") or "").upper()
                            sh = float(x.get("shares") or 0)
                            confirmed = (x.get("status") or "confirmed") == "confirmed"
                            d = act_date(x)
                            if confirmed:
                                if d == today_str and pre_today_bal is None:
                                    pre_today_bal = net
                                if at == "SPLIT":
                                    # 份额拆分(1份→F份, F存shares字段): 余额×F, 不是买卖;
                                    # 盘前份额同步折算, 拆分日的'卖出占盘前'才是同标度可比
                                    if sh > 0:
                                        net *= sh
                                        if pre_today_bal is not None:
                                            pre_today_bal *= sh
                                else:
                                    net += sh if at in _IN else (-sh if at in _OUT else 0)
                                    if d == today_str:
                                        if at in _IN:
                                            today_in += sh
                                        elif at in _OUT:
                                            today_out += sh
                            if not in_range(d):
                                continue
                            r = {"date": d, "动作": fa.get(at, at),
                                 "price": x.get("unit_price"),
                                 "shares": sh if at != "SPLIT" else None,
                                 "金额": x.get("amount"), "余额": round(net, 2),
                                 "note": x.get("note") or ""}
                            if str(x.get("note") or "").startswith("DCA"):
                                r["来源"] = "定投"
                            if at == "SPLIT":
                                r["拆分比"] = f"1:{sh:g}"
                            elif fac_after[i] != 1.0:
                                # 该笔之后发生过拆分: 给现行标度的调整价/调整份额
                                try:
                                    if x.get("unit_price") is not None:
                                        r["调整价"] = round(float(x["unit_price"]) / fac_after[i], 4)
                                    r["调整份额"] = round(sh * fac_after[i], 2)
                                except (TypeError, ValueError):
                                    pass
                            if not confirmed:
                                r["状态"] = "待确认(T+1未出净值)"
                            recs.append(r)
                        cur = round(net, 2)
                        pre_today = round(pre_today_bal if pre_today_bal is not None else net, 2)   # 今日开盘前份额(拆分日=拆分前)
                        out = {"code": ac, "name": an, "asset_class": "基金/ETF",
                               "当前份额": cur, "trades": recs,
                               "range": {"start": s or None, "end": e or None},
                               "note": "基金/ETF 申赎流水; 每条带 余额=该笔后的累计净份额(已确认, 份额拆分行按拆分比折算)。"
                                       "说'卖了多少/还剩多少/减仓占比'一律用 余额/今日 里的数。"
                                       "拆分前的交易带 调整价/调整份额(已折算到现行标度, 与现价直接可比)——"
                                       "算均价、对比现价、算每笔浮盈一律用调整后的数, price 仅是当时的原始成交价。"
                                       "整体成本与盈亏以 get_holdings 返回的 摊薄成本 为准。"
                                       "来源=定投 的行是定投计划自动买入(策略性规律买入, 评价口径区别于主观加仓)。"}
                        plan = (await _dca_plan_map()).get(a["id"])
                        if plan:
                            out["定投计划"] = plan
                        if today_in or today_out:
                            out["今日"] = {"净买入份额": round(today_in, 2), "净卖出份额": round(today_out, 2),
                                           "盘前份额": pre_today, "当前份额": cur,
                                           "卖出占盘前%": round(today_out / pre_today * 100, 1) if pre_today > 0 else None}
                        return out
            except Exception:
                pass
            return {"code": bare, "trades": [], "note": "该标的无成交记录"}
        # 名称
        name = ""
        for h in await get_all_holdings():
            if h.get("stock_code") == bare:
                name = h.get("stock_name") or ""
                break
        recs = sorted([fmt(a) for a in acts if in_range(a.get("trade_date") or str(a.get("created_at") or ""))],
                      key=lambda x: x["date"])
        summary = {}
        try:
            from services.position_ledger import ledger_state
            st = await ledger_state(bare, acts)   # 券商读持仓行; 费用/汇率口径同持仓列表, 含分红摊薄
            # 已实现盈亏用 realized_carry(已平仓段+分红, 不含浮动)。注意 realized_pnl 与 carry
            # 在清仓后是同一笔, 不能相加; 当前持仓段的浮盈在 综合成本 里体现, 不算"已实现"。
            summary = {"当前持股": st.get("shares"), "综合成本": st.get("cost_price"),
                       "成本未摊薄分红前": st.get("cost_price_raw"), "每股已收分红": st.get("div_per_share"),
                       "已实现盈亏": round(float(st.get("realized_carry") or 0), 2),
                       "加权持有天数": st.get("weighted_days"), "累计手续费": st.get("total_fees")}
            if split_stock_code(bare)[0] in ("HK", "US"):
                summary["币种"] = code_currency(bare)
                summary["已实现盈亏(人民币,成交日汇率)"] = round(float(st.get("realized_carry_cny") or 0), 2)
                summary["其中汇率损益(人民币)"] = round(float(st.get("realized_fx_pnl") or 0), 2)
        except Exception:
            pass
        note = "trades=区间内成交流水(含手续费,按日期升序); position=按全历史算的当前状态(不受区间影响)。同日有买有卖=做T。已实现盈亏含已平仓段+分红。"
        if split_stock_code(bare)[0] in ("HK", "US"):
            note += ("价格/金额/已实现盈亏均为原币(见 币种); 与人民币资产相加或比较时用带(人民币,…)的字段; "
                     "已实现盈亏的人民币值按每笔成交当日汇率折算, 汇率损益单列。")
        return {"code": bare, "name": name, "trades": recs, "position": summary,
                "range": {"start": s or None, "end": e or None}, "note": note}

    # 无 code: 最近全部成交 —— 自己组装(个股 + 场内ETF + 场外基金), 含 pending(T+1待确认)申购, 标出来
    try:
        merged = []
        name_by = {h.get("stock_code"): h.get("stock_name") for h in await get_all_holdings()}
        # A 股个股
        for a in await get_position_actions(None, limit=200):
            d = (a.get("trade_date") or str(a.get("created_at") or ""))[:10]
            if not in_range(d):
                continue
            amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
            sc = a.get("stock_code") or ""
            merged.append({"date": d, "code": sc, "name": name_by.get(sc, ""),
                           "动作": _ACTION_CN.get(a.get("action_type"), a.get("action_type")),
                           "类型": {"HK": "港股", "US": "美股"}.get(split_stock_code(sc)[0], "个股"),
                           "price": a.get("price"), "shares": a.get("shares"), "金额": round(amt, 2) if amt else None,
                           **_ccy_fields(sc, amt)})
        # 场内ETF / 场外基金 (含待确认)
        from database import list_external_assets, list_external_actions
        from services.external_assets import _is_onchain_etf
        fa = {"BUY": "买入", "ADD": "买入", "REDEEM": "卖出"}
        for a in await list_external_assets():
            if a.get("asset_type") != "FUND":
                continue
            cls = "场内ETF" if _is_onchain_etf(str(a.get("code") or "")) else "场外基金"
            for x in await list_external_actions(a["id"]):
                act = fa.get((x.get("action_type") or "").upper())
                d = act_date(x)
                if not act or not in_range(d):
                    continue
                pend = (x.get("status") or "confirmed") != "confirmed"
                rec = {"date": d, "code": str(a.get("code") or ""),
                       "name": a.get("name") or "", "动作": act, "类型": cls, "price": x.get("unit_price"),
                       "shares": x.get("shares"), "金额": x.get("amount")}
                if str(x.get("note") or "").startswith("DCA"):
                    rec["来源"] = "定投"
                if pend:
                    rec["状态"] = "待确认(T+1未出净值)"
                merged.append(rec)
        merged.sort(key=lambda x: x["date"], reverse=True)
        return {"recent_trades": merged[:80], "range": {"start": s or None, "end": e or None},
                "note": "成交(时间倒序), 含个股(A股/港股/美股)/场内ETF/场外基金; 含待确认申购(状态=待确认)。日期缺失的成交按录入时间(created_at)归日。"
                        "港股/美股行的 price/金额 是原币(见 币种), 汇总时用 金额(人民币,现汇率)。"
                        "来源=定投 的行是定投计划自动买入(策略性规律买入, 不是主观追高); 没这个标记的才是手动操作。"}
    except Exception:
        acts = await get_position_actions(None, limit=40)
        name_by = {h.get("stock_code"): h.get("stock_name") for h in await get_all_holdings()}
        recs = [{**fmt(a), "code": a.get("stock_code"), "name": name_by.get(a.get("stock_code"), "")} for a in acts]
        return {"recent_trades": recs, "note": "最近成交(仅个股, 基金账本读取失败)。"}


async def _tool_capital_allocation(code: str) -> dict:
    """资本配置台账: 从股东拿了多少现金、分红回购还了多少、回购贵不贵。只陈述钱的流向, 不评价人。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "资本配置台账仅支持 A 股(分红送配与现金流量表口径只对得上 A 股)"}
    from services.capital_allocation import ledger
    try:
        return await ledger(_norm_code(code))
    except Exception as e:
        return {"error": f"资本配置台账取数失败: {e}"}


async def _tool_screen_quality(code: str) -> dict:
    """去劣筛选 7 条: 用多年财报排除"不是一流公司"。只出排除/未被排除/判不了, 不出评分与买卖。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "去劣筛选仅支持 A 股(多年财报口径只对得上 A 股财务摘要)"}
    from services.quality_screen import screen
    try:
        return await screen(_norm_code(code))
    except Exception as e:
        return {"error": f"去劣筛选取数失败: {e}"}


async def _tool_get_thesis(code: str) -> dict:
    """读用户当初记的买入逻辑(thesis-tracker), 用于复盘'当初为什么买、逻辑还成不成立'。不带 code 看全部。"""
    from database import get_thesis, list_theses
    from services.market_data import normalize_stock_code
    if not code:
        ts = await list_theses()
        return {"theses": ts, "note": "用户记录的各持仓买入逻辑; 复盘时对照现状看逻辑是否还成立。"}
    raw = normalize_stock_code(_norm_code(code))
    bare = raw.split(".")[-1] if "." in raw else raw
    # 先按完整代码(HK.00700)查, 再退回裸码(保存端历史上按裸码存)
    t = (await get_thesis(raw) if raw != bare else None) or await get_thesis(bare)
    if not t:
        return {"code": bare, "thesis": None, "note": "用户没记这只的买入逻辑。可提示他在持仓里补一句, 以后好复盘。"}
    # 算"记于多久之前", 让复盘能锚定时间(几个月前的逻辑 vs 昨天刚记的, 复盘意义不同)
    days_since = None
    created = (t.get("created_at") or "")[:10]
    try:
        import datetime as _dt
        if created:
            today = (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()
            days_since = (today - _dt.date.fromisoformat(created)).days
    except Exception:
        pass
    # 漂移: 逻辑改过几次、每次跟着什么改。改过的逻辑不能当"当初的逻辑"复盘 —— 现在这一版
    # 可能已经是照着股价改过的说法, 拿它去对现状必然自洽。
    dr = {}
    try:
        from services.thesis_drift import drift as _drift
        d = await _drift(raw)
        if d.get("有记录") and int(d.get("修订次数") or 0) > 1:
            dr = {"逻辑漂移": {k: d.get(k) for k in
                             ("修订次数", "首版日期", "末版日期", "逐次改写", "改写时浮亏轨迹",
                              "自末版以来", "口径")}}
        elif d.get("有记录"):
            dr = {"逻辑漂移": {"修订次数": 1, "自末版以来": d.get("自末版以来"),
                             "note": "只写过一版, 没有改写记录。"}}
    except Exception as e:
        dr = {"逻辑漂移": {"error": f"取数失败: {e}"}}
    return {"code": bare, "name": t.get("name"), "thesis": t.get("thesis"),
            "recorded_at": created, "updated_at": (t.get("updated_at") or "")[:10], "days_since": days_since,
            **dr,
            "note": "thesis=用户记的买入逻辑(最新一版); recorded_at=当初记下的日期, days_since=距今天数。"
                    "复盘时先点明'这是你X天前/X个月前记的逻辑', 再逐条对照现价/基本面/消息/红线客观判定每条是否仍成立, 买卖结论由用户自定。"
                    "逻辑漂移里 逐次改写.判定.档 有三档: 跟事实走(基本面确实变了)/跟股价走(基本面没动只有股价动)/"
                    "只改了说法。出现'跟股价走'要如实指出来并把当时的浮亏说出来 —— 这是事后合理化的迹象, "
                    "但只陈述这个行为事实, 不引申成买卖结论。文本变化里的 改写/删除/新增 是逐句 diff, "
                    "语义上是同义改写还是换了另一条理由, 由你读 diff 自己判断并说明依据。"}


def _dca_desc(p: dict) -> str:
    """定投计划→人话: 每交易日/每周X/每月X日 + 金额或份额 + 暂停标注。"""
    v = p.get("value")
    what = f"{v:g}元" if (p.get("mode") or "amount") == "amount" else f"{v:g}份"
    freq = p.get("frequency") or "monthly"
    if freq == "daily_trading":
        when = "每交易日"
    elif freq == "weekly":
        wd = int(p.get("day_of_week") or 1)
        when = f"每周{'一二三四五六日'[wd - 1]}"
    else:
        when = f"每月{p.get('day_of_month') or 1}日"
    s = f"{when} {what}"
    if (p.get("status") or "active") != "active":
        s += "(已暂停)"
    return s


async def _dca_plan_map() -> dict:
    """asset_id → 定投计划描述(同一资产多计划用 / 连接)。查不到返回空表, 不影响主流程。"""
    try:
        from database import list_dca_schedules
        m: dict = {}
        for p in await list_dca_schedules():
            m.setdefault(p["asset_id"], []).append(_dca_desc(p))
        return {k: " / ".join(v) for k, v in m.items()}
    except Exception:
        return {}


async def _tool_get_holdings() -> dict:
    try:
        from services.market_data import code_currency, code_fx, split_stock_code
        hs = await _active_holdings()
        dca_by_asset = await _dca_plan_map()
        # 个股按市场分组: 港股成本是港元、美股是美元, 混进 "A股" 一组会被当成人民币直接相加。
        # 港美股额外给 币种/汇率/成本金额(人民币), 合计一律用人民币那一列。
        stocks: dict[str, list] = {"A股": [], "港股": [], "美股": []}
        for h in hs:
            code = h.get("stock_code") or ""
            mkt = split_stock_code(code)[0]
            row = {"code": code, "name": h.get("stock_name"),
                   "shares": h.get("shares"), "综合成本": h.get("cost_price"),
                   "每股已收分红": h.get("每股已收分红"),
                   "持有天数": h.get("hold_days"), "开仓日": h.get("open_date")}
            if mkt in ("HK", "US"):
                fx = code_fx(code)
                row["币种"] = code_currency(code)
                row["汇率(人民币/原币)"] = round(fx, 4)
                try:
                    row["成本金额(人民币)"] = round(float(h.get("cost_price") or 0) * float(h.get("shares") or 0) * fx, 2)
                except (TypeError, ValueError):
                    pass
            stocks[{"HK": "港股", "US": "美股"}.get(mkt, "A股")].append(row)
        # 其他持仓: 场内ETF/场外基金/理财/现金/加密/机器人 —— 持仓不止 A 股, 一并读出来
        other = {}
        try:
            from database import list_external_assets, list_external_actions
            from services.external_ledger import compute_external_state
            from services.external_assets import _is_onchain_etf
            for x in await list_external_assets():
                at = x.get("asset_type")
                sh = x.get("shares")
                mv = x.get("manual_value")
                if at == "FUND" and not (sh and sh > 0):   # 已赎回(shares=0)的基金不算在持
                    continue
                row = {"name": x.get("name"), "code": x.get("code") or ""}
                if dca_by_asset.get(x.get("id")):
                    row["定投计划"] = dca_by_asset[x["id"]]
                if sh and sh > 0:
                    row["份额"] = round(float(sh), 2)
                if mv is not None:
                    row["金额元"] = round(float(mv), 2)
                # 基金/ETF: 从流水账本算权威摊薄成本(拆分/减仓已折算), 与现价同标度,
                # 直接给单价——盈亏 = (现价 − 摊薄成本) × 份额
                if at in ("FUND", "CRYPTO") and sh and sh > 0:
                    try:
                        st = compute_external_state(await list_external_actions(x["id"]), at)
                        led_sh = st.get("shares") or 0
                        if led_sh > 0:
                            row["份额"] = round(led_sh, 2)
                            row["摊薄成本"] = round((st.get("diluted_cost") or 0) / led_sh, 4)
                    except Exception:
                        pass
                cls = _ASSET_CLASS_CN.get(at, at)
                if at == "FUND":   # 场内按市价+佣金成交, 场外按 T+1 净值申赎, 分开归类
                    cls = "场内ETF" if _is_onchain_etf(str(x.get("code") or "")) else "场外基金"
                other.setdefault(cls, []).append(row)
        except Exception:
            pass
        return {"A股": stocks["A股"], **{k: v for k, v in stocks.items() if k != "A股" and v},
                "其他持仓": other,
                "note": "我的全部在持: A股/港股/美股个股(holdings) + 其他持仓(场内ETF/场外基金/理财/现金/加密/机器人, 来自资产看板)。已清仓/已赎回的不在此列"
                        "(今天刚清仓的票与今天的盈亏看 get_today_pnl)。"
                        "个股: 综合成本=含手续费+分红摊薄(对齐券商); 持有天数=资金加权(0=今天才开仓); 开仓日已带星期照抄。"
                        "港股/美股的 综合成本 是原币单价(港元/美元, 见 币种), 跨市场合计与占比一律用 成本金额(人民币)。"
                        "分类按交易场所据实表述: 场内ETF(代码 5xxxxx/1xxxxx)在交易所按市价+券商佣金成交, "
                        "场外基金(支付宝/天天基金等)按 T+1 净值申赎, 两类各按自己的名字说。"
                        "基金/ETF 给份额+摊薄成本(单价, 份额拆分与减仓已实现均已折算, 与现价同标度, 对齐券商口径);"
                        "浮动盈亏=(现价−摊薄成本)×份额, 成本一律以本字段为准。现金/理财/机器人给金额(元)。"
                        "带 定投计划 字段的资产在自动定投(频率+每期金额, 已暂停会标注)——这类持仓的规律小额买入是策略性定投, 不是主观追高。"
                        "要各大类占比/现金理财结构分析用 get_asset_allocation。"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_today_pnl() -> dict:
    """今天的盈亏与操作: 全账户当日盈亏(现金流口径, 含今日清仓) + 今天卖出的每只算本次卖出的已实现盈亏(相对持仓成本)。"""
    try:
        from services.today_pnl import today_pnl, _today_cst
        from database import get_position_actions, list_external_assets, list_external_actions
        from services.position_ledger import ledger_state, RELEASE
        from services.external_ledger import compute_external_state
        r = await today_pnl()
        day = _today_cst()
        ext = {str(a.get("code") or ""): a for a in await list_external_assets()}
        rows, closed_sum = [], 0.0
        for it in r.get("items") or []:
            mkt = it.get("market") or "A"
            foreign = it.get("type") == "A" and mkt in ("HK", "US")
            typ = ({"HK": "港股", "US": "美股"}.get(mkt, "A股") if it.get("type") == "A" else it.get("type"))
            row = {"name": it.get("name"), "code": it.get("code"), "类型": typ,
                   "今日盈亏": it.get("today_pnl"), "现持有": it.get("shares"),
                   "今日买入": it.get("bought"), "今日卖出": it.get("sold")}
            if foreign:
                # today_pnl 已按行情汇率折成人民币; 卖出均价/成本/已实现是原币, 单独标币种
                row["币种"] = it.get("currency") or ("HKD" if mkt == "HK" else "USD")
                row["汇率(人民币/原币)"] = it.get("fx_rate")
                if mkt == "US" and it.get("session_day"):
                    row["对应美东交易日"] = it["session_day"]
            if it.get("closed_today"):
                row["今日清仓"] = True
                closed_sum += it.get("today_pnl") or 0
            if it.get("opened_today"):
                row["今日新建仓"] = True
            if it.get("estimated"):
                row["估算"] = True
            if (it.get("sold") or 0) > 0:
                try:
                    if it.get("type") == "A":
                        acts = await get_position_actions(it["code"], limit=1000)
                        before = [a for a in acts if (a.get("trade_date") or "")[:10] < day]
                        sa = await ledger_state(it["code"], acts, dilute=False)
                        sb = await ledger_state(it["code"], before, dilute=False)
                        sells = [a for a in acts if (a.get("trade_date") or "")[:10] == day
                                 and (a.get("action_type") or "").upper() in RELEASE]
                        cost_before = sb.get("cost_price")
                    else:
                        a0 = ext.get(str(it.get("code")))
                        acts = [x for x in await list_external_actions(a0["id"]) if (x.get("status") or "confirmed") == "confirmed"]
                        before = [x for x in acts if str(x.get("trade_date") or "")[:10] < day]
                        sa = compute_external_state(acts, a0.get("asset_type"))
                        sb = compute_external_state(before, a0.get("asset_type"))
                        sells = [x for x in acts if str(x.get("trade_date") or "")[:10] == day
                                 and (x.get("action_type") or "").upper() in ("REDEEM", "SELL", "REDUCE")]
                        sh_b = sb.get("shares") or 0
                        cost_before = round((sb.get("diluted_cost") or sb.get("cost_amount") or 0) / sh_b, 4) if sh_b else None
                    px = [float(a.get("price") or a.get("unit_price") or 0) for a in sells]
                    qty = [abs(float(a.get("shares") or 0)) for a in sells]
                    if sum(qty) > 0:
                        row["卖出均价"] = round(sum(p * q for p, q in zip(px, qty)) / sum(qty), 4)
                    row["卖出前持仓成本"] = cost_before
                    if it.get("type") == "A" and sb.get("fifo_cost_price"):
                        row["这批股票的买入均价(FIFO)"] = round(float(sb["fifo_cost_price"]), 4)
                    row["本次卖出已实现"] = round(float(sa.get("realized_pnl") or 0) - float(sb.get("realized_pnl") or 0), 2)
                    if foreign:
                        row["本次卖出已实现(人民币)"] = round(row["本次卖出已实现"] * float(it.get("fx_rate") or 1.0), 2)
                except Exception as e:  # noqa: BLE001
                    row["已实现_错误"] = str(e)
            rows.append(row)
        return {"日期": r.get("date"), "全账户今日盈亏": r.get("total"), "其中今日清仓部分": round(closed_sum, 2),
                "其中估算部分(场外基金净值未出)": r.get("estimated_part"), "未能计算": r.get("unknown"), "明细": rows,
                "note": "今日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本(现金流口径, 含手续费), "
                        "**今天清仓的票也在明细里**(今日清仓=true, 现持有=0) —— 回答今天的盈亏/操作/'今天怎么样'时必须把它们算进去, "
                        "别只看 get_holdings(那里只有还在手里的)。"
                        "今日盈亏是相对昨收的当天变动; 本次卖出已实现 = 今天这笔卖出新增的已实现盈亏, 两者口径不同, 分开说。"
                        "A股的 卖出前持仓成本 是综合成本(之前做T/减仓的盈亏已摊进去, 那部分当时已计入已实现), 所以 本次卖出已实现 ≠ (卖价−综合成本)×股数;"
                        "讲'这批股票买在多少、卖在多少'用 这批股票的买入均价(FIFO)。"
                        "标 估算 的是场外基金按底层代理估的当日盈亏, 净值公布后会修正。"
                        "今日盈亏与全账户合计都是人民币; 港股/美股行的 卖出均价/卖出前持仓成本/本次卖出已实现 是原币(见 币种),"
                        "与人民币相加时用 本次卖出已实现(人民币)。美股的'今日'是 对应美东交易日 那一场。"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_zsxq_digest(days: int = 1, all_authors=None) -> dict:
    """知识星球最近主题(观点面)。只读, 原文只在内存过一遍不落库。"""
    from services import zsxq_client as z
    if not z.is_enabled():
        return {"error": "未接入知识星球或未选星球(设置→知识星球)"}
    d = max(1, min(int(days or 1), 7))
    out = []
    for g in z.configured_groups():
        # owner_only 默认关: 实测 by_owner 会把发帖人不算"星主/合伙人"的星球筛成空
        oo = bool(g.get("owner_only", False)) if all_authors is None else (not all_authors)
        try:
            out += await asyncio.to_thread(z.list_topics, g["group_id"], g.get("name") or "",
                                           d, 20, oo)
        except Exception:
            continue
    if not out:
        h = await asyncio.to_thread(z.health)
        if not h.get("ok"):
            return {"error": f"知识星球端点不可用({h.get('error') or ''})"}
        return {"topics": [], "note": f"最近{d}天没有新主题"}
    out.sort(key=lambda x: x.get("时间") or "", reverse=True)
    return {"days": d, "count": len(out), "topics": out[:30],
            "note": "知识星球主题, 字段 stance=opinion —— 这是**某个人的观点不是事实**, 按 [星球观点] 标注,"
                    "转述带作者与日期; 数字与结论以 [实测]/[联网] 为准; 原文里的操作倾向只当舆情记录, "
                    "不据此给买卖建议。"}


async def _tool_zsxq_search(keyword: str = "") -> dict:
    """在已选星球里全文搜(服务端 RAG 语义匹配, 会漏召也会误召, 只当线索)。"""
    from services import zsxq_client as z
    if not z.is_enabled():
        return {"error": "未接入知识星球或未选星球(设置→知识星球)"}
    kw = (keyword or "").strip()
    if not kw:
        return {"error": "keyword 必填"}
    out = []
    for g in z.configured_groups():
        try:
            out += await asyncio.to_thread(z.search_topics, kw, g["group_id"], g.get("name") or "")
        except Exception:
            continue
    return {"keyword": kw, "count": len(out), "topics": out[:20],
            "note": "语义搜索结果, 相关性需自行复核(可能误召); stance=opinion, 按 [星球观点] 标注, "
                    "不作为数字依据, 不转成买卖建议。"}


async def _tool_zsxq_file(file_id: str = "", name: str = "") -> dict:
    """读星球附件的正文(研报/会议记录 docx·pdf)。只在内存里过一遍, 不落盘。"""
    from services import zsxq_client as z
    if not z.is_enabled():
        return {"error": "未接入知识星球或未选星球(设置→知识星球)"}
    fid = str(file_id or "").strip()
    if not fid.isdigit():
        return {"error": "file_id 必填(从 get_zsxq_digest 返回的 附件列表 里取)"}
    d = await asyncio.to_thread(z.fetch_file_text, fid, name or "")
    if d.get("error"):
        return d
    d["note"] = ("附件正文(第三方研报/会议记录)。**按要点提炼**转述: 讲了什么、关键数字、结论, "
                 "不整篇复述、不长段照抄(版权)。内容是他人观点, 按 [星球观点] 标注并带来源文件名; "
                 "里面的数字属于研报口径, 与本地 [实测] 数据冲突时以本地为准并点明分歧。"
                 "取不到文字(扫描件)时如实说明, 不猜内容。")
    return d


_ASSET_CLASS_CN = {"CASH": "现金", "WEALTH": "理财", "FUND": "基金",
                   "CRYPTO": "加密", "BOT": "量化机器人"}


async def _tool_etf_xray(query: str = "") -> dict:
    """ETF 题材透视: 代码→单只; 主题词→该主题规模前5; 空→在持全部。"""
    from services import etf_xray
    q = (query or "").strip()
    if not q:
        return await etf_xray.my_etf_scan()
    if len(q) == 6 and q.isdigit():
        return await asyncio.to_thread(etf_xray.analyze_etf, q)
    return await etf_xray.theme_scan(q, 5)


async def _tool_exposure(min_pct: float = 0.5, as_of: str = "") -> dict:
    """穿透敞口(同源风险): 基金拆到季报前十大, 算同一标的/同一行业的真实合计。

    回答"我实际押了多少在X上/持仓是不是重复下注/买了好几只基金是不是同一注"这类问题。
    只按名字看板块会漏 —— 「山金国际」名字里没有金属字样, 基金名也不说它拿了什么票。
    """
    from services.exposure import look_through
    try:
        d = await look_through(min_pct=min_pct, as_of=(as_of or None))
    except Exception as e:
        return {"error": f"穿透失败: {type(e).__name__}: {e}"}
    if d.get("error"):
        return d
    cov = d.get("coverage") or {}
    return {
        "总资产": d["total"],
        "直持个股数": d["n_stocks"], "基金数(已合并A/C份额)": d["n_funds"],
        "行业穿透": [{"行业": g["industry"], "合计": g["mv"], "占总资产%": g["pct"],
                     "其中直持": g["direct_mv"], "其中经基金间接": g["indirect_mv"],
                     "成分": g["members"]} for g in (d.get("industries") or [])[:8]],
        "标的穿透": [{"标的": it["name"], "代码": it["code"], "合计": it["total_mv"],
                     "占总资产%": it["pct"], "直持": it["direct_mv"], "间接": it["indirect_mv"],
                     "来源数": it["n_sources"],
                     "经由": [f"{v['fund']} {v['weight_pct']}%" for v in it["via"][:4]],
                     "两地上市已合并": it.get("multi_line", False)}
                    for it in (d.get("items") or [])[:12]],
        "基金撞车": [{"A": p["a"], "B": p["b"], "前十大重叠%": p["overlap_pct"],
                     "重合只数": p["n_same"], "重合": p["same"]}
                    for p in (d.get("fund_pairs") or [])[:5]],
        "结论": [w["text"] for w in (d.get("warnings") or [])[:8]],
        "覆盖": {"穿透到的基金数": cov.get("penetrated_funds"),
                 "拉不到明细": [u["name"] for u in (cov.get("uncovered") or [])],
                 "覆盖偏薄": [t["name"] for t in (cov.get("thin") or [])]},
        "note": (cov.get("note") or "") + " 表述时务必带上这条口径: 敞口是下限而非全量。"
                "结构同源(底层同一批票)与统计同源(走势一起动)是两件事, 后者看相关性矩阵。",
    }


async def _tool_asset_allocation() -> dict:
    """全量资产配置快照: 各大类(股票/现金/理财/基金/加密/机器人)市值+占比 + 现金/理财逐笔明细(金额/年化/持有天数)。
    供'现金理财怎么分/应急金够不够/结构合不合理'这类资产配置讨论, 不涉及个股买卖。单位元(CNY)。"""
    from api.assets_routes import list_assets
    from services.market_data import get_realtime_quotes
    try:
        data = await list_assets()
    except Exception as e:
        return {"error": f"读资产失败: {e}"}
    by_type = (data.get("summary") or {}).get("by_type") or {}
    assets = data.get("assets") or []

    classes: dict[str, float] = {}
    for t, v in by_type.items():
        cn = _ASSET_CLASS_CN.get(t, t)
        classes[cn] = round(classes.get(cn, 0.0) + float(v.get("value") or 0), 2)

    # A股/港美股市值(CNY)
    try:
        hs = await _active_holdings()
        if hs:
            codes = [h["stock_code"] for h in hs]
            quotes = await get_realtime_quotes(codes)
            stock_val = 0.0
            for h in hs:
                q = quotes.get(h["stock_code"]) or {}
                px = q.get("price") or 0
                fx = q.get("fx_rate") or 1
                stock_val += px * float(h.get("shares") or 0) * fx
            if stock_val > 0:
                classes["股票"] = round(stock_val, 2)
    except Exception:
        pass

    total = round(sum(classes.values()), 2)
    breakdown = [{"类别": k, "金额": v, "占比%": round(v / total * 100, 1) if total > 0 else 0}
                 for k, v in sorted(classes.items(), key=lambda kv: -kv[1])]

    # 现金 + 理财 逐笔明细(配置讨论的重点: 流动性 & 收益)
    liquid = []
    for a in assets:
        if a.get("asset_type") not in ("CASH", "WEALTH"):
            continue
        q = a.get("quote") or {}
        liquid.append({
            "名称": a.get("name"), "类别": _ASSET_CLASS_CN.get(a.get("asset_type")),
            "金额": round(a.get("current_value") or 0, 2),
            "年化%": round((a.get("annual_yield_rate") or q.get("annual_yield_rate") or 0) * 100, 2) or None,
            "持有天数": q.get("days_held"),
        })

    # 盈亏: 原来这个工具只给市值不给盈亏, get_holdings 也只有成本没有市值 ——
    # 于是 agent 手里凑不出「我总共赚亏多少」, 问到就只能瞎猜或说没数据。
    # 口径必须与看板顶栏逐字一致(Dashboard.jsx 的算法), 否则一个问题两个答案:
    #   总盈亏 = 浮动 + 已实现
    #   已实现只补「已平仓段 + 分红」—— 持仓段的已实现早已摊进浮动, 再加就重复
    #   场外排除 CASH: 现金利息已作为现金行的 pnl 计入浮动
    pnl: dict = {}
    try:
        unrealized = round(sum(float(a.get("pnl") or 0) for a in assets), 2)
        try:
            from api.portfolio_routes import list_holdings as _lh
            for h in await _lh():
                h = h if isinstance(h, dict) else h.model_dump()
                if (h.get("shares") or 0) > 0:
                    unrealized += float(h.get("unrealized_pnl") or 0)
        except Exception:
            pass
        unrealized = round(unrealized, 2)

        from api.portfolio_routes import realized_pnl as _rs
        from api.assets_routes import assets_realized as _ar
        s = await _rs()
        stock_carry = s.get("total_realized_carry")
        if stock_carry is None:
            stock_carry = sum(i.get("realized_pnl", 0) for i in (s.get("items") or [])
                              if not i.get("still_holding"))
        asset_realized = sum(
            (i.get("closed_realized") if i.get("closed_realized") is not None else i.get("realized_pnl", 0))
            for i in ((await _ar()).get("items") or []) if i.get("asset_type") != "CASH")
        realized = round(float(stock_carry) + float(asset_realized), 2)
        pnl = {
            "总盈亏": round(unrealized + realized, 2),
            "浮动盈亏": unrealized,
            "已实现": realized,
            "已实现_股票": round(float(stock_carry), 2),
            "已实现_基金理财加密机器人": round(float(asset_realized), 2),
        }
    except Exception as e:
        pnl = {"error": f"盈亏取数失败: {e}"}

    return {"unit": "元(CNY)", "total_asset": total, "breakdown": breakdown,
            "pnl": pnl,
            "cash_and_wealth_detail": liquid,
            "note": "breakdown=各大类市值与占比; pnl=全口径盈亏(总盈亏=浮动+已实现, "
                    "与看板顶栏同一算法; 已实现只含已平仓段与分红, 持仓段的已实现已摊进浮动不重复计); "
                    "cash_and_wealth_detail=现金/理财逐笔(流动性与收益)。"
                    "据此可分析流动性分层/应急金/收益-期限权衡, 但具体怎么分由用户自己决定。"}


async def _tool_sector_momentum(days: int = 10) -> dict:
    """板块趋势矩阵: 各行业近 N 日累计涨跌/连涨动能/净流入 → 看动量是否延续(动量风格) 还是冲高回落(退潮/反转)。"""
    try:
        from services.sector_matrix import get_sector_matrix
        m = await get_sector_matrix(days=int(days or 10))
        rows = m.get("rows") or []
        if not rows:
            return {"error": "板块矩阵暂无数据"}
        def brief(r):
            return {"板块": r["name"], "最新交易日涨幅%": r.get("today_pct"), f"近{m.get('days')}日累计": r.get("cum_pct"),
                    "连涨天": r.get("streak"), "净流入亿": r.get("net_inflow"),
                    "量能趋势": r.get("vol_trend"), "量价": r.get("vp_read")}
        return {"days": m.get("days"), "intraday": m.get("intraday"),
                "走强top": [brief(r) for r in rows[:8]],
                "退潮bottom": [brief(r) for r in rows[-5:]]}
    except Exception as e:
        return {"error": str(e)}


_concept_cache: dict = {}


def _fetch_hot_concepts_sync(top: int = 15) -> list[dict]:
    """今日东财概念板块涨幅榜(带主力净流入)。这是 量化/游资正在冲的'概念'粒度
    (如 CPO/HBM/先进封装/玻璃基板…), 比行业级更细。
    akshare 走死分片 79.push2 被墙 → 直连可达 host(push2delay 优先)+ 重试轮换。"""
    import requests as _rq
    import time as _t
    ck = f"concepts_{top}"
    c = _concept_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com",
             "1.push2.eastmoney.com", "50.push2.eastmoney.com"]
    params = {"pn": "1", "pz": str(max(top, 30)), "po": "1", "np": "1", "fltt": "2",
              "invt": "2", "fid": "f3", "fs": "m:90 t:3",
              "fields": "f12,f14,f3,f62,f104,f105"}
    for i in range(12):
        host = hosts[i % len(hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/clist/get", params=params, timeout=7)
            diff = (r.json().get("data") or {}).get("diff")
            if diff:
                out = []
                for x in diff[:top]:
                    try:
                        out.append({"概念": x.get("f14"), "涨跌幅": float(x.get("f3")),
                                    "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2),
                                    "涨家": x.get("f104"), "跌家": x.get("f105")})
                    except (ValueError, TypeError):
                        continue
                if out:
                    _concept_cache[ck] = (out, _t.time())
                    return out
        except Exception:
            _t.sleep(0.3)
    return []


async def _tool_hot_concepts(top: int = 15) -> dict:
    """今日热门概念榜(概念粒度, 比行业细): 涨幅 + 主力净流入。看量化/资金在冲哪个具体概念。"""
    out = await asyncio.to_thread(_fetch_hot_concepts_sync, int(top or 15))
    if not out:
        return {"error": "概念榜暂不可达(东财源抖动), 请改用行业级 get_sector_momentum"}
    return {"top_concepts": out, "note": "按最新交易日涨幅排序; 主力净流入正=资金流入"}


_board_list_cache: dict = {}


def _fetch_board_code_sync(name: str) -> tuple | None:
    """板块/概念名 → (BK代码, 标准名, 类型)。直接传 BKxxxx 也认。搜概念(t:3)+行业(t:2)板块列表。"""
    import requests as _rq
    import time as _t
    q = (name or "").strip()
    if not q:
        return None
    if q.upper().startswith("BK") and q[2:].isdigit():
        return (q.upper(), q.upper(), "板块")
    cache = _board_list_cache.get("boards")
    if not cache or _t.time() - cache[1] > 600:
        hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com"]
        boards = []
        # 翻页拉全量(EM 单页截 ~100; 概念 400+/行业 90+ 必须分页, 否则只拿到涨幅靠前的)
        for fs, kind in (("m:90 t:3", "概念"), ("m:90 t:2", "行业")):
            for pn in range(1, 8):
                page = None
                for h in hosts:
                    try:
                        page = (_rq.get(f"https://{h}/api/qt/clist/get", timeout=7, headers=hdr,
                                        params={"pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2",
                                                "invt": "2", "fid": "f3", "fs": fs, "fields": "f12,f14"}).json()
                                .get("data") or {}).get("diff")
                        break
                    except Exception:
                        _t.sleep(0.3)
                if not page:
                    break
                boards += [(x.get("f14"), x.get("f12"), kind) for x in page if x.get("f12")]
                if len(page) < 100:
                    break
        if boards:
            _board_list_cache["boards"] = (boards, _t.time())
            cache = _board_list_cache["boards"]
    if not cache:
        return None
    boards = cache[0]
    for nm, cd, kind in boards:                       # 精确
        if nm == q:
            return (cd, nm, kind)
    hits = [(nm, cd, kind) for nm, cd, kind in boards if q in (nm or "")]
    if hits:
        hits.sort(key=lambda x: len(x[0]))            # 最短名优先(最贴近)
        return (hits[0][1], hits[0][0], hits[0][2])
    return None


def _fetch_board_stocks_sync(name: str, top: int = 12) -> dict:
    """某板块/概念成分股按今日涨幅 top: 涨跌幅/现价/换手/主力净流入。"""
    import requests as _rq
    import time as _t
    resolved = _fetch_board_code_sync(name)
    if not resolved:
        return {"error": f"找不到板块/概念「{name}」(试试更标准的名字, 或先用 get_hot_concepts 看在榜的概念名)"}
    bk, std, kind = resolved
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    params = {"pn": "1", "pz": str(max(top, 12)), "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f3", "fs": f"b:{bk}", "fields": "f12,f14,f2,f3,f8,f62"}
    for i in range(9):
        host = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"][i % 3]
        try:
            diff = (_rq.get(f"https://{host}/api/qt/clist/get", params=params, timeout=7,
                            headers=hdr).json().get("data") or {}).get("diff")
            if diff:
                rows = []
                for x in diff[:top]:
                    try:
                        rows.append({"name": x.get("f14"), "code": x.get("f12"),
                                     "涨跌幅": x.get("f3"), "现价": x.get("f2"),
                                     "换手%": x.get("f8"),
                                     "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2)})
                    except (ValueError, TypeError):
                        continue
                if rows:
                    return {"板块": std, "类型": kind, "code": bk, "top_stocks": rows,
                            "note": f"「{std}」成分股按最新交易日涨幅排序; 主力净流入正=资金净买入(榜单快照, 用于看资金集中在哪几只; 单只票精确金额以 get_fund_flow 为准)。"}
        except Exception:
            _t.sleep(0.3)
    return {"error": f"「{std}」成分股暂不可达(东财源抖动)"}


async def _tool_board_stocks(board: str, top: int = 12) -> dict:
    """查某个板块/概念里今日涨幅 top-N 的个股(龙头), 带涨跌幅/现价/换手/主力净流入。
    board 传概念或行业名(如 玻璃基板/CPO/光通信/小金属)或 BK 代码。"""
    return await asyncio.to_thread(_fetch_board_stocks_sync, board, int(top or 12))


async def _tool_hot_rank() -> dict:
    """资金人气榜(东财): 资金/散户关注度最高的个股, 标出哪些在用户持仓里。看资金主线/抱团方向。"""
    try:
        from api.market_routes import hot_rank
        r = await hot_rank(top=20)
        items = [{"name": x.get("name"), "code": x.get("code"), "rank": x.get("rank"),
                  "mine": x.get("mine")} for x in (r.get("items") or [])]
        return {"top": items, "mine": [x.get("name") for x in (r.get("mine") or [])]}
    except Exception as e:
        return {"error": str(e)}


_POLICY_KW = [
    "央行", "降准", "降息", "逆回购", "MLF", "LPR", "国债", "专项债", "财政", "货币政策",
    "证监会", "银保监", "金融监管", "国常会", "国务院", "政治局", "发改委", "工信部", "部委",
    "政策", "监管", "调控", "刺激", "新政", "出口管制", "关税", "制裁", "实体清单",
    "反垄断", "反内卷", "供给侧", "去产能", "收储", "汇率", "稳增长", "会议", "规划", "意见",
]


async def _tool_market_news(limit: int = 40) -> dict:
    """全市场财经快讯(东财+财联社+同花顺+金十), 含政策面/国家调控 + 全球宏观/地缘/央行。用来把宏观政策、监管动向、
    产业政策、央行财政、重要会议、海外市场扰动等市场背景因素纳入分析。
    policy_news=政策关键词筛选; important_flash=金十标重要的快讯(全球宏观/地缘/央行, 对 A 股情绪影响大)。"""
    try:
        from api.news_routes import market_news
        mn = await market_news()
        items = (mn.get("items") or [])[:max(limit, 40)]
        def is_pol(t):
            return any(k in (t or "") for k in _POLICY_KW)
        heads = [{"title": it.get("title"), "time": it.get("time"), "source": it.get("source")} for it in items]
        policy = [h for h in heads if is_pol(h["title"])]
        important = [{"title": it.get("title"), "time": it.get("time")}
                     for it in items if it.get("important")][:12]
        return {"policy_news": policy[:18], "important_flash": important, "headlines": heads[:limit],
                "note": "policy_news=政策/调控相关筛选; important_flash=金十重要快讯(全球宏观/地缘/央行); headlines=全部要闻(时间倒序)"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_market_sentiment() -> dict:
    try:
        from api.market_routes import market_sentiment
        s = await market_sentiment()
        # 涨停的"质量": 原来只有 n_zt 这个只数, 52 个涨停配 57 亿封单和配 20 亿封单看不出区别。
        # 封单额=买一挂单额(真实盘口的量), 收盘定格。取不到就整块省掉, 不给半截数字。
        quality = None
        try:
            from services.limit_up_pool import quality as _lup_quality
            d = str(s.get("date") or "")          # market_sentiment 给的是 20260902, 档案按 YYYY-MM-DD 存
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
            q = await _lup_quality(iso)
            if q.get("有档案"):
                quality = q
        except Exception:
            quality = None
        # 接力: 昨天各格子里的票今天怎么样了 + 连板梯队迁移。只给统计与前几只, 不铺全名单
        # (agent 要的是"接得住接不住"这个判断, 铺 40 只成分是噪音)。
        relay = None
        try:
            from services.stock_tags import (pools as _pools, migration as _mig,
                                              pool_backtest as _pbt)
            p = await _pools()
            if p.get("可用"):
                mg = await _mig(p["锚点日"])
                # 历史分池是"今天这批数值算大还是算小"的唯一标尺: 单日 28/9/7 只说明不了
                # 任何事, 245 天的均值才是参照。不可用(覆盖率不够/对照没过)时整块省掉。
                bt = await _pbt()
                relay = {"锚点日": p["锚点日"],
                         "各池": [{"池": g["池"], "只数": g["只数"], **(g["统计"] or {}),
                                   "领涨": [x["名称"] for x in g["成分"][:3]]} for g in p["池"]],
                         "梯队迁移": (mg.get("梯队迁移") if mg.get("可用") else None),
                         "历史分池均值": ({"回放天数": bt["参与统计的天数"], "分池": bt["分池"],
                                          "幅度对照通过": bt["幅度对照通过"], "口径": bt["口径"]}
                                         if bt.get("可用") else None),
                         "口径": p["口径"]}
        except Exception:
            relay = None
        # 第二数据源(可选 provider): 东财没有的跳水榜/多空风向标/官方市场评价。
        # 按统计交易日取; 没接入或取不到就整块省 —— 别把"未接入"的提示塞进给模型的数字里。
        from services.providers import gateway as _pg
        d = str(s.get("date") or "")
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
        ext_senti = await _pg.try_call("sentiment", iso or None)
        return {"统计交易日": s.get("date_cn") or s.get("date"),
                "mood": s.get("mood"), "mood_desc": s.get("mood_desc"),
                "n_zt": s.get("n_zt"), "n_dt": s.get("n_dt"), "zbl_rate": s.get("zbl_rate"),
                "max_lianban": s.get("max_lianban"), "money_effect": s.get("money_effect"),
                "hot_sectors": [h.get("name") for h in (s.get("hot_sectors") or [])[:6]],
                "涨跌家数": s.get("breadth"),
                "涨跌分布": ({"统计日": s["zdfb"].get("qdate"),
                          "分布": {b["档"]: b["家数"] for b in s["zdfb"].get("bins") or []}}
                         if s.get("zdfb") else None),
                "涨停质量": quality,
                "接力": relay,
                "扩展源情绪": ext_senti,
                "note": "涨停/连板/炸板等指标属于 统计交易日 这一天; money_effect=上一交易日涨停的票在统计交易日的平均涨幅。"
                        "落笔时间一律用统计交易日的具体日期(带星期), 相对词(今天/昨天)按它换算。"
                        "涨跌家数(全市场+沪/深/北分市场)是调用时点的最新快照: 交易时段=此刻盘中实况, 收盘后或休市日=最近收盘的定格, 按此措辞引用。"
                        "涨跌分布=沪深两市每1%一档的家数直方图(不含北交所), 同为实时快照; ±档覆盖各自区间, 涨跌停的票按实际涨跌幅落档"
                        "(主板±10%在±10档, 创业/科创±20%在 涨>10%/跌>10% 档), 可用来说'跌是集中在-3%~-5%还是大面积深跌'这类结构。"
                        "涨停质量=逐只涨停的封单额(买一挂单额, 真实盘口量)聚合: 只数一样但封单合计差一倍就是两个盘, "
                        "描述打板情绪强弱时以它为主、只数为辅; 一字板多=共识硬, 尾盘才封多+开过板多=分歧大。为 null 表示那天还没落档, 此时别就封单下结论。"
                        "涨停质量.封单扎堆是按**行业**(静态分类)聚合、题材扎堆是按**当日炒作主线**(数字货币/光模块/机器人这种)聚合——"
                        "问'今天在炒什么主线/资金扎堆哪个题材/哪个方向最强'看题材扎堆(一只票可同时属多个题材, 已逐个拆开计数); "
                        "题材扎堆为 null 表示那天档案是回填的、没有题材标签。"
                        "接力=锚点日(上一交易日)各股池今天的实际表现+连板梯队迁移: 昨日首板/昨日连板/昨日反复板三个池的今日平均涨幅与红盘数, "
                        "以及各连板高度的接力率(昨日涨停的票今日仍涨停的比例)。回答'昨天那批今天接住了吗/打板还赚不赚钱/情绪在升温还是退潮'看这块——"
                        "它是已发生的事实而非预测。注意'昨日反复板'(连板数=1 但这波已涨停过几次的高位老票)与'昨日首板'(真新面孔)要分开说, 两者表现常相反。"
                        "接力.历史分池均值=245 个交易日回放的同池长期均值, 是判断'今天这个数算大还是算小'的唯一标尺——单日只有几十只样本, 不比不出强弱; "
                        "其中『超出同日涨停均值pp』才是关键列(减掉了当天全市场涨停股的平均次日收益, 否则大盘涨的日子每个池都好看)。"
                        "⚠ 高板池带生存者偏差: 3板的票是已通过两次接力筛选剩下的, 只能说'已经连上去的那批次日更强', 不能读成'买高板更好'。"
                        "扩展源情绪=可选的第二数据源, 补东财没有的三样: 跳水榜(冲高又跳水的票=资金出逃, 封单额的反面)、"
                        "多空风向标(全市场量能较昨日同期%, 负=缩量退场)、以及一句该源的官方市场评价。其'实际涨停'与本项目东财口径可能差几只(封住到收盘 vs 盘中触及), "
                        "两个源不一致时把两个数都说出来、指明口径差, 不要挑一个当唯一真值。为 null 表示没接扩展数据源、或那边这次没取到(非交易日或接口抖动)。"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_market_review() -> dict:
    """今日强势股复盘画像(扫全市场涨幅榜+成交额榜聚合)。"""
    try:
        from services.market_review import scan_strong_stocks
        return await asyncio.to_thread(scan_strong_stocks)
    except Exception as e:
        return {"error": str(e)}


# 以下四个由**可选的扩展数据源(provider)**供数。没接入的时候这几个工具压根不会挂给模型
# (见 _active_tools), 所以正常不会被调到; 真被调到就如实说没接入, 不编。

async def _tool_deep_lhb(code: str, date: str = "") -> dict:
    """深度龙虎榜席位(带游资身份标签)。凭证失效时如实报 need_login。"""
    from services.market_data import normalize_stock_code, is_a_share
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "深度龙虎榜仅支持 A 股"}
    from services.providers import gateway
    return await gateway.call("stock_lhb", _norm_code(code), date or None)


async def _tool_plate_bidding() -> dict:
    """板块竞价异动(当日集合竞价, 实时无历史)。凭证失效时如实报 need_login。"""
    from services.providers import gateway
    return await gateway.call("plate_bidding")


async def _tool_hot_themes() -> dict:
    """本月热门题材榜。凭证失效时如实报 need_login。"""
    from services.providers import gateway
    return await gateway.call("hot_themes")


async def _tool_inst_position(date: str = "", code: str = "") -> dict:
    """机构持仓。不传 code: A 股机构增仓/减仓榜(行业板块, 季报口径, 凭证失效时如实报 need_login);
    code=美股: 该股 13F 机构持仓; code=港股: 说明口径并指向权益披露。"""
    hu = _hkus(code) if code else None
    if hu and hu[0] == "US":
        from services import foreign_f10 as ff
        return await asyncio.to_thread(ff.us_inst_position, hu[1])
    if hu and hu[0] == "HK":
        return {"code": f"HK.{hu[1].zfill(5)}", "supported": False,
                "note": "港股没有类似美国 13F 的季度机构持仓全量披露; 港交所只要求持股≥5% 的主要股东与董事申报权益。"
                        "主要股东/董事持股、近期权益变动与回购用 get_shareholders 查看。"}
    from services.providers import gateway
    return await gateway.call("inst_position", date or None)


async def _tool_seat_history(q: str) -> dict:
    """龙虎榜席位追踪(名号/营业部名 → 近期上榜明细+统计)。"""
    try:
        from services.lhb_detail import seat_history
        return await seat_history(q)
    except Exception as e:
        return {"error": f"席位追踪失败: {e}"}


async def _tool_inst_flow(code: str = "") -> dict:
    """机构席位动向: 空=近30天全市场机构净买/净卖榜; 传6位代码=该股机构席位时间线。"""
    try:
        from services.inst_flow import inst_flow, inst_flow_for
        q = _norm_code(code or "").strip()
        if len(q) == 6 and q.isdigit():
            return await inst_flow_for(q)
        return await inst_flow(20)
    except Exception as e:
        return {"error": str(e)}


async def _tool_earnings(code: str = "") -> dict:
    """业绩预告/业绩日历: 空=A 股全市场预喜/预警榜(带持仓关联); 传6位代码=查该股预告;
    传港美股代码=该股下次/最近业绩日 + 最近一期业绩摘要。"""
    hu = _hkus(code) if code else None
    if hu:
        from services import foreign_f10 as ff
        fn = ff.hk_earnings if hu[0] == "HK" else ff.us_earnings
        try:
            return await asyncio.to_thread(fn, hu[1])
        except Exception as e:
            return {"error": f"港美股业绩日历获取失败: {e}"}
    try:
        from services.earnings_board import earnings_board
        b = await earnings_board(30)
        q = _norm_code(code or "").strip()
        if len(q) == 6 and q.isdigit():
            for r in (b.get("预喜") or []) + (b.get("预警") or []):
                if r["code"] == q:
                    return {**r, "期": b.get("period"),
                            "note": "幅度=归母净利同比变动中值%; 正式财报以披露日公告为准。"}
            return {"code": q, "期": b.get("period"),
                    "note": "该股最新报告期尚未披露业绩预告(预告只对大幅变动强制, 未披露不代表业绩差)。"}
        return b
    except Exception as e:
        return {"error": str(e)}


_TOOLS = [
    _LOAD_GUIDE_TOOL,
    {"name": "calc", "description": "算术兜底(Decimal 精算, 禁止心算时用它)。三种 op: "
     "eval=算表达式(expr, 只支持数字与 + - * / ( ) 和百分号, 如 '(4.36-3.96)/3.96*100'); "
     "market_cap=市值验算(price 现价, shares 股本(亿股), 可选 reported 外部报出的市值(亿), unit 默认'亿')——"
     "会告诉你算出来多少、与报出值偏差多少、以及是不是正好差 10/100/1000 倍(那基本就是单位或总股本/流通股本搞错了); "
     "cross=多来源交叉验证(field 字段名, values 各来源的值数组, 可选 tol_pct 默认1)——返回离散度与是否一致。"
     "凡是要写进结论的乘除、百分比、市值、同比环比, 都过这个工具再落笔; 它不做估值判断(不给内在价值/目标价)。",
     "input_schema": {"type": "object", "properties": {
         "op": {"type": "string", "enum": ["eval", "market_cap", "cross"]},
         "expr": {"type": "string"}, "price": {"type": "number"}, "shares": {"type": "number"},
         "reported": {"type": "number"}, "unit": {"type": "string"},
         "field": {"type": "string"}, "values": {"type": "array", "items": {"type": "number"}},
         "tol_pct": {"type": "number"}}, "required": []}},
    {"name": "resolve_stock", "description": "把股票名字或代码解析成标准代码+名称。用户报名字(如'锡业股份')时先调它拿代码。",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "股票名字或代码"}}, "required": ["query"]}},
    {"name": "get_quote", "description": "查个股实时行情: 现价/当日涨跌幅/开高低/成交额/换手。code 直接用 resolve_stock 返回的 code 原样传(A股是裸6位如 600171 / 000960; 港美股 HK.00700 / US.AAPL), 保持原样、A股无需 sh/sz 前缀。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_trend", "description": "查个股近 N 个交易日走势(裸K + 量): 累计涨跌/逐日涨跌/上涨天数。支持 A 股/港股/美股。daily_pct 每条是 {date, open_pct, pct, high_pct, low_pct, vol_ratio, shape}: date 是该日真实交易日(YYYY-MM-DD), open_pct/pct 是开盘/收盘相对昨收, high_pct/low_pct 是当日最高/最低相对昨收, vol_ratio 是当日量比(成交量/前5日均量, >1.5 放量、<0.7 缩量), shape 是这根K线的裸K形态(如 光头光脚阳线/长上影阴线/十字星)。最后一条即 last_date(最新交易日)。limit_pct=该股涨跌停幅度%(科创板688/创业板30开头=20, 北交所8/4开头=30, 沪深主板含ST=10), 别按10%默认。daily_pct 每条已带 板 字段(收在涨停封板/盘中触及涨停后回落/跌停, 已按该股真实涨停幅度判好, 直接用别自己算)。引用某天涨跌时日期以 date 字段为准。读裸K量价: 用 open_pct/pct/high_pct/low_pct 还原每根K线的开收高低位置 + shape 形态 + vol_ratio 量, 描述放量光头大阳=量价齐升、放量长上影=冲高回落分歧、缩量十字=观望、高位放量长上影=兑现等。无需分时即可还原历史每天盘中量价形态。structure 字段给阶梯式上行结构识别(进二退三框架, A股): 阶梯式上行(抬高高点+抬高低点)/抬高低点、台阶支撑(最近确认的上行低点价位)+距支撑%、回调量能(缩量=抛压衰竭洗盘特征/放量=分歧)、结构破位(收盘跌破上一抬高低点, 上行结构被破坏); 另含顶部/派发结构(进二退三镜像): 双顶(两个相近高点=M头, 附颈线价位)/二次冲高未创新高(右峰明显低于左峰=冲高动能衰减)、跌破颈线(顶部结构确认=高位资金共识破裂); 以及更多确定性形态: 头肩顶(肩头肩三高, 头部见顶)/头肩底(倒头肩三低, 底部企稳, 附底颈线)/突破底颈线、顶背离(价创新高但量能萎缩=上涨共识透支)/底背离(价创新低但量能萎缩=抛压衰竭)、2B假突破(斯波朗迪2B法则: 冲过前一swing高点但收盘已跌回该前高之下=突破失败, 上行趋势反转警讯, 附前高与冲高价)/2B假破位(击穿前低后收回其上=下行假破位, 空头动能衰竭信号, 附前低与下探价; 配合量能讲: 假突破放量滞涨更典型、假破位缩量下探更典型)、收敛三角(高点降+低点抬=变盘临近)、向上跳空缺口/向下跳空缺口([下沿,上沿]价位, 前复权后多为真实跳空)。用这些字段客观描述该股所处的趋势结构与所在台阶, 把方向性决策留给用户。本工具(A股)还会附一张我方数据渲染的K线图(蜡烛+量能+均线, 已标注台阶支撑/颈线), 你能直接看到它: 图下方那栏是**成交量(手)**不是成交额(轴上已标, 结果里的 图说明 字段也写了口径), 别把柱子的高度说成金额; 据图识别上面字段未编码的形态(头肩顶/旗形/收敛三角/量价背离/缺口等)作为补充, 凡引用具体价位/涨跌幅/量比仍以结构化字段为准(图负责形、数字负责数)。盘中(未收盘)时返回带 盘中=true + 盘中提示, 末根 daily_pct 标 量_盘中预估=true: 今天的量比是按已走交易时间折算的全天预估(粗估), 缩量/放量按'当前节奏预计'表述, 收盘前不下定论。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "days": {"type": "integer", "description": "默认20"}}, "required": ["code"]}},
    {"name": "get_intraday", "description": "单日分时走势, 判断盘中冲高回落/炸板/尾盘拉升时用, 比日K细。覆盖 A 股/港股/美股。A 股(需启用 TDX 数据源): 开盘/最高及时间/最低及时间/现价 + 较最高回落% + 路径采样。港股/美股(腾讯分时): 交易日 + 时区(港股=香港时间; 美股=美东时间, 给最近一个已开盘的交易日) + 开盘/最高/最低(带时刻与相对昨收涨跌%)/现价 + VWAP 及现价相对 VWAP% + 日内振幅% + 较最高回落% + 高点先于低点 + 成交量(股) + 量能分段(各时段成交占全天比例, 尾盘段含收盘竞价) + 路径采样。引用时刻时同时写明所属交易日与时区。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_news", "description": "查个股最近新闻(标题+摘要+时间), 用来找涨跌的消息面原因。支持 A股/港股/美股(东财)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_announcements", "description": "查个股公告, 结构化且比新闻权威, 看公司层面有没有实质事件驱动。覆盖 A 股/港股/美股。A 股=交易所公告(分红/回购/增减持/业绩预告/重组/股权激励); 港股=港交所披露易公告(东财转载的简体标题, 带日期/类型/链接), 每日回购的「翌日披露报表」合并为一条计数, key=true 标出业绩/股息/回购/并购/配售/关连交易等事件型公告; 美股=SEC EDGAR 申报, 8-K 是重大事件且带 Item 编号(如 1.01 签署重大协议、3.02 未登记股份发行), 归因盘前异动时比新闻更权威。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_fund_flow", "description": "查个股资金流: 主力/超大单/大单/中单/小单净额(亿, 与榜单 f62 同源) + 近几日趋势。按单笔金额分档(超大单+大单=主力)。重要口径提示: 当下普遍拆单 + 多子账户操作, 大单常被拆成中小单分散在多账户, 单笔分档已无法等同真实主力意图, 净流入只是参考线索而非定论。务必与 get_trend 的量价(pct+vol_ratio)和K线位置配合解读, 不单凭净流入下结论。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_lhb", "description": "龙虎榜: code+date→该股该上榜日的席位明细(买卖前五营业部名称+金额+席位画像: 机构/北向/常见量化通道, 回答'那天谁在买谁在砸/是量化砸的吗'); 仅 code→该股近期是否上榜及净买额/上榜原因; 都不传→最近交易日资金净买额榜(资金主线)。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; 留空看全市场榜"}, "date": {"type": "string", "description": "可选 YYYY-MM-DD, 与code一起给=查该日席位明细"}}}},
    {"name": "get_capital_allocation", "description": "资本配置台账(近10个年报期): 这家公司从股东手里**拿到**多少现金(股权融资实际到账)、**还回**多少(现金分红+已实施回购)、净额、窗口内累计分红占累计归母净利润的比例, 以及逐笔回购的金额/均价/回购当时的PB。回答'管理层对股东厚道吗/这公司只融资不分红吗/分红大方吗/回购是不是买在高位/管理层会不会花钱/资本配置怎么样'时用。融资额取的是现金流量表「吸收投资收到的现金」减子公司吸收少数股东投资 —— 换股吸收合并虽走定向增发但不进现金, 按这个口径自然不计入(拿发行额算会把一次换股并购算成天量抽血)。这是台账不是评价: 不打分不排名不给买卖也不评价管理层人品; 「拿得多还得少」本身不是缺点, 扩张期公司本该融资, 关键看融来的钱变成了什么(台账答不了这一步)。回购均价是当时名义价, 不要直接和现价比涨跌(中间分红送转会失真), 要比贵贱就用返回里的 回购PB 对 当前PB。要拉五个数据源约5-10秒。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "6位代码"}}, "required": ["code"]}},
    {"name": "screen_quality", "description": "去劣筛选(7 条硬指标, 排除法): 10年平均ROE<8% / 5年累计自由现金流为负 / 利息覆盖<2倍 / 长期毛利率<15% / 经营现金流比净利润<0.7 / 长期净利率<5% / 5年真实股本膨胀>20% —— 命中任一条即「排除」。回答'这家质地怎么样/够不够一流/是不是好公司/长期能不能拿/财务健康吗'时用。它给的是**多年**财务(近30期年报聚合), get_fundamentals 只给最新一期, 问长期质地必须用这个。结论只有 被排除/未被排除/判不了 三种: **未被排除≠值得买**, 只表示这7条没抓住它; 判不了=缺数据, 不许当成通过。门限沿用 ai-berkshire 的约定而非本项目实测, 转述时要带这句。第7条已扣除送股转股(送转不改变持股比例, 不是稀释)。银行/保险的利息覆盖不适用, 且整套对金融股适配有限(返回里会提醒)。要拉三个数据源约5-10秒, 一次只查一只。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "6位代码"}}, "required": ["code"]}},
    {"name": "get_red_flags", "description": "客观红线清单: 扫近期公告(监管处罚/立案/退市风险/业绩预亏/商誉减值/交易所问询/违规占用/股东减持等) + 解禁抛压 + 基本面健康度, 列出有事实依据的风险点(按高/中/低排)。回答'这票有没有雷/风险/暴雷过吗/能不能放心拿'时用。命中=有该风险事实, 非卖出建议。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_company_profile", "description": "查公司是做什么的 + 什么背景。覆盖 A 股/港股/美股。A 股: 公司简介(主营业务) + 细分行业 + 主营构成(各产品/地区收入占比和毛利率) + 控股/实际控制人/前三大股东(据此判断国资/央企/地方国企/中科院系/民营/外资性质)。港股: 简介 + 主营业务 + 东财行业与分类层级 + 上市日期/板块/港股通资格 + 总部与注册地 + 员工人数 + 财报披露币种 + 财年结束日 + 董事长/核数师。美股: 简介 + 行业 + 主营构成(按产品营收占比) + 上市日期/交易所 + 总部与注册地 + 员工人数 + 财报披露币种 + 财年结束日。回答'这家公司主营什么、靠什么赚钱、谁控股、什么背景、和同行业务差异'时必用。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_stock_concepts", "description": "查个股所属行业/概念 + 核心题材。判断'这只票属于哪个概念、是否属于当下资金主线/热门概念'时用; 可与 get_hot_concepts 交叉印证。覆盖 A 股/港股/美股。A 股给概念板块与题材; 港股给东财港股行业 + 行业分类层级(大类到细类) + 证券类型(H股/非H股) + 港股通资格(深港通/沪港通); 美股给 板块/行业 分类与曾用名(用于核实公司身份与更名)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_fundamentals", "description": "查个股基本面+估值, 回答'这票贵不贵、业绩好不好、盈利质地、有没有业绩拐点'时用。覆盖 A 股/港股/美股。A 股: 营收/净利及同比、ROE/毛利率/净利率、资产负债率、每股收益 + PE(TTM)/PB/总市值/行业。港股: 最新报告期财务(东财把港股财报统一折成人民币列示, 金额币种=CNY) + 财报披露币种(原始币种, 如腾讯=CNY、汇丰=USD) + 按港元现价现算的 PE_TTM/静态PE/PB/总市值, EPS_TTM/BPS 字段逐项给出 原币→换算汇率→折算后 的过程。美股: 最近年报财务(原币) + 按现价现算的 PE_TTM/PB/总市值, 非美元披露(如 ADR 以人民币披露)的同样列出折算过程。转述估值时引用返回的倍数与折算后每股数。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_commodity", "description": "查个股关联的大宗商品期货价(有色金属股: 铜/铝/金/锌/镍/锡 走上期所连续合约)。判断有色股涨跌是不是金属价驱动时用; 钨/锑/稀土/锂等小金属无交易所合约会返回不可得。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_peers", "description": "同行横向对比, 回答'同业里它贵不贵、谁领涨、资金更偏好谁、龙头是谁'时用; 可配合 get_fundamentals 看相对估值。覆盖 A 股/港股/美股。A 股给板块成分股的涨跌幅/PE/PB/主力净流入; 港股给东财同行业按市值排前 10 的公司(含本股) + 现价/涨跌幅/市值(亿港元)/PE_TTM/PB + 行业 PE 平均与中值; 美股给相似标的及各自涨跌(含盘前/盘后)。港股与美股的同行结果只含价格与估值字段。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_shareholders", "description": "筹码面/股东结构。覆盖 A 股/港股/美股。A 股: 十大流通股东及增减持、北向(香港中央结算)持股变动、未来限售解禁(抛压)。港股: 港交所权益披露的主要股东(持股≥5%)与董事持股、近期权益变动(好仓/淡仓、变动股数、变动后持股%)、近 90 天回购汇总(天数/股数/金额/区间均价)。美股: 13F 机构持股占比与前十大机构(含申报日与变动) + 内部人交易汇总(近3月/12月买卖笔数与股数、最近几笔)。回答'谁在持股、控股股东/大股东/北向/机构在加还是减、有没有解禁压力、公司在不在回购'时用。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_zsxq_digest", "description": "读用户已接入的知识星球最近 N 天主题(默认1天)。用于回答「情绪面/复盘博主怎么看今天的盘」「星球里在讲什么逻辑」这类**观点面**问题, 补指标看不到的文本面。返回字段 stance=opinion —— 是某个人的看法不是事实, 按 [星球观点] 标注并带作者与日期; 数字与结论仍以 [实测]/[联网] 为准。仅在用户问观点/社群/复盘看法时用。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "description": "回看天数, 1-7, 默认 1"}, "all_authors": {"type": "boolean", "description": "默认取全部作者; 传 false 则只要星主及合伙人的帖"}}}},
    {"name": "search_zsxq", "description": "在已接入的知识星球里全文搜关键词(个股名/题材/概念), 看社群里有没有人讲过它。服务端语义搜索, 会漏召也会误召, 只当线索; 返回 stance=opinion, 按 [星球观点] 标注, 不作为数字依据、不转成买卖建议。",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string", "description": "搜索词, 如 股票名/题材词"}}, "required": ["keyword"]}},
    {"name": "read_zsxq_file", "description": "读知识星球某个附件的正文(券商研报/会议记录 docx·pdf)。get_zsxq_digest 返回的帖子里带 附件列表(file_id + 名称), 想看某份研报讲了什么就用它取正文。内容是第三方研报, 只按要点提炼转述(讲了什么/关键数字/结论), 不整篇复述; 扫描件取不到文字会如实说明。",
     "input_schema": {"type": "object", "properties": {"file_id": {"type": "string", "description": "附件 id, 从 附件列表 取"}, "name": {"type": "string", "description": "文件名(可选, 用于判断格式)"}}, "required": ["file_id"]}},
    {"name": "get_today_pnl", "description": "查用户**今天**的盈亏与操作: 全账户当日盈亏(券商口径, 含今天清仓/新建的仓) + 逐只明细(今日盈亏、今日买卖股数、是否今日清仓/新建) + 今天卖出的每只的卖出均价、卖出前持仓成本、本次卖出已实现盈亏。回答'今天怎么样/今天赚了亏了多少/今天我做了什么/今天的操作对不对/复盘今天'时必用 —— 今天清仓的票不在 get_holdings 里, 只有这里有。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_holdings", "description": "查用户当前**全部**在持: A股(代码/名称/股数/综合成本/持有天数) + 其他持仓(场内ETF/场外基金/理财/现金/加密/机器人, 含份额或金额; 有定投计划的基金带 定投计划 字段——频率+每期金额)。回答'我的持仓/我有什么/我持有啥/哪些在定投/跟我持仓的关系'时用——持仓不止A股, 用户还有基金/ETF/现金/理财/机器人。要各大类占比或现金理财结构分析则用 get_asset_allocation。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_thesis", "description": "读用户当初记录的买入逻辑(为什么买这只)+ **逻辑漂移**。回答'我当初为什么买X、X的逻辑还成立吗、帮我复盘X'时用: 拿到 thesis 后对照现价/基本面/消息/红线, 客观说每条理由还成不成立。传 code 时另带漂移分析: 这段逻辑改过几版、每一版是跟着基本面改的还是**只跟着股价**改的(后者=事后合理化的迹象, 带当时的浮亏)、末版之后事实又变了什么、以及逐句 diff(改写/删除/新增)。复盘改过的逻辑必须先说它被改过 —— 拿照着股价改过的说法去对现状, 必然自洽。不传 code 看全部持仓的逻辑(不含漂移)。仅当用户记过才有。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选, 留空看全部"}}}},
    {"name": "get_asset_allocation", "description": "查用户全量资产配置与总盈亏: 总资产 + 各大类(股票/现金/理财/基金/加密/机器人)市值占比 + **全口径盈亏**(总盈亏/浮动/已实现, 与看板顶栏同一算法) + 现金理财逐笔明细(金额/年化/持有天数)。回答'我总共赚了/亏了多少、我的总盈亏、回本了吗、浮亏多少、已经亏掉多少'以及'现金理财怎么分配、应急金够不够、资产结构合不合理'时用——总盈亏只有这个工具有, get_holdings 只给成本与份额、不含盈亏。不涉及个股买卖。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_exposure", "description": "查**穿透后的真实敞口**(同源风险): 把持有的基金/ETF 拆到季报前十大, 把直持个股与基金间接持有的同一标的合起来算, 再按行业归并。回答'我实际押了多少在X上/持仓是不是重复下注/买的这几只基金是不是同一注/哪个方向敞口过大/分散得够不够'时用。只按名字或板块看会漏——「山金国际」名字里没有金属字样, 基金名也不说它拿了什么票。口径: 只到前十大(占基金净值约 25%~50%), 所以给出的是**下限**; 拉不到明细的基金单列, 不等于敞口为 0。结构同源看这个, 走势同源(相关性)是另一回事。传 as_of=YYYY-MM-DD 可回看**那天**的结构(复盘用: 当下的敞口里已经没有那天清掉的票了; 结果里的 basis 写明那天的估值来路, warnings 首条也会标出这是回看)。",
     "input_schema": {"type": "object", "properties": {"min_pct": {"type": "number", "description": "可选, 只看占总资产 ≥ 这个百分比的标的, 默认 0.5"}, "as_of": {"type": "string", "description": "可选, YYYY-MM-DD; 回看那天的持仓结构, 留空=当下"}}}},
    {"name": "get_trades", "description": "查用户成交记录(含个股/场内ETF/场外基金): 传 code→该标的买卖/加减仓/分红或申赎流水(A股另给综合成本/已实现盈亏/持有天数, 同日有买有卖=做T); 不传→最近全部成交(三类合并)。可用 start/end(YYYY-MM-DD)按成交日期筛区间('这周/6月/上个月'自己换算成日期传)。回答'我什么时候买的、成本多少、做过几次T、这票赚没赚、持有多久、最近/某段时间交易了啥、哪些买入是定投'时用(定投计划自动买入的行带 来源=定投)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; 留空看全部"}, "start": {"type": "string", "description": "可选, 起始日 YYYY-MM-DD"}, "end": {"type": "string", "description": "可选, 截止日 YYYY-MM-DD"}}}},
    {"name": "get_market_sentiment", "description": "查大盘打板情绪(涨停数/连板高度/炸板率/赚钱效应/热点板块)、全市场涨跌家数(几家上涨几家下跌, 含沪/深/北分市场)和涨跌分布直方图(每1%一档的家数, 看下跌集中在哪个深度), 回答'今天普跌吗/多少家在跌/跌得有多深/赚钱效应', 判断是个股原因还是大盘普涨普跌; 也用于判断市场风格(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转)。另带 涨停质量: 逐只涨停的封单额(=买一挂单额, 真实盘口量)聚合出封单合计/中位数/一字板只数/尾盘才封只数/开过板只数/封单最厚前五/封单扎堆(按行业)/题材扎堆(按当日炒作主线, 如数字货币·光模块·机器人) —— 只数只告诉你有多少票涨停, 封单额才告诉你那些涨停有多硬(同样52个涨停, 57亿封单和20亿封单是两个盘); 题材扎堆看资金主线扎在哪个方向。接了扩展数据源时另带 扩展源情绪(第二数据源): 跳水榜(冲高跳水=资金出逃)/多空风向标(量能较昨同期%)/该源官方市场评价, 是东财口径之外的交叉印证。问'今天打板情绪强不强/涨停结实吗/封单怎么样/在炒什么主线/资金扎堆哪个题材/有没有票冲高跳水/量能是放还是缩/情绪比昨天好还是差'时看这一块。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_etf_xray", "description": "ETF 题材透视(避雷): 用基金季报真实成分股对照名称宣称的主题, 给出 主题匹配权重%/警示(贴题·有偏离·偏离显著)/行业分布/前十大成分(逐只标贴题与否)。query 传主题词(如 红利/家电/半导体)时 = 找该主题规模最大的前5只逐只透视(只看大规模的, 小盘ETF流动性差); 传6位基金代码 = 透视这一只; 留空 = 透视用户在持的全部场内ETF。回答'这只ETF名不副实吗/XX主题买哪只ETF靠谱/我的ETF成分是啥/有没有挂羊头卖狗肉'时用。宽基/风格类(红利等)会标注行业口径不适用, 看行业分布与成分即可。数据=季报(滞后一季度), 表述时注明。",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "主题词(红利/半导体) 或 6位基金代码; 留空=在持场内ETF"}}}},
    {"name": "get_market_review", "description": "今日强势股复盘画像(扫全市场涨幅榜+成交额榜聚合): 涨停数、板块/概念扎堆(资金主线)、风格(小盘高换手妖股 vs 大盘低换手趋势)、领涨样本、吸金榜。回答'今天什么风格的票在涨/今天强势股有什么共性/资金扎堆在哪/帮我复盘今天市场'时用。比 get_market_sentiment 多了'强势股结构与扎堆方向'这层。结合 get_holdings 可点出用户持仓沾不沾今日主线。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_inst_flow", "description": "机构席位动向(龙虎榜机构专用席位买卖统计): code 留空=近30天全市场机构净买入/净卖出榜, 每行带 距最近/首次上榜日至今涨跌%——大额净买入+至今大跌 即市场说的'机构接在山顶', 净卖出+至今大跌='机构跑对了'; 传6位代码=该股机构席位事件时间线。回答'机构最近在买什么/XX是不是机构被套/机构在这只票上怎么操作的'时用。上榜日才披露(抽样非全量), 表述时注明。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "6位代码查单票; 留空看全市场榜"}}}},
    {"name": "get_deep_lhb", "description": "深度龙虎榜(扩展数据源): 传 code(+可选 date)→该股该日龙虎榜买卖席位明细, 每个席位带营业部名 + 买卖金额 + **游资分组标签**(知名游资/机构专用/量化抢筹这类身份识别)。与 get_lhb 的区别: get_lhb 走东财只给裸营业部名, 这个多一层游资身份标签, 回答'那天是哪个游资在买/是不是知名游资进场/机构还是量化'时更准。盘后16:30后当日数据才全。由可选的扩展数据源供数, 凭证失效时返回里带 need_login=true, 此时提示用户去设置页更新扩展数据源凭证。仅 A 股, 已披露客观数据非买卖建议。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "6位股票代码"}, "date": {"type": "string", "description": "可选 YYYY-MM-DD, 默认最近交易日"}}, "required": ["code"]}},
    {"name": "get_plate_bidding", "description": "板块竞价异动(扩展数据源): 早盘集合竞价(约9:15-9:25)阶段被资金抢筹的板块及领涨个股, 分三组——今日新增竞价异动 / 昨日爆发板块延续异动 / 其他异动板块, 每个板块带 竞价换手·竞价涨幅·竞价主力净额亿。回答'今天竞价哪些板块异动/开盘资金主攻什么方向/竞价领涨板块是谁'时用, 是判断当天主线的开盘先手信号。**只有当日、无历史**, 且数据只在集合竞价前后(约9:15-9:31)产出, 非该时段返回 有数据=false 并说明是时段问题(不是故障)。由可选的扩展数据源供数, 凭证失效返回 need_login=true, 提示去设置页更新。仅A股, 客观数据非买卖建议。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_hot_themes", "description": "本月热门题材榜(扩展数据源): 本月资金关注度最高的题材/板块, 按热度降序排名。回答'现在市场主线题材是什么/这个月哪些题材最热/题材轮动到哪了'时用, 是月度视角的主线判断(补 get_sector_momentum 的日度动能和涨停题材的当日视角)。由可选的扩展数据源供数, 凭证失效返回 need_login=true。客观数据非买卖建议。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_inst_position", "description": "机构持仓。不传 code=A 股机构增仓/减仓榜(扩展数据源): 按最新季报持仓, 机构在哪些行业板块增仓/减仓(增仓金额带符号)+机构持仓市值+占流通比, 回答'机构最近在加仓什么方向/在减持哪些板块/中线往哪搬仓', 属于中线季度持仓变化, 与 get_inst_flow(龙虎榜机构席位=短线盘口)互补; date 可传季报日默认最新; 凭证失效时返回 need_login=true。传美股 code=该股 13F 机构持仓: 机构持股占比 + 相对上季增持/减持/持平/新进/清仓的机构家数与股数 + 前十大机构(申报日/持股/变动) + 近四个季度机构家数与持股占比趋势(申报期内的季度标 申报未齐)。传港股 code=返回口径说明(港股只有≥5%主要股东与董事的权益披露, 由 get_shareholders 提供)。季报滞后, 客观持仓数据非买卖建议。",
     "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "可选季报日 YYYY-MM-DD, 仅 A 股板块榜使用, 默认最新可用季报"}, "code": {"type": "string", "description": "可选; US.AAPL 查该美股 13F 机构持仓, HK.00700 返回港股口径说明; 留空看 A 股板块榜"}}}},
    {"name": "get_seat_history", "description": "龙虎榜席位追踪: 传席位名号(章盟主/陈小群/拉萨天团)或营业部名(子串/全名), 返回该席位近90天上榜明细(股票/净额/上榜后1·5·10日涨跌)+客观统计(上榜次数/净买入后1日与5日红盘率)。回答'章盟主最近在买什么/这个席位胜率怎么样/大佬说XX进场了帮我看看'时用。名号映射来自公开名录会漂移, 统计是纯历史描述, 表述时注明。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string", "description": "席位名号或营业部名"}}, "required": ["q"]}},
    {"name": "get_earnings", "description": "业绩预告与业绩日历。code 留空=A 股全市场业绩预告: 预喜榜(预增/扭亏, 按归母净利同比幅度排)+预警榜(预减/首亏)+持仓关联清单(直持或经由在持ETF成分), 回答'哪些股票业绩好/最近业绩雷有哪些/我持仓相关的业绩怎么样'。传 A 股6位代码=查该股最新报告期预告(未披露预告属常态, 预告只对大幅变动强制, 表述时注明)。传港股代码=下次业绩董事会日期(取自公司董事会会议通告正文) + 最近一次业绩公告(日期/标题/链接) + 最新报告期财务摘要。传美股代码=下次业绩日(Nasdaq, 标明是公司已排期还是按历史规律预计, 含盘前/盘后) + 近四季 EPS 与一致预期及超预期% + 最近一季财报摘要。正式财报数字用 get_fundamentals。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; A股6位代码 / HK.00700 / US.AAPL; 留空看A股全市场榜"}}}},
    {"name": "get_sector_momentum", "description": "板块趋势矩阵: 各行业近N日累计涨跌/连涨动能/净流入 + 量能趋势(近3日均量/前段均量, >1.2量能放大、<0.8萎缩)和量价 tag(放量上行=量价配合趋势健康/缩量上行=动能衰减/放量下跌=抛压重等)。判断板块是真上升趋势(涨+量价配合+资金顺)还是虚涨(涨但缩量/资金流出)。days 趋势窗口可传 5(短线)/10(中期)/20(中长期), 默认10; 问'短期/这几天'传5, '近一个月趋势'传20。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "get_hot_rank", "description": "资金人气榜(东财): 关注度最高的个股, 标出哪些在用户持仓。看资金主线/抱团方向。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "description": "趋势窗口交易日数, 5/10/20, 默认10"}}}},
    {"name": "get_hot_concepts", "description": "今日热门概念板块榜(概念粒度, 比行业更细, 如 CPO/HBM/先进封装/玻璃基板/固态电池等): 涨幅+主力净流入。回答'量化/资金这几天在冲哪个具体概念、概念怎么切'时用它。",
     "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "description": "默认15"}}}},
    {"name": "get_board_stocks", "description": "查某个板块/概念里今日涨幅 top-N 的个股(龙头): 涨跌幅/现价/换手/主力净流入。找到主线概念后看里面哪几只领涨、资金集中在谁身上。board 传概念或行业名(如 玻璃基板/CPO/光通信/小金属)或 BK 代码。",
     "input_schema": {"type": "object", "properties": {"board": {"type": "string"}, "top": {"type": "integer", "description": "默认12"}}, "required": ["board"]}},
    {"name": "get_market_news", "description": "全市场财经快讯(含政策面/国家调控: 货币财政、央行、证监会/部委监管、产业政策、行业调控、出口管制/关税、国常会/政治局等重要会议)。分析市场背景、判断政策驱动/调控影响时必看; policy_news 是政策相关筛选。",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "默认40"}}}},
    {"name": "get_chain_quote", "description": "批量取一组票的多周期量价摘要(产业链全景/多票横向对比专用): 一次返回每只的 pct_5d/pct_20d/pct_60d 涨幅、dist_20high 距20日高、ma 均线排列(全多头/多头/短多头/纠缠/空头)、vol 量能(放量/平/缩量)。做'X产业链上游到下游量价一览'时: 先 web_search 拿到该产业链各环节代表公司, 把这串代码/名称一次传进来即可拿到整条链量价, 无需逐只 get_trend。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"stocks": {"type": "array", "items": {"type": "string"}, "description": "股票名称或代码列表, 最多24只"}}, "required": ["stocks"]}},
    {"name": "get_coiled_stocks", "description": "横盘蓄势观察池(龙头池内结构筛选+AI看图复核): 找'仍在安静横盘蓄势中(突破前)'的百亿机构票, 返回每只的横盘天数/箱体振幅/缩量比/距上沿%/近3日量能/AI贴合度与判词。用户问'有哪些在横盘蓄势/打底/准备启动的票'时用它; 结果是客观结构描述, 横盘可能向下解决, 表述时保持结构层面。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_global_indices", "description": "全球指数/汇率/商品实时行情: A股大盘(上证/深成/沪深300/创业板/科创50/科创100/北证50)、港股(恒生/恒科/国企)、美股(道琼斯/纳斯达克/标普)、海外(日经225/韩国KOSPI/伦敦FTSE)、汇率(USDCNH等)、贵金属/工业金属/能化期货。问大盘、外围市场、某国指数、汇率、金铜油价时用它, 不用 web_search。query 传指标名子串(如 KOSPI/纳斯达克/沪金)只取匹配项, 留空返回全部。",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "指标名/组名子串过滤, 空=全部"}}}},
    {"name": "read_url", "description": "抓取某个网页的正文全文(干净 markdown)。web_search 给的是摘要片段, 当需要某篇文章的完整内容时用它读全——尤其: 产业链/行业深度梳理研报(把各环节代表公司抽全更准)、核实某条事实的原文细节、读公告/政策原文。先用 web_search 拿到 url, 再对最相关的 1-2 篇 read_url 读全。",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "要抓取的 http(s) 网页链接"}}, "required": ["url"]}},
    # Anthropic 服务端联网搜索: 本地工具查不到/可能过期的事实(海外公司是否上市/IPO/代码/政策/最新消息)用它核实, 以联网结果为准而非凭记忆。
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
]

async def _tool_calc(a: dict) -> dict:
    """算术兜底。关键数字不许心算 —— 乘除/百分比/市值验算/多源交叉一律过这儿。"""
    from services import calc_rigor as _cr
    op = (a.get("op") or "eval").strip()
    if op == "market_cap":
        return _cr.verify_market_cap(a.get("price"), a.get("shares"),
                                     a.get("reported"), a.get("unit") or "亿")
    if op == "cross":
        return _cr.cross_validate(a.get("field") or "该字段", a.get("values") or [],
                                  float(a.get("tol_pct") or 1.0))
    return _cr.evaluate(a.get("expr") or "")


async def _async_value(v):
    return v


_EXECUTORS = {
    "load_guide": lambda a: _async_value(_load_guide(a.get("topics"))),
    "calc": lambda a: _tool_calc(a),
    "resolve_stock": lambda a: _tool_resolve_stock(a.get("query", "")),
    "get_quote": lambda a: _tool_get_quote(a.get("code", "")),
    "get_trend": lambda a: _tool_get_trend(a.get("code", ""), a.get("days", 20)),
    "get_intraday": lambda a: _tool_intraday(a.get("code", "")),
    "get_news": lambda a: _tool_get_news(a.get("code", "")),
    "get_announcements": lambda a: _tool_announcements(a.get("code", "")),
    "get_fund_flow": lambda a: _tool_fund_flow(a.get("code", "")),
    "get_lhb": lambda a: _tool_lhb(a.get("code", ""), a.get("date", "")),
    "get_seat_history": lambda a: _tool_seat_history(a.get("q", "")),
    "get_deep_lhb": lambda a: _tool_deep_lhb(a.get("code", ""), a.get("date", "")),
    "get_plate_bidding": lambda a: _tool_plate_bidding(),
    "get_hot_themes": lambda a: _tool_hot_themes(),
    "get_inst_position": lambda a: _tool_inst_position(a.get("date", ""), a.get("code", "")),
    "get_red_flags": lambda a: _tool_red_flags(a.get("code", "")),
    "screen_quality": lambda a: _tool_screen_quality(a.get("code", "")),
    "get_capital_allocation": lambda a: _tool_capital_allocation(a.get("code", "")),
    "get_company_profile": lambda a: _tool_company_profile(a.get("code", "")),
    "get_stock_concepts": lambda a: _tool_stock_concepts(a.get("code", "")),
    "get_fundamentals": lambda a: _tool_fundamentals(a.get("code", "")),
    "get_commodity": lambda a: _tool_commodity(a.get("code", "")),
    "get_peers": lambda a: _tool_peers(a.get("code", "")),
    "get_shareholders": lambda a: _tool_shareholders(a.get("code", "")),
    "get_holdings": lambda a: _tool_get_holdings(),
    "get_today_pnl": lambda a: _tool_today_pnl(),
    "get_zsxq_digest": lambda a: _tool_zsxq_digest(a.get("days", 1), a.get("all_authors")),
    "search_zsxq": lambda a: _tool_zsxq_search(a.get("keyword", "")),
    "read_zsxq_file": lambda a: _tool_zsxq_file(a.get("file_id", ""), a.get("name", "")),
    "get_thesis": lambda a: _tool_get_thesis(a.get("code", "")),
    "get_asset_allocation": lambda a: _tool_asset_allocation(),
    "get_exposure": lambda a: _tool_exposure(float(a.get("min_pct") or 0.5), a.get("as_of", "")),
    "get_trades": lambda a: _tool_trades(a.get("code", ""), a.get("start", ""), a.get("end", "")),
    "get_market_sentiment": lambda a: _tool_market_sentiment(),
    "get_etf_xray": lambda a: _tool_etf_xray(a.get("query", "")),
    "get_market_review": lambda a: _tool_market_review(),
    "get_inst_flow": lambda a: _tool_inst_flow(a.get("code", "")),
    "get_earnings": lambda a: _tool_earnings(a.get("code", "")),
    "get_sector_momentum": lambda a: _tool_sector_momentum(a.get("days", 10)),
    "get_hot_rank": lambda a: _tool_hot_rank(),
    "get_hot_concepts": lambda a: _tool_hot_concepts(a.get("top", 15)),
    "get_board_stocks": lambda a: _tool_board_stocks(a.get("board", ""), a.get("top", 12)),
    "get_chain_quote": lambda a: _tool_chain_quote(a.get("stocks", [])),
    "read_url": lambda a: _tool_read_url(a.get("url", "")),
    "get_market_news": lambda a: _tool_market_news(a.get("limit", 40)),
    "get_global_indices": lambda a: _tool_global_indices(a.get("query", "")),
    "get_coiled_stocks": lambda a: _tool_coiled(),
}


async def _run_tool(tu: dict) -> dict:
    """跑单个工具调用, 把异常/未知工具兜成 {"error":...}。供 asyncio.gather 并发执行。

    顺便记工具缺口: 取不到数时"工具没报错、模型照着空数据往下答"是最难发现的一类问题
    (实测 get_news 对美股返回 10 条但最新一条比异动早三天), 记成台账才不用等用户看出来。
    """
    name = tu.get("name")
    args = tu.get("input") or {}
    try:
        fn = _EXECUTORS.get(name)
        if not fn:
            return {"error": f"未知工具 {name}"}
        out = await fn(args)
    except Exception as e:
        out = {"error": str(e)}
    try:
        from services import tool_gaps
        await tool_gaps.record(name, args, out)
    except Exception:
        pass
    return out


def _failed_steps(tus: list, outs: list) -> list[dict]:
    """哪些工具这一轮取数失败了 → step_result 事件。

    step 事件是在工具**跑之前**发的, 不回填成败的话失败的工具在界面上跟成功的长得一模一样
    (照样打绿勾), 于是"某一块其实没数、模型用记忆圆过去了"这件事完全看不出来。
    抽成函数只为了可测: 事件发射埋在 agent loop 里, 不然要起真 LLM 才验得到。
    """
    out = []
    for tu, o in zip(tus, outs):
        if isinstance(o, dict) and o.get("error"):
            out.append({"type": "step_result", "tool": tu.get("name"), "ok": False,
                        "err": str(o.get("error"))[:80]})
    return out


def _result_content(out: dict):
    """工具输出 → tool_result.content。普通输出=JSON 字符串; 带图(get_trend 渲染的K线)=
    [text, image] 块列表, 把图作为多模态输入喂给模型(base64 不进 text, 避免巨串)。"""
    if isinstance(out, dict) and out.get("_chart_png_b64"):
        out = dict(out)
        b64 = out.pop("_chart_png_b64")
        media = out.pop("_chart_media", "image/png")
        return [{"type": "text", "text": _json.dumps(out, ensure_ascii=False)},
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}]
    return _json.dumps(out, ensure_ascii=False)


_ZSXQ_TOOLS = ("get_zsxq_digest", "search_zsxq", "read_zsxq_file")

# 扩展数据源(provider)供数的工具 → 它依赖的那项能力。provider 没声明该能力就不挂这个工具。
_PROVIDER_TOOLS = {
    "get_deep_lhb": "stock_lhb",
    "get_plate_bidding": "plate_bidding",
    "get_hot_themes": "hot_themes",
    "get_inst_position": "inst_position",
}


async def _active_tools() -> list:
    """web_search 是 Anthropic 服务端工具, 只有官方端点支持; 若切到 DeepSeek/硅基流动等
    非 Anthropic 厂商, 必须去掉它, 否则请求会被对方拒绝。其余自定义工具各厂商通用。

    知识星球与扩展数据源的工具都是可选接入(默认没有), 没接就别塞给模型 —— 否则它会去调
    然后拿一串 error, 既浪费一轮又容易让它把"没接入"读成"查不到这只票"。"""
    tools = _TOOLS
    try:
        from services import zsxq_client
        if not zsxq_client.is_enabled():
            tools = [t for t in tools if t.get("name") not in _ZSXQ_TOOLS]
    except Exception:
        tools = [t for t in tools if t.get("name") not in _ZSXQ_TOOLS]
    try:
        from services.providers import gateway
        caps = await gateway.capabilities()
    except Exception:
        caps = frozenset()
    tools = [t for t in tools
             if _PROVIDER_TOOLS.get(t.get("name")) in (None, *caps)]
    try:
        if _llm._is_anthropic_official():
            return tools
    except Exception:
        pass
    return [t for t in tools if t.get("type") != "web_search_20250305"]



def _system() -> str:
    """系统提示 + 当前日期(让 agent 能把'这周/本月/上个月'换算成 get_trades 的 start/end)。"""
    import datetime as _dt
    d = _dt.date.today()
    wk = "一二三四五六日"[d.weekday()]
    monday = (d - _dt.timedelta(days=d.weekday())).isoformat()
    weekend = d.weekday() >= 5  # 周六/周日 A股休市(节假日未单列, 以工具返回日期为准)
    last_trade = d - _dt.timedelta(days=d.weekday() - 4) if weekend else d
    ltwk = "一二三四五六日"[last_trade.weekday()]
    mkt = (f"今天周末休市, 行情/资金/情绪类工具返回的是最近交易日 周{ltwk}({last_trade.isoformat()}) 的收盘快照。"
           f"引用这些数据时, 用'周{ltwk}收盘'或具体日期({last_trade.isoformat()})指代, 当前问题里以'今日/今天/盘中'指代的均换算为该交易日, 周末无盘中数据。"
           if weekend else
           "今天是交易日, 行情类工具盘中返回实时滚动值、收盘后返回当日收盘值, 可用'今日'指代。")
    # 已批准的沉淀规则接在固定正文之后、日期之前: 日期天天变, 让它留在最末尾, 前面这
    # 一大段当天才是逐字节稳定的(prompt 缓存的断点就打在 system 末尾, 见 llm_client)。
    from services.rule_forge import active_rules_sync, render_rules
    learned = render_rules(active_rules_sync())
    return _SYSTEM + learned + (
        f"\n【今天】{d.isoformat()} 周{wk}; 本周一={monday}, 本月1号={d.replace(day=1).isoformat()}。"
        f"用户问时间范围时据此换算 start/end。\n【交易日状态】{mkt}")


_TOOL_CN = {
    "load_guide": "读作答规范",
    "resolve_stock": "解析代码", "get_quote": "查行情", "get_trend": "查走势",
    "get_news": "查新闻", "get_intraday": "查分时", "get_announcements": "查公告", "get_fund_flow": "查资金流", "get_lhb": "查龙虎榜", "get_seat_history": "查席位历史", "get_deep_lhb": "查深度龙虎榜",
    "get_company_profile": "查公司主营", "get_red_flags": "查红线风险", "screen_quality": "跑去劣筛选", "get_capital_allocation": "查资本配置", "get_stock_concepts": "查所属概念", "get_fundamentals": "查基本面", "get_commodity": "查商品价",
    "get_peers": "同行对比", "get_shareholders": "查股东解禁",
    "get_holdings": "看持仓", "get_today_pnl": "看今日盈亏", "get_zsxq_digest": "读星球", "search_zsxq": "搜星球", "read_zsxq_file": "读星球附件", "get_thesis": "看买入逻辑", "get_asset_allocation": "看资产配置", "get_trades": "查成交记录", "get_market_sentiment": "看大盘情绪", "get_market_review": "复盘强势股", "get_inst_flow": "查机构动向", "get_earnings": "查业绩预告",
    "get_sector_momentum": "看板块动量", "get_hot_rank": "看资金热度",
    "get_hot_concepts": "看热门概念", "get_board_stocks": "查板块龙头", "get_market_news": "看政策快讯", "web_search": "联网搜索",
    "get_chain_quote": "产业链量价", "read_url": "读网页全文", "get_global_indices": "看全球指数", "get_coiled_stocks": "扫横盘蓄势",
}


def _clean_answer(text: str) -> str:
    """模型 web_search 后常在正文里内联 <cite index="3-1">...</cite> 这种引用标签(非结构化 citation),
    前端按纯文本渲染会原样露出。剥掉标签保留里面文字。"""
    t = text or ""
    t = _re.sub(r"</?cite[^>]*>", "", t)          # <cite index="x">/<cite ...>/</cite>
    t = _re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _collect_sources(content: list, acc: list, seen: set) -> list:
    """从一轮响应里抽 web_search 命中的网页来源(标题+url), 按 url 去重累加到 acc。
    结构: web_search_tool_result 块的 content 是 [{type:web_search_result, title, url, page_age}]。
    返回本轮新增的来源(供流式实时推送)。"""
    fresh = []
    for b in content:
        if b.get("type") != "web_search_tool_result":
            continue
        inner = b.get("content")
        if not isinstance(inner, list):
            continue
        for r in inner:
            if r.get("type") != "web_search_result":
                continue
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item = {"title": (r.get("title") or url).strip(), "url": url, "age": r.get("page_age")}
            acc.append(item)
            fresh.append(item)
    return fresh


def _assemble_cited_text(content: list, sources: list, seen: set) -> str:
    """拼接最终答案文本, 在每个带结构化 citations 的文字段尾插入 ⟦N⟧ 角标。
    N = 该来源在 sources 列表中的 1-based 序号(与底部'联网来源'列表同号, 前端渲染成可点上标)。
    citations 引到但 web_search_tool_result 没收录的 url, 补进 sources 末尾。"""
    url2idx = {s["url"]: i + 1 for i, s in enumerate(sources)}
    parts = []
    for b in content:
        if b.get("type") != "text":
            continue
        parts.append(b.get("text") or "")
        cits = b.get("citations")
        if not isinstance(cits, list) or not cits:
            continue
        marks, here = [], set()
        for ci in cits:
            url = (ci.get("url") or "").strip()
            if not url or url in here:
                continue
            here.add(url)
            if url not in url2idx:
                sources.append({"title": (ci.get("title") or url).strip(), "url": url, "age": None})
                seen.add(url)
                url2idx[url] = len(sources)
            marks.append(f"⟦{url2idx[url]}⟧")
        if marks:
            parts.append("".join(marks))
    return _clean_answer("".join(parts))


def _image_blocks(images: list | None) -> list:
    """把前端传来的图片(data URL 或裸 base64)转成 Anthropic image content 块。最多 4 张。"""
    out = []
    for raw in (images or [])[:4]:
        if not isinstance(raw, str) or not raw.strip():
            continue
        s = raw.strip()
        media = "image/jpeg"
        if s.startswith("data:"):
            try:
                head, b64 = s.split(",", 1)
                media = head.split(";")[0][5:] or media   # data:image/png;base64
                s = b64
            except ValueError:
                continue
        out.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": s}})
    return out


def _seed_messages(question: str, history: list | None, images: list | None = None) -> list:
    """把前端传来的多轮历史(只含 role+text 的简化对话)接到当前问题前面, 让 agent 有上下文。
    带 images 时, 当前 user 消息构造成 [图片块... + 文本块] 多模态内容。"""
    msgs = []
    for h in (history or [])[-8:]:           # 最多带最近 8 条, 控制 token
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:4000]})
    blocks = _image_blocks(images)
    if blocks:
        msgs.append({"role": "user", "content": blocks + [{"type": "text", "text": question or "看看这张图"}]})
    else:
        msgs.append({"role": "user", "content": question})
    return msgs


# 上游过载时在 loop 层再重发几次。llm_client 内部已按指数退避等到 ~15s, 这里是第二道:
# 一次问答会跑十几轮, 第 12 轮撞上过载就把前面所有工具结果都扔了太亏 —— 同一个 messages
# 原样重发即可(工具结果都在 messages 里, 不用重跑取数)。
_OVERLOAD_ROUND_RETRIES = 3
_OVERLOAD_ROUND_WAIT_S = 20      # 第 n 次等 n×20 秒


# 工具轮里的正文多长算"答案的前半段"而不是进度旁白("我先查一下持仓"这类一句话)
_CARRY_MIN_CHARS = 200


def _round_text(content: list) -> str:
    return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def _join_answer(carried: list, final: str) -> str:
    """把工具轮里已写出的成段正文接到最终答案前面。

    模型有时先写一大段答案, 中途调 calc 验算, 再在最后一轮接着写后半段 —— 只取末轮文本
    就会丢掉前半段, 答案从半句话开始(2026-10-13 评测发现)。末轮已经把前文重写过一遍时
    (开头那段又出现在末轮里)不再拼, 免得重复。
    """
    parts = []
    for t in carried:
        head = t[:40]
        if head and head not in final:
            parts.append(t)
    parts.append(final)
    return "\n\n".join(p for p in parts if p)


async def _llm_round(messages: list):
    """跑一轮 LLM 调用, 上游过载则原样重发。

    async generator, 产出 (kind, value):
      ("wait", 事件dict)  等待重试中, 调用方把它推给前端(顺带当 SSE 心跳)
      ("resp", 响应dict)  成功
      ("error", 文案)     不可重试的错误, 或过载重试也用尽了
    """
    for attempt in range(_OVERLOAD_ROUND_RETRIES + 1):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _system(), _MODEL, _ROUND_MAX_TOKENS,
                await _active_tools(), thinking=_THINKING, effort=_EFFORT)
            yield ("resp", resp)
            return
        except _llm.LLMOverloaded as e:
            if attempt >= _OVERLOAD_ROUND_RETRIES:
                yield ("error", f"{e}。这是上游服务器忙, 不是你的配置问题 —— 过一两分钟重问一次即可")
                return
            wait = _OVERLOAD_ROUND_WAIT_S * (attempt + 1)
            yield ("wait", {"type": "step", "tool": "llm_retry", "label": "上游过载, 等待重试",
                            "arg": f"{wait}s (第 {attempt + 1}/{_OVERLOAD_ROUND_RETRIES} 次)"})
            await asyncio.sleep(wait)
        except Exception as e:
            yield ("error", str(e))
            return


async def ask_stock_stream(question: str, history: list | None = None, images: list | None = None):
    """流式版: 边跑边 yield 事件 (step/answer/done/error), 供 SSE 推给前端。
    每轮 LLM 调用之间 yield 工具步骤, 步骤实时出现; 末轮文本作为答案。
    history: 前端传的多轮对话历史 [{role, content}], 让 agent 有上下文(支持追问)。
    images: 当前问题附带的图片(data URL / base64), 走多模态。"""
    question = (question or "").strip()
    if not question and not images:
        yield {"type": "error", "error": "空问题"}
        return
    messages = _seed_messages(question, history, images)
    sources: list = []
    seen_urls: set = set()
    tool_outs: dict = {}
    used: list = []
    carried: list = []                       # 工具轮里已写出的成段正文, 见 _join_answer
    repaired = False
    for rnd in range(_MAX_ROUNDS):
        resp = None
        async for kind, val in _llm_round(messages):
            if kind == "wait":
                yield val
            elif kind == "error":
                yield {"type": "error", "error": val}
                return
            else:
                resp = val
        if resp is None:                    # 理论到不了(上面两支都 return 了), 兜一下防静默空转
            yield {"type": "error", "error": "LLM 无响应"}
            return
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        # 服务端联网搜索(web_search)已由 API 执行完, 这里只把"联网搜索"作为步骤推给前端
        for b in content:
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search":
                used.append("web_search")
                yield {"type": "step", "tool": "web_search", "label": "联网搜索",
                       "arg": (b.get("input") or {}).get("query", "")}
        # 抽取本轮 web_search 命中的网页来源, 实时推给前端(去重累加)
        fresh = _collect_sources(content, sources, seen_urls)
        if fresh:
            yield {"type": "sources", "sources": fresh}
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            before = len(sources)
            text = _join_answer(carried, _assemble_cited_text(content, sources, seen_urls))
            problems = _guard.inspect(text, used, tool_outs) if not repaired else []
            if problems:
                repaired = True
                yield {"type": "step", "tool": "self_check", "label": "回答自查",
                       "arg": f"{len(problems)} 处待修"}
                messages.append(_guard.repair_message(problems))
                continue
            if len(sources) > before:        # citations 引到的新 url 补推一条 sources 事件, 保证角标可解析
                yield {"type": "sources", "sources": sources[before:]}
            yield {"type": "answer", "text": text}
            yield {"type": "done"}
            return
        # 先把这一轮模型的简短思考文本(若有)推出去当“正在做什么”的旁白
        think = _round_text(content)
        if think:
            yield {"type": "thought", "text": think[:120]}
        if len(think) >= _CARRY_MIN_CHARS:
            carried.append(_assemble_cited_text(content, sources, seen_urls))
        used.extend(tu.get("name") or "" for tu in tus)
        for tu in tus:
            yield {"type": "step", "tool": tu.get("name"),
                   "label": _TOOL_CN.get(tu.get("name"), tu.get("name")),
                   "arg": (tu.get("input") or {}).get("query") or (tu.get("input") or {}).get("code") or ""}
        # 同一轮里的工具相互独立(各自的网络请求), 并发跑, 顺序保留, 单个失败不连累其他
        outs = await asyncio.gather(*[_run_tool(tu) for tu in tus])
        for tu, out in zip(tus, outs):
            tool_outs.setdefault(tu.get("name") or "", []).append(out)
        # 取数失败要**看得见**: step 事件是在工具跑之前发的, 之前失败的工具在界面上照样打绿勾,
        # 模型完全可以用记忆把那一段圆过去而用户看不出来。这类"工具挂了但答案看着完整"是最难
        # 发现的失败模式(ai-berkshire 的 issue #58 同一类问题: 后台 agent 联网被静默拦截,
        # 却仍输出一份看起来完整的伪研究)。所以逐个回填成败。
        for ev in _failed_steps(tus, outs):
            yield ev
        for out in outs:
            if isinstance(out, dict) and out.get("chart_url"):
                yield {"type": "chart", "url": out["chart_url"], "code": out.get("code", "")}
        results = [{"type": "tool_result", "tool_use_id": tu.get("id"),
                    "content": _result_content(out)}
                   for tu, out in zip(tus, outs)]
        messages.append({"role": "user", "content": results})
    yield {"type": "answer", "text": "（分析步数超限, 请换个问法或更具体）"}
    yield {"type": "done"}


async def ask_stock(question: str, history: list | None = None, images: list | None = None) -> dict:
    """跑 agent loop, 返回 {answer, tools_used, rounds}。"""
    question = (question or "").strip()
    if not question and not images:
        return {"answer": "", "error": "空问题"}
    messages = _seed_messages(question, history, images)
    tools_used: list[str] = []
    sources: list = []
    seen_urls: set = set()
    charts: list = []
    tool_outs: dict = {}          # 工具名 → [返回值], 给护栏对答案
    carried: list = []            # 工具轮里已写出的成段正文, 见 _join_answer
    repaired = False              # 护栏只补一轮, 防来回拉锯
    for rnd in range(_MAX_ROUNDS):
        resp = None
        async for kind, val in _llm_round(messages):     # "wait" 事件非流式版没处可推, 丢掉
            if kind == "error":
                return {"answer": "", "error": val, "tools_used": tools_used, "rounds": rnd}
            if kind == "resp":
                resp = val
        if resp is None:
            return {"answer": "", "error": "LLM 无响应", "tools_used": tools_used, "rounds": rnd}
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        # 服务端 web_search 也计入 tools_used(它是 server_tool_use, 不在 tus 里, 否则会被漏记成"没联网")
        tools_used.extend("web_search" for b in content
                          if b.get("type") == "server_tool_use" and b.get("name") == "web_search")
        _collect_sources(content, sources, seen_urls)
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = _join_answer(carried, _assemble_cited_text(content, sources, seen_urls))
            problems = _guard.inspect(text, tools_used, tool_outs) if not repaired else []
            if problems:
                # 只补一轮: 让模型只改这几点。不违规时这段完全不执行(常态零成本)。
                repaired = True
                messages.append(_guard.repair_message(problems))
                continue
            return {"answer": text, "tools_used": tools_used, "rounds": rnd + 1,
                    "sources": sources, "charts": charts,
                    "guard_repaired": repaired}
        tools_used.extend(tu.get("name", "") for tu in tus)
        if len(_round_text(content)) >= _CARRY_MIN_CHARS:
            carried.append(_assemble_cited_text(content, sources, seen_urls))
        # 同一轮里的工具并发跑(相互独立), 顺序保留, 单个失败不连累其他
        outs = await asyncio.gather(*[_run_tool(tu) for tu in tus])
        for tu, out in zip(tus, outs):
            tool_outs.setdefault(tu.get("name") or "", []).append(out)
        charts.extend(out["chart_url"] for out in outs
                      if isinstance(out, dict) and out.get("chart_url"))
        results = [{"type": "tool_result", "tool_use_id": tu.get("id"),
                    "content": _result_content(out)}
                   for tu, out in zip(tus, outs)]
        messages.append({"role": "user", "content": results})
    return {"answer": "（分析步数超限, 请换个问法或更具体）", "tools_used": tools_used,
            "rounds": _MAX_ROUNDS, "charts": charts}
