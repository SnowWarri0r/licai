"""期间复盘: 按年或按月汇总交易本身 —— 做了多少、花了多少、拿了多久、买点之后怎样。

范围: 个股(A 股 / 港股 / 美股, position_actions) + 场内 ETF/LOF(external_asset_actions)。
场外基金是申购赎回与定投, 不是盘中交易决策, 不纳入。金额一律折人民币(港美股按每笔成交日汇率,
没记的用当前汇率)。

四块:
  交易: 买入/卖出决策数(同一标的同一天同方向算一次)、买卖成交额、换手率
        换手率 = (买入额 + 卖出额) / 2 / 期间平均总资产; 平均总资产取期间内的每日资产快照,
        快照从开始记录那天起才有, 期间早于它时换手率为空并说明。
  费用: 期间内每笔成交的费用合计(手填的按手填, 其余与持仓页同一套估算), 占成交额与占已实现毛利的比例。
  持有期: 期间内每次卖出按先进先出配到买入批次, 按成本加权的持有天数分档, 每档的已实现毛利与赚钱占比。
        已实现毛利 = (卖出价 - 买入价) × 股数, 不扣费、不含分红, 与费用分开列。
  买点: 买点回看里买入日落在期间内的那部分(A 股个股 + 场内 ETF), 之后 5/20 个交易日的结果。
"""
from __future__ import annotations

import re
import statistics
from datetime import date

HOLD_BUCKETS = [(0, 1, "1 天内"), (2, 7, "2~7 天"), (8, 30, "8~30 天"), (31, 90, "31~90 天"),
                (91, 10 ** 6, "90 天以上")]

_PERIOD = re.compile(r"^\d{4}(-\d{2})?$")
ER_WAIT = 15          # 秒: 等买点回看的上限


def valid_period(p: str) -> bool:
    return bool(_PERIOD.match(p or ""))


def in_period(day: str, period: str) -> bool:
    return str(day or "")[:len(period)] == period


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


# ── 先进先出配对 ─────────────────────────────────────────

def fifo_sells(events: list[dict]) -> list[dict]:
    """一只标的的流水(已按日期排好) → 每次卖出(同一天合并)配到的买入批次。

    events: {date, side: B|S|SPLIT, price(人民币), shares, ratio(SPLIT 用)}; BONUS 记成 price=0 的 B。
    返回 [{date, shares, cost, proceeds, wdays}], wdays = 成本加权持有天数。卖出超过持仓的部分
    (账本缺早期买入)不配对, 也不计入。
    """
    lots: list[dict] = []
    out: dict[str, dict] = {}
    for e in events:
        side = e["side"]
        if side == "SPLIT":
            r = float(e.get("ratio") or 0)
            if r > 0:
                for lot in lots:
                    lot["shares"] *= r
                    lot["price"] /= r
            continue
        sh = float(e.get("shares") or 0)
        if sh <= 0:
            continue
        if side == "B":
            lots.append({"date": e["date"], "price": float(e.get("price") or 0), "shares": sh})
            continue
        rem = sh
        s = out.setdefault(e["date"], {"date": e["date"], "shares": 0.0, "cost": 0.0, "proceeds": 0.0,
                                       "_dw": 0.0})
        while rem > 1e-9 and lots:
            lot = lots[0]
            take = min(lot["shares"], rem)
            cost = take * lot["price"]
            s["shares"] += take
            s["cost"] += cost
            s["proceeds"] += take * float(e.get("price") or 0)
            s["_dw"] += max(_days(lot["date"], e["date"]), 0) * max(cost, take)  # 送股成本为 0 时按股数加权
            lot["shares"] -= take
            rem -= take
            if lot["shares"] <= 1e-9:
                lots.pop(0)
    res = []
    for s in out.values():
        if s["shares"] <= 0:
            continue
        wbase = s["cost"] if s["cost"] > 0 else s["shares"]
        res.append({"date": s["date"], "shares": s["shares"], "cost": s["cost"], "proceeds": s["proceeds"],
                    "wdays": s["_dw"] / wbase if wbase else 0.0})
    return res


def hold_bucket(days: float) -> str:
    d = int(round(days))
    for lo, hi, lab in HOLD_BUCKETS:
        if lo <= d <= hi:
            return lab
    return HOLD_BUCKETS[-1][2]


# ── 汇总 ────────────────────────────────────────────────

def _r(v, n=2):
    return None if v is None else round(v, n)


def summarize(period: str, trades: list[dict], snapshots: list[dict], entries: list[dict]) -> dict:
    """trades: [{code, name, market: A|HK|US|ETF, date, side: B|S|SPLIT, price(人民币), shares, ratio, fee(人民币)}]
    snapshots: [{snap_date, total_value}]; entries: 买点回看的 entries。"""
    in_p = [t for t in trades if t["side"] in ("B", "S") and in_period(t["date"], period)]

    # 交易
    by_mkt: dict[str, dict] = {}
    dec_b, dec_s = set(), set()
    for t in in_p:
        m = by_mkt.setdefault(t["market"], {"buy_amt": 0.0, "sell_amt": 0.0, "fee": 0.0, "n_buy": 0, "n_sell": 0})
        amt = float(t["price"] or 0) * float(t["shares"] or 0)
        key = (t["code"], t["date"])
        if t["side"] == "B":
            m["buy_amt"] += amt
            if key not in dec_b:
                dec_b.add(key)
                m["n_buy"] += 1
        else:
            m["sell_amt"] += amt
            if key not in dec_s:
                dec_s.add(key)
                m["n_sell"] += 1
        m["fee"] += float(t.get("fee") or 0)
    buy_amt = sum(m["buy_amt"] for m in by_mkt.values())
    sell_amt = sum(m["sell_amt"] for m in by_mkt.values())
    fee = sum(m["fee"] for m in by_mkt.values())

    snaps = [float(s["total_value"]) for s in snapshots
             if in_period(s["snap_date"], period) and float(s.get("total_value") or 0) > 0]
    avg_assets = statistics.mean(snaps) if snaps else None
    first_snap = min((s["snap_date"] for s in snapshots), default=None)
    partial = bool(avg_assets) and first_snap and first_snap > period     # 快照在期间中途才开始
    if partial:
        # 分子分母必须同一段时间: 只拿快照开始之后的成交去比快照期间的平均资产
        traded = sum(float(t["price"] or 0) * float(t["shares"] or 0) for t in in_p if t["date"] >= first_snap)
    else:
        traded = buy_amt + sell_amt
    turnover = (traded / 2 / avg_assets * 100) if avg_assets else None
    if avg_assets is None:
        t_note = "期间内没有每日资产快照(快照从 %s 开始记录), 算不出换手率" % (first_snap or "—")
    elif partial:
        t_note = f"资产快照从 {first_snap} 开始记录, 换手率只按这之后的成交与 {len(snaps)} 个快照日算"
    else:
        t_note = f"平均总资产按期间内 {len(snaps)} 个快照日算"

    # 持有期 + 已实现毛利(按标的分别做先进先出, 只统计期间内的卖出)
    by_code: dict[str, list] = {}
    for t in trades:
        by_code.setdefault(t["code"], []).append(t)
    sells = []
    for code, evs in by_code.items():
        evs = sorted(evs, key=lambda x: (x["date"], 0 if x["side"] in ("B", "SPLIT") else 1))
        for s in fifo_sells(evs):
            if in_period(s["date"], period):
                sells.append({**s, "code": code, "name": evs[0].get("name") or code,
                              "pnl": s["proceeds"] - s["cost"]})
    groups = []
    for lo, hi, lab in HOLD_BUCKETS:
        rs = [s for s in sells if hold_bucket(s["wdays"]) == lab]
        cost = sum(s["cost"] for s in rs)
        pnl = sum(s["pnl"] for s in rs)
        groups.append({"label": lab, "n": len(rs), "cost": _r(cost), "pnl": _r(pnl),
                       "ret_pct": _r(pnl / cost * 100) if cost > 0 else None,
                       "win": _r(sum(1 for s in rs if s["pnl"] > 0) / len(rs), 3) if rs else None})
    realized = sum(s["pnl"] for s in sells)
    days_all = sorted(s["wdays"] for s in sells)

    # 买点
    es = [e for e in entries if in_period(e.get("date"), period)]
    done = [e for e in es if e.get("r20_pct") is not None]
    exs = [e["ex20_pct"] for e in done if e.get("ex20_pct") is not None]
    entry = {"n": len(es), "n_done": len(done),
             "mean5_pct": _r(statistics.mean([e["r5_pct"] for e in es if e.get("r5_pct") is not None]))
             if any(e.get("r5_pct") is not None for e in es) else None,
             "mean20_pct": _r(statistics.mean([e["r20_pct"] for e in done])) if done else None,
             "median20_pct": _r(statistics.median([e["r20_pct"] for e in done])) if done else None,
             "win20": _r(sum(1 for e in done if e["r20_pct"] > 0) / len(done), 3) if done else None,
             "mean_ex20_pct": _r(statistics.mean(exs)) if exs else None}

    return {
        "period": period,
        "trading": {
            "n_buy": len(dec_b), "n_sell": len(dec_s), "buy_amt": _r(buy_amt), "sell_amt": _r(sell_amt),
            "avg_assets": _r(avg_assets), "turnover_pct": _r(turnover, 1), "turnover_note": t_note,
            "by_market": {k: {kk: (_r(vv) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                          for k, v in sorted(by_mkt.items())},
        },
        "fees": {
            "total": _r(fee),
            "pct_of_traded": _r(fee / (buy_amt + sell_amt) * 100, 3) if buy_amt + sell_amt > 0 else None,
            "realized_gross": _r(realized),
            "pct_of_realized": _r(fee / realized * 100, 1) if realized > 0 else None,
        },
        "holding": {"n_sells": len(sells), "groups": groups,
                    "median_days": _r(statistics.median(days_all), 1) if days_all else None},
        "entries": entry,
        "note": "范围: 个股(A 股/港股/美股)与场内 ETF, 场外基金不计。金额折人民币。同一标的同一天同方向的多笔成交算一次决策。"
                "已实现毛利按先进先出配对, 不扣费、不含分红; 费用单列。持有天数为自然日, 按成本加权。"
                "买点一栏只含 A 股个股与场内 ETF。纯回顾已发生的交易, 不构成买卖建议。",
    }


def periods_of(trades: list[dict]) -> dict:
    days = sorted({t["date"][:10] for t in trades if t["side"] in ("B", "S") and len(t.get("date") or "") >= 7})
    return {"years": sorted({d[:4] for d in days}, reverse=True),
            "months": sorted({d[:7] for d in days}, reverse=True)}


# ── 读库 ────────────────────────────────────────────────

async def load_trades() -> list[dict]:
    import asyncio
    from database import get_all_holdings, get_position_actions, list_external_actions, list_external_assets
    from services.market_data import _is_etf_lof, code_fx, split_stock_code
    from services.position_ledger import ACQUIRE, RELEASE, SPLIT, attach_auto_fees, split_ratio

    holdings = {h["stock_code"]: h for h in await get_all_holdings()}
    acts = await get_position_actions(None, limit=100000)
    by_code: dict[str, list] = {}
    for a in acts:
        by_code.setdefault(a.get("stock_code") or "", []).append(a)
    out: list[dict] = []
    resolve = None
    for code, rows in by_code.items():
        if not code:
            continue
        mkt = split_stock_code(code)[0]
        mkt = mkt if mkt in ("HK", "US") else "A"
        resolve = await attach_auto_fees(rows, code, (holdings.get(code) or {}).get("broker"), resolve)
        fx_now = None
        if mkt != "A" and any(not a.get("fx_rate") for a in rows):
            fx_now = await asyncio.to_thread(code_fx, code)
        name = (holdings.get(code) or {}).get("stock_name") or code
        for a in rows:
            t = (a.get("action_type") or "").upper()
            d = str(a.get("trade_date") or a.get("created_at") or "")[:10]
            if len(d) != 10:
                continue
            fx = float(a.get("fx_rate") or 0) or (fx_now or 1.0) if mkt != "A" else 1.0
            if t == SPLIT:
                out.append({"code": code, "name": name, "market": mkt, "date": d, "side": "SPLIT",
                            "ratio": split_ratio(a), "price": 0.0, "shares": 0.0, "fee": 0.0})
                continue
            if t not in ACQUIRE and t not in RELEASE:
                continue
            fee = a.get("fee") if a.get("fee") is not None else a.get("_auto_fee")
            out.append({"code": code, "name": name, "market": mkt, "date": d,
                        "side": "B" if t in ACQUIRE else "S",
                        "price": float(a.get("price") or 0) * fx, "shares": float(a.get("shares") or 0),
                        "fee": float(fee or 0) * fx})
    try:
        for asset in await list_external_assets(include_closed=True):   # 已清仓归档的 ETF 也要算进当年的交易
            code = str(asset.get("code") or "")
            if asset.get("asset_type") != "FUND" or not _is_etf_lof(code):
                continue
            for x in await list_external_actions(asset["id"]):
                if (x.get("status") or "confirmed") != "confirmed":
                    continue
                t = (x.get("action_type") or "").upper()
                d = str(x.get("trade_date") or "")[:10]
                sh = abs(float(x.get("shares") or 0))
                if len(d) != 10:
                    continue
                if t == "SPLIT":
                    out.append({"code": code, "name": asset.get("name") or code, "market": "ETF", "date": d,
                                "side": "SPLIT", "ratio": sh, "price": 0.0, "shares": 0.0, "fee": 0.0})
                    continue
                if t not in ("BUY", "ADD", "REDEEM") or sh <= 0:
                    continue
                px = float(x.get("unit_price") or 0) or (abs(float(x.get("amount") or 0)) / sh)
                out.append({"code": code, "name": asset.get("name") or code, "market": "ETF", "date": d,
                            "side": "S" if t == "REDEEM" else "B", "price": px, "shares": sh,
                            "fee": float(x.get("fee") or 0)})
    except Exception as e:  # noqa: BLE001
        print(f"[period-review] ETF 流水读取失败: {e}")
    return out


async def build(period: str = "") -> dict:
    from database import list_portfolio_snapshots
    trades = await load_trades()
    avail = periods_of(trades)
    if not avail["years"]:
        return {"empty": True, "note": "还没有个股或场内 ETF 的成交流水。", **avail}
    period = period if valid_period(period) else avail["years"][0]
    snaps = await list_portfolio_snapshots(limit=5000)
    # 买点回看首次要逐笔回放 K 线(约 1 分钟), 不让其余三块陪着等: 限时取, 没算完就先空着,
    # 计算本身照常在后台跑完(shield), 下次打开就有了
    import asyncio
    pending = False
    try:
        from services.entry_review import build as er_build
        er = await asyncio.wait_for(asyncio.shield(er_build()), timeout=ER_WAIT)
        entries = [] if er.get("empty") else er.get("entries") or []
    except asyncio.TimeoutError:
        entries, pending = [], True
    except Exception as e:  # noqa: BLE001
        print(f"[period-review] 买点回看不可用: {e}")
        entries = []
    out = summarize(period, trades, snaps, entries)
    out["entries"]["pending"] = pending
    return {"empty": False, **avail, **out}
