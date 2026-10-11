"""港股/美股 F10: 估值(含币种折算)、公司简介、行业、同行、股东、机构持仓、业绩日历、港股公告。

数据源(都是公开免鉴权接口):
  东财 datacenter securities/api/data/v1/get   港美股 F10 的各张报表(RPT_HKF10_* / RPT_USF10_*)
  东财 np-anotice-stock(ann_type=H)            港股公告, 正文走 np-cnotice-stock
  Nasdaq api.nasdaq.com                       美股 13F 机构持仓、内部人交易、业绩日期与 EPS 意外

币种口径(实测, 是这个模块存在的主要原因):
  东财港股主要指标(RPT_HKF10_FN_MAININDICATOR)的 CURRENCY 字段恒为 HKD, 但数值其实是
  **统一折成人民币**的 —— 港股 F10 页面上写着「币种：人民币」。对过三家:
    港交所(港元报表) 2024 年归母净利 120.8 亿 = 130.5 亿港元 × 0.926
    汇丰(美元报表)   2024 年 EPS 8.9855   = 1.25 美元 × 7.19
    腾讯(人民币报表) 2024 年归母净利 1940.7 亿, 与年报人民币数一致
  所以「原始披露币种」要另取(三表摘要 RPT_CUSTOM_HKF10_APPFN_INCOME_SUMMARY 的 CURRENCY),
  算 PE/PB 时把人民币口径的 EPS/BPS 按即期汇率折成港元再除。
  美股主要指标(RPT_USF10_DATA_MAININDICATOR)按原币列示(CURRENCY: 美元/人民币/新台币),
  ADR 的 EPS/BPS 已是「每 ADS」口径, 与美股报价同一单位。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime

import requests as _rq
from concurrent.futures import ThreadPoolExecutor

_DC_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_EM_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"}
_NQ_HDR = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
           "Accept": "application/json, text/plain, */*",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}

_cache: dict = {}
_lock = threading.Lock()
_last_hit: dict = {}          # host → 上次请求时刻; 同一 host 两次请求至少隔 _MIN_GAP 秒
_MIN_GAP = 0.25

CUR_CODE = {"人民币": "CNY", "港元": "HKD", "港币": "HKD", "美元": "USD", "新台币": "TWD",
            "欧元": "EUR", "英镑": "GBP", "日元": "JPY", "新加坡元": "SGD", "澳元": "AUD",
            "加元": "CAD", "瑞士法郎": "CHF", "韩元": "KRW"}


def _cur(v) -> str:
    s = str(v or "").strip()
    return CUR_CODE.get(s, s.upper())


def _session():
    s = _rq.Session()
    s.trust_env = False
    return s


def _throttle(host: str):
    with _lock:
        gap = time.time() - _last_hit.get(host, 0)
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last_hit[host] = time.time()


def _get_json(url: str, params: dict | None, headers: dict, ttl: float, tries: int = 2):
    """带缓存 + 同 host 限速 + 一次重试的 GET。东财偶发限流(直接 RST), 失败返回 None 不抛。"""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < ttl:
        return hit[0]
    host = url.split("/")[2]
    for i in range(tries):
        _throttle(host)
        try:
            r = _session().get(url, params=params, headers=headers, timeout=12)
            if r.status_code == 200:
                j = r.json()
                _cache[key] = (j, time.time())
                return j
        except Exception:  # noqa: BLE001  限流/断连都按取不到处理
            pass
        time.sleep(0.6 * (i + 1))
    return hit[0] if hit else None          # 过期缓存也比空强


def dc(report: str, filt: str, columns: str = "ALL", sort: str = "", order: str = "-1",
       size: int = 20, source: str = "F10", ttl: float = 3600) -> list[dict]:
    """东财 datacenter 一张报表。取不到返回 []。"""
    p = {"reportName": report, "columns": columns, "filter": filt, "pageNumber": 1,
         "pageSize": size, "source": source, "client": "PC"}
    if sort:
        p["sortColumns"], p["sortTypes"] = sort, order
    j = _get_json(_DC_URL, p, _EM_HDR, ttl) or {}
    return ((j.get("result") or {}).get("data")) or []


def _par(*fns):
    """几个互不依赖的取数并发跑(各自限速/缓存照旧), 按入参顺序返回结果; 单个失败给 None。"""
    def safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        return list(ex.map(safe, fns))


def _num(v, nd: int | None = None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, nd) if nd is not None else f


def _yi(v, nd: int = 2):
    f = _num(v)
    return round(f / 1e8, nd) if f is not None else None


def _d10(v) -> str:
    return str(v or "")[:10]


def hk_secucode(sym: str) -> str:
    return f"{str(sym).split('.')[-1].zfill(5)}.HK"


def us_secucode(sym: str) -> str | None:
    """AAPL → AAPL.O / KO → KO.N(东财按交易所给后缀, 只能查表)。"""
    s = str(sym).split(".", 1)[-1].upper() if str(sym).upper().startswith("US.") else str(sym).upper()
    rows = dc("RPT_USF10_INFO_SECURITYINFO", f'(SECURITY_CODE="{s}")',
              "SECUCODE,SECURITY_CODE,LISTING_DATE,TRADE_MARKET,YEAR_SETTLE_DAY,SECURITY_TYPE",
              size=5, ttl=86400)
    return rows[0]["SECUCODE"] if rows and rows[0].get("SECUCODE") else None


# ── 币种折算 ────────────────────────────────────────────

def convert(amount, frm: str, to: str, implied: float | None = None) -> dict | None:
    """把 amount(frm 币种)折成 to 币种, 返回 {原币, 折算后, 汇率, 汇率来源}。
    frm/to 不在 CNY/HKD/USD 内时用 implied(数据源自带的隐含汇率)兜底, 都没有返回 None。"""
    from services.market_data import fx_cross_rate
    a = _num(amount)
    if a is None:
        return None
    fx = fx_cross_rate(frm, to)
    if fx:
        rate, src = fx["rate"], fx["source"]
    elif implied:
        rate, src = implied, "数据源隐含汇率"
    else:
        return None
    if frm == to:
        return {"原币": f"{round(a, 4)} {frm}", "换算汇率": "同币种, 无需换算", "折算后": round(a, 4),
                "汇率来源": "same", "_rate": 1.0}
    return {"原币": f"{round(a, 4)} {frm}", "换算汇率": f"1 {frm} = {round(rate, 4)} {to}",
            "折算后": round(a * rate, 4), "汇率来源": src, "_rate": rate}


def _ratio(price, per_share):
    p, e = _num(price), _num(per_share)
    if p is None or not e:
        return None
    return round(p / e, 2)


def hk_report_currency(sym: str) -> str | None:
    """港股原始披露币种(取三表摘要的 CURRENCY)。"""
    rows = dc("RPT_CUSTOM_HKF10_APPFN_INCOME_SUMMARY", f'(SECUCODE="{hk_secucode(sym)}")',
              "SECUCODE,REPORT_DATE,FISCAL_YEAR,CURRENCY,ACCOUNT_STANDARD,REPORT_TYPE",
              sort="REPORT_DATE", size=1, ttl=86400)
    lst = (rows[0].get("REPORT_LIST") if rows else None) or rows
    for r in lst or []:
        if r.get("CURRENCY"):
            return _cur(r["CURRENCY"])
    return None


def hk_main_rows(sym: str, size: int = 8) -> list[dict]:
    return dc("RPT_HKF10_FN_MAININDICATOR", f'(SECUCODE="{hk_secucode(sym)}")',
              sort="REPORT_DATE", size=size, ttl=3600)


def build_hk_valuation(rows: list[dict], report_cur: str | None, price, quote_cur: str = "HKD") -> dict:
    """港股估值块(纯计算, 便于单测)。rows=主要指标按报告期降序, 数值为人民币口径。"""
    if not rows:
        return {}
    r0 = rows[0]
    annual = next((r for r in rows if str(r.get("DATE_TYPE_CODE")) == "001"), None)
    out: dict = {"报价币种": quote_cur, "现价": _num(price),
                 "财报披露币种": report_cur or "未知",
                 "数据币种": "CNY",
                 "口径说明": "东财港股 F10 把各家财报统一折成人民币列示(原始披露币种见 财报披露币种), "
                         "PE/PB 用人民币口径的每股数按即期汇率折成报价币种后再除。"}
    eps_ttm = _num(r0.get("EPS_TTM"))
    cv = convert(eps_ttm, "CNY", quote_cur) if eps_ttm is not None else None
    if cv:
        out["EPS_TTM"] = {k: v for k, v in cv.items() if k != "_rate"}
        out["PE_TTM"] = _ratio(price, cv["折算后"])
        out["PE_TTM说明"] = f"现价 / 折算后 EPS_TTM; 截至 {_d10(r0.get('REPORT_DATE'))} 的近四季"
    if annual is not None:
        ca = convert(annual.get("BASIC_EPS"), "CNY", quote_cur)
        if ca:
            out["最近年报EPS"] = {"报告期": _d10(annual.get("REPORT_DATE")),
                               **{k: v for k, v in ca.items() if k != "_rate"}}
            out["PE_静态"] = _ratio(price, ca["折算后"])
    cb = convert(r0.get("BPS"), "CNY", quote_cur)
    if cb:
        out["BPS"] = {"报告期": _d10(r0.get("REPORT_DATE")), **{k: v for k, v in cb.items() if k != "_rate"}}
        out["PB"] = _ratio(price, cb["折算后"])
    p = _num(price)
    issued, hk_sh = _num(r0.get("ISSUED_COMMON_SHARES")), _num(r0.get("HK_COMMON_SHARES"))
    if p and issued:
        out[f"总市值亿{quote_cur}"] = round(p * issued / 1e8, 1)
        out["总股本亿股"] = round(issued / 1e8, 2)
        if hk_sh and abs(hk_sh - issued) / issued > 0.001:
            out[f"港股市值亿{quote_cur}"] = round(p * hk_sh / 1e8, 1)
            out["市值说明"] = "总市值=全部已发行股本×港股现价(含 A 股等非港股部分, 按港股价近似); 港股市值=H股/港股股本×现价"
    return out


def build_hk_financials(r0: dict) -> dict:
    if not r0:
        return {}
    return {"报告期": _d10(r0.get("REPORT_DATE")), "报告类型": r0.get("REPORT_TYPE"),
            "营业收入亿": _yi(r0.get("OPERATE_INCOME")), "营收同比增长%": _num(r0.get("OPERATE_INCOME_YOY"), 1),
            "归母净利润亿": _yi(r0.get("HOLDER_PROFIT")), "净利同比增长%": _num(r0.get("HOLDER_PROFIT_YOY"), 1),
            "毛利率%": _num(r0.get("GROSS_PROFIT_RATIO"), 1), "净利率%": _num(r0.get("NET_PROFIT_RATIO"), 1),
            "ROE%": _num(r0.get("ROE_YEARLY"), 2), "资产负债率%": _num(r0.get("DEBT_ASSET_RATIO"), 1),
            "基本EPS": _num(r0.get("BASIC_EPS"), 3), "金额币种": "CNY"}


def hk_fundamentals(sym: str, price) -> dict:
    rows = hk_main_rows(sym)
    if not rows:
        return {"error": "港股主要指标暂不可达(东财 F10 抖动或限流)"}
    rc = hk_report_currency(sym)
    return {"valuation": build_hk_valuation(rows, rc, price),
            "financials": build_hk_financials(rows[0]),
            "note": "金额单位亿人民币(东财统一折算口径); 同比为 YoY; ROE 为年化。"
                    "PE/PB 已按即期汇率把人民币每股数折成港元计算, 换算过程见 EPS_TTM/BPS 字段。"}


def build_us_valuation(d: dict, price, quote_cur: str = "USD") -> dict:
    """美股估值块(纯计算)。d=RPT_USF10_DATA_MAININDICATOR 一行, 按原币列示。"""
    if not d:
        return {}
    cur = _cur(d.get("CURRENCY_ABBR") or d.get("CURRENCY")) or "USD"
    eps = _num(d.get("RATIO_EPS_TTM"))
    implied = None
    if eps and _num(d.get("EPS_TTM_USD")):
        implied = _num(d.get("EPS_TTM_USD")) / eps       # 东财自带的原币→美元隐含汇率(新台币等兜底用)
    out: dict = {"报价币种": quote_cur, "现价": _num(price), "财报披露币种": cur,
                 "数据币种": cur, "最近报告期": _d10(d.get("REPORT_DATE"))}
    ce = convert(eps, cur, quote_cur, implied)
    if ce:
        out["EPS_TTM"] = {k: v for k, v in ce.items() if k != "_rate"}
        out["PE_TTM"] = _ratio(price, ce["折算后"])
        out["PE_TTM说明"] = "现价 / 折算后 EPS_TTM(近四季归母净利 / 总股本)"
    cb = convert(d.get("BVPS"), cur, quote_cur, implied)
    if cb:
        out["BPS"] = {k: v for k, v in cb.items() if k != "_rate"}
        out["PB"] = _ratio(price, cb["折算后"])
    p, sh = _num(price), _num(d.get("ISSUED_COMMON_SHARES"))
    if p and sh:
        out[f"总市值亿{quote_cur}"] = round(p * sh / 1e8, 1)
        out["总股本亿股"] = round(sh / 1e8, 2)
    if cur != quote_cur:
        out["口径说明"] = (f"财报以 {cur} 披露, 每股数按即期汇率折成 {quote_cur} 后再算 PE/PB; "
                       "ADR 的每股数已是每 ADS 口径。")
    return out


def us_fundamentals_valuation(sym: str, price) -> dict:
    secu = us_secucode(sym)
    if not secu:
        return {}
    rows = dc("RPT_USF10_DATA_MAININDICATOR", f'(SECUCODE="{secu}")', size=1, ttl=3600)
    return build_us_valuation(rows[0], price) if rows else {}


# ── 公司简介 / 行业 ─────────────────────────────────────

def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def hk_profile(sym: str) -> dict:
    secu = hk_secucode(sym)
    org = dc("RPT_HKF10_INFO_ORGPROFILE", f'(SECUCODE="{secu}")', size=1, ttl=86400)
    if not org:
        return {"error": "港股公司资料暂不可达(东财 F10 抖动或限流)"}
    o = org[0]
    sec = (dc("RPT_HKF10_INFO_SECURITYINFO", f'(SECUCODE="{secu}")', size=1, ttl=86400) or [{}])[0]
    ggt = [k for k, f in (("深港通", "GANGGUTONGBIAODISHEN"), ("沪港通", "GANGGUTONGBIAODIHU"))
           if sec.get(f) == "是"]
    return {"code": f"HK.{secu[:5]}", "name": o.get("SECURITY_NAME_ABBR"), "全称": o.get("ORG_NAME"),
            "英文名": o.get("ORG_EN_ABBR"), "industry": o.get("BELONG_INDUSTRY"),
            "行业分类": o.get("INDUSTRY_TYPE"), "profile": _clean(o.get("ORG_PROFILE")) or None,
            "主营业务": _clean(o.get("MAIN_BUSINESS")) or None,
            "上市日期": _d10(sec.get("LISTING_DATE") or o.get("LISTING_DATE")),
            "上市板块": o.get("BELONG_MARKET") or sec.get("BOARD"), "证券类型": sec.get("SECURITY_TYPE"),
            "港股通": "、".join(ggt) if ggt else "否",
            "总部/办公地址": o.get("ADDRESS"), "注册地": o.get("REG_PLACE"),
            "员工人数": o.get("EMP_NUM"), "董事长": o.get("CHAIRMAN"),
            "财报披露币种": hk_report_currency(sym), "财年结束日": o.get("YEAR_SETTLE_DAY"),
            "核数师": o.get("ACCOUNT_FIRM"), "官网": o.get("ORG_WEB"),
            "每手股数": o.get("TRADE_UNIT"),
            "note": "东财港股 F10 公司资料; 财报披露币种取自三表摘要(东财展示的财务数值统一折成人民币)。"}


def us_profile_f10(sym: str) -> dict:
    secu = us_secucode(sym)
    if not secu:
        return {"error": f"东财美股 F10 未收录 {sym}"}
    org = dc("RPT_USF10_INFO_ORGPROFILE", f'(SECUCODE="{secu}")', size=1, ttl=86400)
    if not org:
        return {"error": "美股公司资料暂不可达(东财 F10 抖动或限流)"}
    o = org[0]
    sec = (dc("RPT_USF10_INFO_SECURITYINFO", f'(SECUCODE="{secu}")', size=1, ttl=86400) or [{}])[0]
    fin = dc("RPT_USF10_FN_GMAININDICATOR", f'(SECUCODE="{secu}")', "SECUCODE,REPORT_DATE,CURRENCY,CURRENCY_ABBR,FINANCIAL_DATE",
             sort="REPORT_DATE", size=1, ttl=86400)
    out = {"code": f"US.{secu.split('.')[0]}", "name": o.get("SECURITY_NAME_ABBR"), "全称": o.get("ORG_NAME"),
           "英文名": o.get("ORG_EN_ABBR"), "industry": o.get("BELONG_INDUSTRY"),
           "profile": _clean(o.get("ORG_PROFILE")) or None,
           "上市日期": _d10(sec.get("LISTING_DATE")), "交易所": sec.get("TRADE_MARKET") or o.get("BELONG_MARKET"),
           "证券类型": sec.get("SECURITY_TYPE") or o.get("SECURITY_TYPE"),
           "总部/办公地址": o.get("ADDRESS"), "注册地": o.get("REG_PLACE"), "国家": o.get("COUNTRY"),
           "员工人数": o.get("EMP_NUM"), "董事长": o.get("CHAIRMAN"),
           "财报披露币种": _cur((fin[0].get("CURRENCY_ABBR") or fin[0].get("CURRENCY"))) if fin else None,
           "财年结束日": sec.get("YEAR_SETTLE_DAY") or o.get("YEAR_SETTLE_DAY"), "官网": o.get("ORG_WEB")}
    # 主营构成(按产品, 最新一期)
    ed = dc("RPT_USF10_INFO_PRODUCTSTRUCTURE_ENDATE", f'(SECUCODE="{secu}")', "SECUCODE,REPORT_DATE,NAME",
            sort="REPORT_DATE", size=1, source="SECURITIES", ttl=86400)
    if ed:
        rd = _d10(ed[0].get("REPORT_DATE"))
        ps = dc("RPT_USF10_INFO_PRODUCTSTRUCTURE", f"(SECUCODE=\"{secu}\")(REPORT_DATE='{rd}')(IS_TOTAL=\"0\")",
                size=50, source="SECURITIES", ttl=86400)
        comp = [{"项目": r.get("PRODUCT_NAME"), "营收占比%": round(_num(r.get("MBI_RATIO")) * 100, 1)}
                for r in sorted(ps, key=lambda x: -(_num(x.get("MBI_RATIO")) or 0)) if _num(r.get("MBI_RATIO")) is not None]
        if comp:
            out["main_business"] = comp[:8]
            out["主营构成期间"] = ed[0].get("NAME") or rd
    out["note"] = "东财美股 F10 公司资料; 主营构成按产品, 占比为该期营收占比。"
    return out


def hk_concepts(sym: str) -> dict:
    p = hk_profile(sym)
    if p.get("error"):
        return p
    levels = [x for x in str(p.get("行业分类") or "").split("-") if x]
    return {"code": p["code"], "name": p.get("name"), "所属行业": p.get("industry"),
            "行业分类层级": levels, "证券类型": p.get("证券类型"), "上市板块": p.get("上市板块"),
            "港股通": p.get("港股通"),
            "note": "所属行业=东财港股行业; 行业分类层级从大类到细类; 港股通=是否为南向资金可买标的(深港通/沪港通)。"}


# ── 同行 ────────────────────────────────────────────────

def _is_rmb_counter(code: str, name: str) -> bool:
    """双柜台的人民币柜台(8xxxx, 名字以 R 结尾) —— 同一家公司, 同行表里去掉。"""
    c = code.split(".")[0]
    return len(c) == 5 and c.startswith("8") and str(name or "").upper().endswith("R")


def hk_peers_raw(sym: str, top: int = 10) -> dict:
    secu = hk_secucode(sym)
    scale = dc("RPT_PCF10_INDUSTRY_SCALE", f'(SECUCODE="{secu}")', sort="HKTOTAL_CAP_RANK", order="1",
               size=40, ttl=3600)
    if not scale:
        return {"error": "港股同行表暂不可达(东财 F10 抖动或限流)"}
    val = dc("RPT_PCF10_INDUSTRY_HKCVALUE", f'(SECUCODE="{secu}")', size=500, ttl=3600)
    pe = {r.get("CORRE_SECUCODE"): r for r in val}
    rows, mine = [], None
    for r in scale:
        c, nm = r.get("CORRE_SECUCODE") or "", r.get("CORRE_SECURITY_NAME") or ""
        if not c.endswith(".HK") or _is_rmb_counter(c, nm):
            continue
        v = pe.get(c) or {}
        row = {"code": f"HK.{c[:5]}", "name": nm, "行业市值排名": r.get("HKTOTAL_CAP_RANK"),
               "_mcap_cny": _num(r.get("HKTOTAL_MARKET_CAP")),
               "PE_TTM": _num(v.get("PE_TTM"), 2), "PB": _num(v.get("PB_MQR"), 2)}
        if c == secu:
            mine = row
        rows.append(row)
    top_rows = rows[:top]
    if mine and mine not in top_rows:
        top_rows.append(mine)
    avg = pe.get("行业平均") or {}
    med = pe.get("行业中值") or {}
    return {"行业": scale[0].get("TYPE_NAME"), "行业成分数": len(val) or None,
            "市值截至": _d10(scale[0].get("MAXSTDREPORTDATE")),
            "行业PE_TTM平均": _num(avg.get("PE_TTM"), 2), "行业PE_TTM中值": _num(med.get("PE_TTM"), 2),
            "rows": top_rows}


# ── 股东 / 回购 ─────────────────────────────────────────

def hk_shareholders(sym: str) -> dict:
    secu = hk_secucode(sym)
    rows = dc("RPT_HKF10_EQUITYCHG_HOLDER", f'(SECUCODE="{secu}")', sort="REPORT_DATE,EQUITY_TYPE,TOTAL_SHARES",
              order="-1,-1,-1", size=60, ttl=21600)
    if not rows:
        return {"error": "港股股东权益披露暂不可达(东财 F10 抖动或限流)"}
    ld = rows[0].get("REPORT_DATE")
    latest = [r for r in rows if r.get("REPORT_DATE") == ld]

    def rec(r):
        return {"name": r.get("HOLDER_NAME"), "持股%": _num(r.get("TOTAL_SHARES_RATIO"), 4),
                "持股数": r.get("TOTAL_SHARES"), "身份": r.get("HOLD_IDENTITY"), "股份类别": r.get("SHARES_TYPE")}
    major = sorted([r for r in latest if r.get("EQUITY_TYPE") == "股东"],
                   key=lambda r: -(_num(r.get("TOTAL_SHARES_RATIO")) or 0))
    directors = sorted([r for r in latest if r.get("EQUITY_TYPE") == "董事"],
                       key=lambda r: -(_num(r.get("TOTAL_SHARES_RATIO")) or 0))
    chg = dc("RPT_HKF10_SH_EQUITYCHG", f'(SECUCODE="{secu}")', sort="EVENT_DATE,AFTER_HOLD_SHARES", order="-1,-1",
             size=10, ttl=21600)
    changes = [{"日期": _d10(c.get("EVENT_DATE")), "股东": c.get("HOLDER_NAME"),
                "仓位": {"L": "好仓", "S": "淡仓", "P": "可供借出"}.get(c.get("POSITION_TYPE"), c.get("POSITION_TYPE")),
                "变动股数": c.get("CHANGE_SHARES"), "变动后持股%": _num(c.get("AFTER_HOLD_RATIO"), 2),
                "均价": _num(c.get("EXCHANGE_AVERAGE_PRICE"), 3)} for c in chg]
    return {"code": f"HK.{secu[:5]}", "截至": _d10(ld),
            "主要股东": [rec(r) for r in major[:8]] or {"note": "该期无 ≥5% 主要股东申报"},
            "董事持股": [rec(r) for r in directors[:6]],
            "近期权益变动": changes,
            "回购": hk_buyback_summary(sym),
            "note": "港交所权益披露口径: 主要股东=持股≥5% 的申报人, 董事持股含受控法团权益; 好仓=多头, 淡仓=空头。"
                    "披露按触发事件申报, 截至日为该期汇总日期。"}


def summarize_buybacks(rows: list[dict], days: int = 90, today: date | None = None) -> dict:
    """近 days 天回购汇总(纯计算)。rows=RPT_HKF10_INFO_REPO 按日期降序。"""
    if not rows:
        return {"note": "近期无回购记录"}
    today = today or date.today()
    win = []
    for r in rows:
        try:
            d = datetime.strptime(_d10(r.get("REPO_DATE")), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days <= days:
            win.append((d, r))
    cur = rows[0].get("CURRENCY") or "HKD"
    out = {"最近回购日": _d10(rows[0].get("REPO_DATE"))}
    if win:
        amt = sum(_num(r.get("REPO_AMT")) or 0 for _, r in win)
        num = sum(_num(r.get("REPO_NUM")) or 0 for _, r in win)
        out.update({f"近{days}天回购天数": len(win), f"近{days}天回购股数": int(num),
                    f"近{days}天回购金额亿{cur}": round(amt / 1e8, 2),
                    "区间均价": round(amt / num, 3) if num else None,
                    "区间": f"{win[-1][0]} ~ {win[0][0]}"})
    else:
        out["note"] = f"近{days}天无回购"
    return out


def hk_buyback_summary(sym: str) -> dict:
    rows = dc("RPT_HKF10_INFO_REPO", f'(SECUCODE="{hk_secucode(sym)}")',
              "SECUCODE,REPO_DATE,REPO_NUM,REPO_AMT,HIGH_PRICE,LOW_PRICE,CURRENCY",
              sort="REPO_DATE", size=90, ttl=21600)
    return summarize_buybacks(rows)


# ── Nasdaq(美股机构/内部人/业绩) ────────────────────────

def _nq(path: str, params: dict | None = None, ttl: float = 21600) -> dict:
    j = _get_json(f"https://api.nasdaq.com/api/{path}", params, _NQ_HDR, ttl) or {}
    return j.get("data") or {}


def _nq_int(s):
    t = str(s or "").replace(",", "").replace("$", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    v = -v if neg else v
    return int(v) if v.is_integer() else v


def parse_nq_institutional(data: dict, top: int = 10) -> dict:
    """Nasdaq institutional-holdings 载荷 → 结构化(纯解析)。"""
    if not data:
        return {}
    summ = data.get("ownershipSummary") or {}
    out: dict = {
        "机构持股占比": (summ.get("SharesOutstandingPCT") or {}).get("value"),
        "总股本百万股": (summ.get("ShareoutstandingTotal") or {}).get("value"),
        "机构持仓总市值百万美元": (summ.get("TotalHoldingsValue") or {}).get("value"),
    }
    pos = {}
    for blk in ("activePositions", "newSoldOutPositions"):
        for r in ((data.get(blk) or {}).get("rows") or []):
            pos[r.get("positions")] = {"机构数": _nq_int(r.get("holders")), "股数": _nq_int(r.get("shares"))}
    cn = {"Increased Positions": "增持", "Decreased Positions": "减持", "Held Positions": "持平",
          "Total Institutional Shares": "合计", "New Positions": "新进", "Sold Out Positions": "清仓"}
    out["持仓变动"] = {cn.get(k, k): v for k, v in pos.items()}
    tbl = ((data.get("holdingsTransactions") or {}).get("table") or {}).get("rows") or []
    out["前十大机构"] = [{"机构": r.get("ownerName"), "申报日": r.get("date"),
                      "持股": _nq_int(r.get("sharesHeld")), "变动股数": _nq_int(r.get("sharesChange")),
                      "变动%": r.get("sharesChangePCT"),
                      "市值千美元": _nq_int(r.get("marketValue"))} for r in tbl[:top]]
    return out


def parse_nq_insider(data: dict) -> dict:
    if not data:
        return {}
    def tbl(key):
        rows = ((data.get(key) or {}).get("rows")) or []
        return {r.get("insiderTrade"): {"近3月": r.get("months3"), "近12月": r.get("months12")} for r in rows}
    trades = (((data.get("transactionTable") or {}).get("table")) or {}).get("rows") or []
    return {"笔数": tbl("numberOfTrades"), "股数": tbl("numberOfSharesTraded"),
            "最近交易": [{"人": t.get("insider"), "职务": t.get("relation"), "日期": t.get("lastDate"),
                       "类型": t.get("transactionType"), "股数": t.get("sharesTraded"),
                       "价格": t.get("lastPrice"), "交易后持股": t.get("sharesHeld")} for t in trades[:6]]}


def us_institutional(sym: str) -> dict:
    s = sym.split(".")[-1].upper()
    d = _nq(f"company/{s}/institutional-holdings",
            {"limit": 10, "type": "TOTAL", "sortColumn": "marketValue", "sortOrder": "DESC"})
    return parse_nq_institutional(d)


def us_insider(sym: str) -> dict:
    s = sym.split(".")[-1].upper()
    return parse_nq_insider(_nq(f"company/{s}/insider-trades", {"limit": 10, "type": "ALL"}))


def em_us_orghold_trend(sym: str, n: int = 4) -> list[dict]:
    """东财 13F 季度汇总: 机构家数/持股占比/新进·增持·减持家数。13F 申报截止为季末后 45 天,
    当季还在陆续申报时家数会远少于上季, 这种未齐的季度标出来。"""
    secu = us_secucode(sym)
    if not secu:
        return []
    rows = dc("RPT_PCUSF10_STOCK_ORGHOLD", f'(SECUCODE="{secu}")', sort="REPORT_DATE", size=n + 1,
              source="SECURITIES", ttl=21600)
    out = []
    for i, r in enumerate(rows[:n + 1]):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        partial = bool(nxt and (_num(r.get("SUM")) or 0) < 0.6 * (_num(nxt.get("SUM")) or 0))
        out.append({"季度": _d10(r.get("REPORT_DATE")), "机构家数": r.get("SUM"),
                    "持股占比%": _num(r.get("HOLD_RATIO_SUM"), 2), "新进": r.get("XJNUM"),
                    "增持": r.get("ZCNUM"), "减持": r.get("JCNUM"),
                    **({"申报未齐": True} if partial else {})})
    return out[:n]


def us_shareholders(sym: str) -> dict:
    inst, ins = _par(lambda: us_institutional(sym), lambda: us_insider(sym))
    inst, ins = inst or {}, ins or {}
    if not inst and not ins:
        return {"error": "美股股东数据暂不可达(Nasdaq 接口抖动)"}
    return {"code": f"US.{sym.split('.')[-1].upper()}",
            "机构持股占比": inst.get("机构持股占比"), "前十大机构": inst.get("前十大机构") or [],
            "内部人交易": ins or {"note": "内部人交易数据暂不可达"},
            "note": "机构持仓来自 13F 申报(季度, 季末后 45 天内陆续申报, 申报日见每行); "
                    "内部人交易来自 SEC Form 4, Automatic Sell 多为预设 10b5-1 计划下的卖出。"}


def us_inst_position(sym: str) -> dict:
    inst, trend = _par(lambda: us_institutional(sym), lambda: em_us_orghold_trend(sym))
    inst, trend = inst or {}, trend or []
    if not inst and not trend:
        return {"error": "美股机构持仓暂不可达(Nasdaq/东财接口抖动)"}
    return {"code": f"US.{sym.split('.')[-1].upper()}", "维度": "个股 13F 机构持仓",
            "机构持股占比": inst.get("机构持股占比"), "持仓变动": inst.get("持仓变动"),
            "前十大机构": inst.get("前十大机构"), "季度趋势": trend,
            "note": "Nasdaq 汇总的最新 13F 口径: 持仓变动=相对上季增持/减持/新进/清仓的机构家数与股数; "
                    "季度趋势取东财 13F 季度汇总, 标 申报未齐 的季度还在申报期内、家数偏少。"}


def parse_nq_earnings_date(data: dict) -> dict:
    txt = _clean((data or {}).get("reportText"))
    ann = _clean((data or {}).get("announcement"))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", txt)
    d = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None
    status = ("预计(按历史规律估算)" if "estimated" in txt
              else ("已排期(公司公告口径)" if "expected" in txt else ""))
    timing = "盘前" if "before market open" in txt else ("盘后" if "after market close" in txt else "")
    return {k: v for k, v in {"下次业绩日": d, "日期性质": status, "时段": timing,
                              "说明": ann or None, "原文": txt or None}.items() if v}


def parse_nq_surprise(data: dict) -> list[dict]:
    rows = (((data or {}).get("earningsSurpriseTable") or {}).get("rows")) or []
    return [{"财季": r.get("fiscalQtrEnd"), "公布日": r.get("dateReported"), "EPS": r.get("eps"),
             "一致预期": r.get("consensusForecast"), "超预期%": r.get("percentageSurprise")} for r in rows]


def us_earnings(sym: str) -> dict:
    s = sym.split(".")[-1].upper()
    def em_rows():
        secu = us_secucode(s)
        return dc("RPT_USF10_FN_GMAININDICATOR", f'(SECUCODE="{secu}")', sort="REPORT_DATE",
                  size=4, ttl=21600) if secu else []
    nd, sd, rows = _par(lambda: _nq(f"analyst/{s}/earnings-date", ttl=21600),
                        lambda: _nq(f"company/{s}/earnings-surprise", ttl=21600), em_rows)
    nxt, hist, rows = parse_nq_earnings_date(nd or {}), parse_nq_surprise(sd or {}), rows or []
    last: dict = {}
    if rows:
        q = next((r for r in rows if r.get("DATE_TYPE") == "单季报"), None) or (rows[0] if rows else None)
        if q:
            last = {"报告期": _d10(q.get("REPORT_DATE")), "口径": q.get("REPORT_DATA_TYPE") or q.get("DATE_TYPE"),
                    "公告日": _d10(q.get("NOTICE_DATE")), "币种": _cur(q.get("CURRENCY_ABBR") or q.get("CURRENCY")),
                    "营收亿": _yi(q.get("OPERATE_INCOME")), "营收同比%": _num(q.get("OPERATE_INCOME_YOY"), 1),
                    "归母净利亿": _yi(q.get("PARENT_HOLDER_NETPROFIT")),
                    "净利同比%": _num(q.get("PARENT_HOLDER_NETPROFIT_YOY"), 1),
                    "基本EPS": _num(q.get("BASIC_EPS"), 3), "毛利率%": _num(q.get("GROSS_PROFIT_RATIO"), 1)}
    if not nxt and not hist and not last:
        return {"error": "美股业绩日历暂不可达(Nasdaq/东财接口抖动)"}
    return {"code": f"US.{s}", "下次业绩": nxt or {"note": "未取到下次业绩日"},
            "近四季EPS与预期": hist, "最近一季财报": last,
            "note": "下次业绩日来自 Nasdaq(Zacks): 日期性质=预计 表示按历史规律估算, 公司正式公布后会更新; "
                    "EPS 为调整后口径, 与 最近一季财报 的 GAAP 基本 EPS 可能不同。"}


# ── 港股公告 / 业绩日历 ─────────────────────────────────

_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"


def hk_ann_rows(sym: str, pages: int = 1, size: int = 50) -> list[dict]:
    code = str(sym).split(".")[-1].zfill(5)
    out = []
    for p in range(1, pages + 1):
        j = _get_json(_ANN_URL, {"sr": "-1", "page_size": str(size), "page_index": str(p), "ann_type": "H",
                                 "client_source": "web", "stock_list": code},
                      {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}, 1800) or {}
        rows = (j.get("data") or {}).get("list") or []
        out += rows
        if len(rows) < size:
            break
    return out


def ann_link(code: str, art: str) -> str:
    return f"https://data.eastmoney.com/notices/detail/{code}/{art}.html"


_HK_ANN_KEY = ("業績", "股息", "回購", "購回", "收購", "出售", "配售", "發行", "供股", "合併", "私有化",
               "主要交易", "關連交易", "盈利警告", "盈利預告", "董事會召開", "停牌", "復牌", "清盤", "除牌")


def build_hk_announcements(rows: list[dict], code: str, limit: int = 15) -> dict:
    """港股公告列表(纯整理): 每日回购的「翌日披露报表」合并成一条, 其余逐条列出。"""
    out, daily = [], []
    for a in rows:
        title = a.get("title") or ""
        types = [c.get("column_name") for c in (a.get("columns") or []) if c.get("column_name")]
        d = _d10(a.get("notice_date"))
        if title.startswith("翌日披露报表") or title.startswith("翌日披露報表"):
            daily.append((d, types))
            continue
        if len(out) < limit:
            key = any(k in t for t in types for k in _HK_ANN_KEY) or any(k in title for k in ("业绩", "回购", "股息", "收购", "配售"))
            out.append({"date": d, "title": title, "类型": types, "key": key,
                        "链接": ann_link(code, a.get("art_code") or "")})
    res: dict = {"code": f"HK.{code}", "announcements": out}
    if daily:
        buy = sum(1 for _, t in daily if any("購回" in x or "回購" in x for x in t))
        res["翌日披露报表"] = {"条数": len(daily), "其中股份购回": buy, "最近": daily[0][0], "最早": daily[-1][0]}
    res["note"] = ("港交所披露易公告(东财转载, 标题为简体译名); 翌日披露报表=当日股份变动(多为回购)的例行申报, "
                   "已合并计数; key=true 是业绩/股息/回购/并购/配售/关连交易等事件型公告。")
    return res


def hk_announcements(sym: str) -> dict:
    code = str(sym).split(".")[-1].zfill(5)
    rows = hk_ann_rows(code, pages=1, size=60)
    if not rows:
        return {"error": "港股公告源暂不可达(东财限流或抖动)"}
    return build_hk_announcements(rows, code)


def parse_board_meeting(text: str) -> dict:
    """从「董事会会议召开日期」公告正文里抠会议日期与审议的期间(纯解析)。"""
    t = re.sub(r"\s+", "", text or "")
    m = re.search(r"[將将]於(\d{4})年(\d{1,2})月(\d{1,2})日", t) or \
        re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日[（(]?星期.[)）]?舉行董事會", t)
    out = {}
    if m:
        out["会议日期"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    p = re.search(r"截至(\d{4}年\d{1,2}月\d{1,2}日)止([^的，。,]{0,24})", t)
    if p:
        out["审议期间"] = f"截至{p.group(1)}止{p.group(2)}"
    pub = re.search(r"[將将]於(\d{4})年(\d{1,2})月(\d{1,2})日由董事會", t)
    if pub:
        out["业绩公告日"] = f"{pub.group(1)}-{int(pub.group(2)):02d}-{int(pub.group(3)):02d}"
    return out


def _ann_text(art: str) -> str:
    j = _get_json("https://np-cnotice-stock.eastmoney.com/api/content/ann",
                  {"art_code": art, "client_source": "web", "page_index": 1},
                  {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}, 86400) or {}
    return (j.get("data") or {}).get("notice_content") or ""


def hk_earnings(sym: str, today: date | None = None) -> dict:
    code = str(sym).split(".")[-1].zfill(5)
    today = today or date.today()
    rows = hk_ann_rows(code, pages=3, size=100)
    board = next((a for a in rows if any("董事會召開" in (c.get("column_name") or "") for c in (a.get("columns") or []))
                  or "董事会会议召开日期" in (a.get("title") or "")), None)
    results = [a for a in rows if any("業績" in (c.get("column_name") or "") for c in (a.get("columns") or []))]
    nxt: dict = {}
    if board:
        info = parse_board_meeting(_ann_text(board.get("art_code") or ""))
        md = info.get("会议日期")
        last_res = _d10(results[0].get("notice_date")) if results else ""
        if md and md >= today.isoformat():
            nxt = {"下次业绩董事会": md, **({"业绩公告日": info["业绩公告日"]} if info.get("业绩公告日") else {}),
                   "审议期间": info.get("审议期间"), "通告日": _d10(board.get("notice_date")),
                   "链接": ann_link(code, board.get("art_code") or "")}
        elif md:
            nxt = {"note": f"最近一次业绩董事会 {md} 已过(业绩公告 {last_res or '见下'}), 下一次的董事会通告尚未发出"}
    if not nxt:
        nxt = {"note": "近期公告里没有待开的业绩董事会通告(港交所规定中期业绩期末后 2 个月内、年度业绩 3 个月内公布)"}
    last: dict = {}
    if results:
        a = results[0]
        last = {"公告日": _d10(a.get("notice_date")), "标题": a.get("title"),
                "链接": ann_link(code, a.get("art_code") or "")}
    mrows = hk_main_rows(code, size=2)
    if mrows:
        f = build_hk_financials(mrows[0])
        last["财务摘要"] = f
    if not rows and not mrows:
        return {"error": "港股业绩日历暂不可达(东财限流或抖动)"}
    return {"code": f"HK.{code}", "下次业绩": nxt, "最近业绩": last or {"note": "未取到最近业绩公告"},
            "note": "下次业绩日取自公司「董事会会议召开日期」公告正文; 财务摘要为东财人民币折算口径, 同比为 YoY。"}
