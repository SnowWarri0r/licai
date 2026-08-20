"""美股的行业/同行/公告 —— 补上原来只支持 A 股的三个工具。

为什么补这三个: 工具缺口台账(services/tool_gaps.py)第一轮就捞出五个工具对美股直接
报错, 而那两天连着翻车的恰好都是美股。这里按"源真的拿得到"来选:

  行业/概念  Yahoo search 的 quotes[0] 给 sector/industry, 顺带给 prevName(曾用名)
  同行      Yahoo recommendationsbysymbol 给相似标的 + 相似度分, 再套本地行情取涨跌
  公告      SEC EDGAR submissions —— 官方源, 免费无鉴权。实测 MRVL 2026-08-19 那份
            8-K 就是谷歌认股权证的披露, 比任何新闻都权威

拿不到的两个如实留空:
  股东/解禁  Yahoo quoteSummary(institutionOwnership/majorHoldersBreakdown)现在一律
            401 Invalid Crumb, 需要 cookie+crumb 才能调; SEC 的 13F/SC 13G 要逐份解
            表格, 不是一个工具的量级
  红线清单   ST/退市风险/商誉/股权质押是 A 股制度下的概念, 美股没有对应口径, 硬套会
            造出看着像结论的假信号
"""
from __future__ import annotations
import os
import re
import time

import requests as _rq

# SEC 的访问规范要求 UA 里带可联系到的邮箱。实测它的 WAF 会拦 UA 里出现 URL 的请求
# (放仓库地址一律 403), 带邮箱形态就放行。默认用占位邮箱 —— 源码是公开的, 不把私人
# 邮箱写进来; 想按 SEC 的要求留真实联系方式就设 SEC_USER_AGENT。
_SEC_UA = {"User-Agent": os.environ.get(
    "SEC_USER_AGENT", "licai-dashboard contact@example.com")}
_YH_UA = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 10

_cache: dict = {}


def _session():
    s = _rq.Session()
    s.trust_env = False
    return s


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < ttl:
        return hit[0]
    val = fn()
    _cache[key] = (val, time.time())
    return val


def _bare(code: str) -> str:
    """US.MRVL / mrvl → MRVL"""
    return (code or "").split(".")[-1].strip().upper()


# ── 行业 / 概念 ─────────────────────────────────────────

def us_profile(code: str) -> dict:
    """sector / industry / 全称 / 曾用名。"""
    sym = _bare(code)

    def fetch():
        r = _session().get("https://query1.finance.yahoo.com/v1/finance/search",
                           params={"q": sym, "quotesCount": 4, "newsCount": 0},
                           headers=_YH_UA, timeout=_TIMEOUT)
        for q in (r.json().get("quotes") or []):
            if (q.get("symbol") or "").upper() == sym:
                return q
        return {}

    q = _cached(f"prof_{sym}", 3600, fetch)
    if not q:
        return {"error": f"未在 Yahoo 找到 {sym}"}
    out = {
        "code": f"US.{sym}",
        "name": q.get("longname") or q.get("shortname") or sym,
        "交易所": q.get("exchDisp") or "",
        "板块": q.get("sectorDisp") or q.get("sector") or "",
        "行业": q.get("industryDisp") or q.get("industry") or "",
        "类型": q.get("typeDisp") or "",
    }
    if q.get("prevName"):
        # 更名是"凭记忆答"最容易翻车的地方, 拿到就明确写出来。
        # 不带 nameChangeDate: 实测 MRVL 返回的是 2026-08-20(当天), 而它实际是 2021 年
        # 由 Group Ltd. 改成 Inc. —— 这个字段的语义没核实清, 报出去就是编日期。
        out["曾用名"] = q["prevName"]
    return out


# ── 同行 ────────────────────────────────────────────────

def us_peers(code: str, limit: int = 6) -> list[dict]:
    """Yahoo 的相似标的列表(带相似度分)。不含行情, 由调用方套本地报价。"""
    sym = _bare(code)

    def fetch():
        r = _session().get(
            f"https://query1.finance.yahoo.com/v6/finance/recommendationsbysymbol/{sym}",
            headers=_YH_UA, timeout=_TIMEOUT)
        res = ((r.json().get("finance") or {}).get("result") or [{}])[0]
        return res.get("recommendedSymbols") or []

    rows = _cached(f"peers_{sym}", 3600, fetch)
    out = []
    for it in rows[:limit]:
        s = (it.get("symbol") or "").upper()
        if not s or s == sym:
            continue
        out.append({"code": f"US.{s}", "相似度": round(float(it.get("score") or 0), 3)})
    return out


# ── 公告 (SEC EDGAR) ────────────────────────────────────

# 常见表单的中文说明: 让模型不必猜 8-K / 13G 是什么
_FORM_CN = {
    "8-K": "重大事件临时报告",
    "10-Q": "季报",
    "10-K": "年报",
    "4": "内部人持股变动",
    "3": "内部人初始持股申报",
    "144": "受限股拟出售通知",
    "SC 13G": "被动大股东申报(≥5%)",
    "SCHEDULE 13G": "被动大股东申报(≥5%)",
    "SC 13D": "主动大股东申报(≥5%)",
    "SCHEDULE 13D": "主动大股东申报(≥5%)",
    "S-8": "员工股权计划登记",
    "S-3": "货架发行登记",
    "DEF 14A": "股东大会委托书",
    "424B5": "发行定价补充说明",
    "13F-HR": "机构季度持仓报告",
}

# 8-K 的 Item 编号才是它的语义载荷: 光看"8-K"只知道有大事, 看条目才知道是什么事。
# 实测 MRVL 2026-08-19 那份是 1.01+3.02 —— 签重大协议 + 发未登记股份, 正是给谷歌
# 发认股权证这件事, 比任何新闻标题都准。
_ITEM_CN = {
    "1.01": "签署重大协议", "1.02": "终止重大协议", "1.03": "破产或接管",
    "2.01": "完成资产收购或处置", "2.02": "业绩与经营结果", "2.03": "新增重大债务",
    "2.05": "重组或裁员成本", "2.06": "资产减值",
    "3.01": "退市或不符合上市规则", "3.02": "未登记股份发行", "3.03": "证券持有人权利变更",
    "4.01": "更换会计师", "4.02": "此前财报不可依赖",
    "5.01": "控制权变更", "5.02": "董事或高管变动", "5.03": "章程修订",
    "5.07": "股东投票结果", "7.01": "公司自愿披露", "8.01": "其他重大事项",
    "9.01": "财务报表与附件",
}


def _form_cn(form: str) -> str:
    f = (form or "").strip().upper()
    if f in _FORM_CN:
        return _FORM_CN[f]
    # "8-K/A" 是修订版。不能用 rstrip("/A") —— 那是按字符集剥, "8-K/A" 会变成 "8-"
    if f.endswith("/A"):
        base = _FORM_CN.get(f[:-2])
        return f"{base}-修订" if base else ""
    return ""


def _items_cn(items: str) -> str:
    out = []
    for code in re.split(r"[,\s]+", (items or "").strip()):
        if not code:
            continue
        out.append(f"{code} {_ITEM_CN[code]}" if code in _ITEM_CN else code)
    return ", ".join(out)


def _cik_of(sym: str) -> str | None:
    """ticker → 10 位 CIK。company_tickers.json 是 776KB 的静态映射, 缓存一天。"""
    def fetch():
        r = _session().get("https://www.sec.gov/files/company_tickers.json",
                           headers=_SEC_UA, timeout=20)
        m = {}
        for v in (r.json() or {}).values():
            t = (v.get("ticker") or "").upper()
            if t:
                m[t] = str(v.get("cik_str") or "").zfill(10)
        return m

    table = _cached("sec_tickers", 86400, fetch)
    return table.get(sym)


def us_filings(code: str, limit: int = 12) -> dict:
    """SEC 最近申报。8-K 才是"重大事件", 4/144 那类是内部人交易, 分开标注。"""
    sym = _bare(code)
    cik = _cik_of(sym)
    if not cik:
        return {"error": f"SEC 里没找到 {sym} 的 CIK(可能是 ADR 或未在美国注册)"}

    def fetch():
        r = _session().get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                           headers=_SEC_UA, timeout=20)
        return r.json() or {}

    sub = _cached(f"sec_sub_{cik}", 900, fetch)
    rec = ((sub.get("filings") or {}).get("recent") or {})
    forms = rec.get("form") or []
    dates = rec.get("filingDate") or []
    descs = rec.get("primaryDocDescription") or []
    accs = rec.get("accessionNumber") or []
    items = rec.get("items") or []

    rows = []
    for i in range(min(limit, len(forms))):
        form = forms[i]
        row = {
            "日期": dates[i] if i < len(dates) else "",
            "表单": form,
            "含义": _form_cn(form),
        }
        if i < len(items) and items[i]:
            row["条目"] = _items_cn(items[i])
        if i < len(descs) and descs[i] and descs[i] != form:
            row["说明"] = descs[i]
        if i < len(accs) and accs[i]:
            acc = accs[i].replace("-", "")
            row["链接"] = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
                          f"{accs[i]}-index.htm")
        rows.append(row)

    return {
        "code": f"US.{sym}",
        "name": sub.get("name") or sym,
        "SEC行业": sub.get("sicDescription") or "",
        "公告": rows,
        "latest_time": rows[0]["日期"] if rows else "",
        "note": "来自 SEC EDGAR。8-K=重大事件临时报告, 4/144=内部人交易, 13D/G=大股东申报",
    }
