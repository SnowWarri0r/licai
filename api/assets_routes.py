"""External assets (funds / crypto / bots) REST endpoints."""
from __future__ import annotations
import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_external_assets, add_external_asset, update_external_asset, delete_external_asset,
    get_external_asset, reassign_external_actions,
    list_external_actions, add_external_action, delete_external_action,
)
from services.external_assets import (
    get_fund_quote, get_crypto_quote, search_fund_by_name,
)
from services.external_ledger import compute_external_state
from services import okx_client

router = APIRouter(prefix="/api/assets", tags=["external-assets"])


from services.external_assets import _is_onchain_etf


def _is_etf_code(code: str) -> bool:
    """场内 ETF/LOF 判定 — 跟报价层 (_is_onchain_etf) 用同一套规则, 避免一处当场内
    报价、另一处当场外走 T+1 pending 的分裂。深市 1xxxxx / 沪市 5xxxxx 都算场内。"""
    return _is_onchain_etf(code)


class DcaInlineCreate(BaseModel):
    """随资产一并创建的定投计划 (可选). 字段语义对齐 DCACreate."""
    mode: str = "amount"
    value: float
    frequency: str = "monthly"
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    note: Optional[str] = ""


class AssetCreate(BaseModel):
    asset_type: str            # FUND / CRYPTO / BOT / WEALTH
    code: str
    name: str
    platform: Optional[str] = ""
    cost_amount: float
    shares: Optional[float] = None
    manual_value: Optional[float] = None
    note: Optional[str] = ""
    okx_algo_id: Optional[str] = None
    okx_bot_type: Optional[str] = None
    annual_yield_rate: Optional[float] = None  # WEALTH 年化收益率, e.g. 0.025 = 2.5%
    start_date: Optional[str] = None           # 起投日 YYYY-MM-DD
    trade_time: Optional[str] = None           # 成交时刻 HH:MM (可选, 供分时图打点)
    pending_amount: Optional[float] = None     # FUND/CRYPTO 待确认金额 (份额未结算)
    bot_budget_override_usdt: Optional[float] = None   # OKX 马丁实际总预算 (USDT), 覆盖算法反推
    purchase_fee_rate: Optional[float] = None   # FUND 申购费率 (小数, 0.0015=0.15%); C 类填 0
    fee: Optional[float] = None                 # 建仓手续费 ¥ (含在 cost_amount 内, 单存便于流水展示)
    broker: Optional[str] = None               # 托管券商/平台, e.g. "招商证券"
    dca: Optional[DcaInlineCreate] = None       # FUND/CRYPTO 同时建定投计划 (可选)


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    cost_amount: Optional[float] = None
    shares: Optional[float] = None
    manual_value: Optional[float] = None
    note: Optional[str] = None
    okx_algo_id: Optional[str] = None
    okx_bot_type: Optional[str] = None
    annual_yield_rate: Optional[float] = None
    start_date: Optional[str] = None
    pending_amount: Optional[float] = None
    bot_budget_override_usdt: Optional[float] = None
    purchase_fee_rate: Optional[float] = None
    broker: Optional[str] = None


class OkxCredentials(BaseModel):
    api_key: str
    secret_key: str
    passphrase: str


class LotAdd(BaseModel):
    """Add a subsequent purchase to an existing asset.

    For FUND/CRYPTO: provide `principal` (本金) plus either `shares` (新增份额)
    or `unit_price` (单价/NAV — backend computes shares = principal / unit_price).

    For WEALTH (理财): provide `principal` plus optional `lot_start_date`
    (this lot's 起投日 — backend computes the new effective start_date as a
    principal-weighted average) and optional `lot_yield_rate` (this lot's
    annualized yield — backend blends with existing).
    """
    principal: float
    shares: Optional[float] = None
    unit_price: Optional[float] = None
    fee: Optional[float] = None             # 手续费 ¥ (含在 principal 内, 单存便于流水展示/编辑)
    lot_start_date: Optional[str] = None    # WEALTH 加投起投日 (default: today)
    lot_yield_rate: Optional[float] = None  # WEALTH 加投年化 (default: keep existing)
    trade_time: Optional[str] = None        # 成交时刻 HH:MM (可选, 供分时图打点)


async def _enrich(asset: dict) -> dict:
    """Add quote, current_value, pnl for one asset.

    cost_amount 和 shares 会从 ledger 重算覆盖 (BOT 跳过 ledger, 走 OKX 同步).
    realized_pnl (累计实现盈亏, 含 INTEREST/DIVIDEND) 透出.
    """
    out = dict(asset)
    t = asset["asset_type"]
    quote = None
    current_value = asset.get("manual_value")

    # Ledger overlay (BOT 不走 ledger)
    realized_pnl = 0.0
    pending_count = 0
    total_released = 0.0
    if t != "BOT":
        try:
            actions = await list_external_actions(asset["id"])
            if actions:
                pending_count = sum(1 for a in actions if (a.get("status") or "confirmed") == "pending")
                # 派生 pending_amount: 实时从 pending 状态的申购流水累加, 避免 DB 字段 stale.
                # (DCA fire / confirm / delete 各处不必再维护一致, 这是 single source of truth.)
                if t in ("FUND", "CRYPTO"):
                    pending_total = sum(
                        float(a.get("amount") or 0) for a in actions
                        if (a.get("status") or "confirmed") == "pending"
                        and (a.get("action_type") or "").upper() in ("BUY", "ADD")
                    )
                    out["pending_amount"] = round(pending_total, 4)
                ledger_state = compute_external_state(actions, t)
                if ledger_state["cost_amount"] >= 0:
                    out["cost_amount"] = ledger_state["cost_amount"]
                if t in ("FUND", "CRYPTO"):
                    out["shares"] = ledger_state["shares"]
                realized_pnl = ledger_state["realized_pnl"]
                total_released = float(ledger_state.get("total_release_proceeds") or 0)
        except Exception as e:
            print(f"[assets] ledger enrich failed for asset#{asset.get('id')}: {e}")
    out["realized_pnl"] = round(realized_pnl, 2)
    out["pending_actions_count"] = pending_count

    if t == "FUND":
        quote = await get_fund_quote(asset["code"])
        # current_value 含 pending (资产总额视角: 钱已经投进去了),
        # 但下面算 pnl 时会减去 pending (浮动只看已确认 lot vs 确认成本).
        pending = float(out.get("pending_amount") or 0)
        if asset.get("manual_value") is not None:
            current_value = float(asset["manual_value"])  # 锁定的总市值（已含 pending）
        elif quote and asset.get("shares"):
            # 优先用官方公布净值 (nav) 而非盘中估值 (est_nav)，跟支付宝/天天基金 App
            # "持有金额" 计算口径一致；尤其 QDII 隔夜市场盘中估算偏差大。
            # 场内 ETF 的 nav 已经是实时市价（onchain branch 设置），同样优先。
            nav = quote.get("nav") or quote.get("est_nav") or 0
            current_value = round(nav * float(asset["shares"]) + pending, 2)
        elif pending > 0:
            current_value = round(float(asset.get("cost_amount") or 0) + pending, 2)
        # 附加代理标的行情 (用底层市场实时数据预判基金当日走势)。
        # 仅对场外基金 (NAV 是 T+1, 盘中看不到今日) 才需要代理推测;
        # 场内 ETF 本身有二级市场实时成交价 (quote.change_pct 即今日真实涨跌), 无需代理。
        if quote is not None and not _is_onchain_etf(asset["code"]):
            try:
                from services.fund_proxy import get_fund_proxy
                proxy = await get_fund_proxy(asset["code"])
                if proxy:
                    quote["proxy_change_pct"] = proxy["weighted_change_pct"]
                    quote["proxy_label"] = proxy["label"]
                    quote["proxy_details"] = proxy["proxies"]
            except Exception as e:
                print(f"[fund-proxy] {asset['code']} failed: {e}")
        # 今日涨跌口径: 场外基金净值是 T+1, change_pct 常是 1-2 天前的旧净值,
        # 拿来当"今日"会把昨天的涨幅冒充成今天。折算规则:
        #   - nav_date 为空 (场内 ETF 实时市价) 或 == 今天 → change_pct 就是今天
        #   - 净值滞后 + 有底层 proxy → 用 proxy (底层标的实时估今天)
        #   - 净值滞后 + 无 proxy → None (不计入今日浮动, 不冒充)
        if quote is not None:
            from datetime import date as _d
            nav_date = quote.get("nav_date") or ""
            if not nav_date or nav_date == _d.today().isoformat():
                quote["today_change_pct"] = quote.get("change_pct")
            elif quote.get("proxy_change_pct") is not None:
                quote["today_change_pct"] = quote.get("proxy_change_pct")
            else:
                quote["today_change_pct"] = None
    elif t == "CRYPTO":
        quote = await get_crypto_quote(asset["code"])
        pending = float(out.get("pending_amount") or 0)
        if asset.get("manual_value") is not None:
            current_value = float(asset["manual_value"])
        elif quote and asset.get("shares"):
            current_value = round(quote["price_cny"] * float(asset["shares"]) + pending, 2)
        elif pending > 0:
            current_value = round(float(asset.get("cost_amount") or 0) + pending, 2)
    elif t == "CASH":
        # 现金：cost = balance（用户简化模型）。无法算真实累计利息，但若用户填了
        # 7日年化，可以按当前余额给出估算月/年息流，比僵死的 +0.00 有意义。
        balance = float(asset.get("manual_value") or asset.get("cost_amount") or 0)
        current_value = round(balance, 2)
        rate = asset.get("annual_yield_rate")
        quote = {"annual_yield_rate": float(rate) if rate is not None else None}
        if rate is not None and balance > 0:
            r = float(rate)
            quote["daily_interest_est"] = round(balance * r / 365, 4)
            quote["monthly_interest_est"] = round(balance * r / 12, 2)
            quote["yearly_interest_est"] = round(balance * r, 2)
    elif t == "WEALTH":
        # 银行理财双向估算:
        #   有年化: current = principal × (1 + rate × days / 365)
        #   有手动市值: current = manual; 反推 implied_rate = (mv/principal - 1) × 365 / days
        from datetime import datetime as _dt, date as _date
        principal = float(asset.get("cost_amount") or 0)
        sd_str = asset.get("start_date") or str(asset.get("created_at", ""))[:10]
        try:
            start_d = _dt.strptime(sd_str, "%Y-%m-%d").date()
        except Exception:
            start_d = _date.today()
        days_held = max(0, (_date.today() - start_d).days)

        rate = asset.get("annual_yield_rate")
        manual = asset.get("manual_value")
        implied_rate = None
        source = None

        if principal <= 0:
            # 本金已全部赎回(ledger 净取出 ≥ 存入), 仓位已清; 利息已记进 realized_pnl。
            # 不能再用残留的 manual_value 当市值, 否则已赎回本金会被当成纯利润。
            current_value = 0.0
            source = "closed"
        elif manual is not None:
            # manual_value 语义: App 上显示的"当前账户余额"(剩余本金 + 当下浮动利息).
            # 卖出/赎回不修改它的语义, 因为 WITHDRAW 已经把本金部分从 lot 扣掉了.
            # 用户每次去 App 看完都直接同步这个数即可.
            current_value = round(float(manual), 2)
            source = "manual"
            # 反推年化只在样本足够（≥7 天）时才靠谱；短期内利息小数点会被放大
            # 成离谱的隐含年化（持有 2 天涨 1% 反推就是 200%/年）。
            if days_held >= 7 and principal > 0:
                implied_rate = round((current_value / principal - 1) * 365 / days_held, 4)
        elif rate is not None:
            r = float(rate)
            current_value = round(principal * (1 + r * days_held / 365), 2)
            source = "yield"
        else:
            current_value = principal  # 无估算依据

        quote = {
            "annual_yield_rate": float(rate) if rate is not None else None,
            "implied_yield_rate": implied_rate,
            "start_date": sd_str,
            "days_held": days_held,
            "accrued_interest": round(current_value - principal, 2),
            "value_source": source,  # 'manual' | 'yield' | None
            "auto_yield": True,
            "released_total": round(total_released, 2),
        }
    elif t == "BOT" and asset.get("okx_algo_id") and okx_client.has_credentials():
        # Auto-sync from OKX
        algo_id = asset["okx_algo_id"]
        bot_type = asset.get("okx_bot_type") or "grid"
        details = await okx_client.get_bot_details(algo_id, bot_type)
        if details:
            # Convert USDT → CNY using current rate (crypto quote gives us usdcny)
            btc_q = await get_crypto_quote("BTC-USDT")  # any symbol works just to get rate
            rate = (btc_q or {}).get("usdcny", 7.2)
            usdt_value = details["current_value_usdt"]
            current_value = round(usdt_value * rate, 2)
            # OKX investmentAmt 反映累计投入 (含追加投资), 用它覆盖本地 cost,
            # 避免追加投资后 PnL 算错. 原 cost_amount 保留在 DB 不动, 作兜底.
            live_cost_cny = round(details["investment_usdt"] * rate, 2) if details.get("investment_usdt") else None
            if live_cost_cny and live_cost_cny > 0:
                out["cost_amount"] = live_cost_cny
            quote = {
                **details,
                "usdcny": rate,
                "live_cost_cny": live_cost_cny,
                "auto_synced": True,
            }
            # 用户手填的总预算覆盖算法反推 (OKX raw 没"总预算"字段, 我们的等比和算法不准)
            override = asset.get("bot_budget_override_usdt")
            if override and override > 0:
                quote["total_budget_usdt"] = round(float(override), 2)
                quote["available_usdt"] = round(max(0.0, float(override) - details.get("investment_usdt", 0)), 2)
                quote["budget_source"] = "manual"
            else:
                quote["budget_source"] = "estimated"
    # else BOT: uses manual_value

    if t == "CASH":
        # 现金：消费/挪入挪出 不是投资盈亏。pnl 只反映 realized_pnl (INTEREST/DIVIDEND)；
        # cost_amount 同步为当前余额，避免 mv-cost 的账面差额被当成浮亏/浮盈。
        balance = float(current_value or 0)
        out["cost_amount"] = round(balance, 2)
        out["quote"] = quote
        out["current_value"] = round(balance, 2)
        out["pnl"] = round(realized_pnl, 2)
        out["pnl_pct"] = None
        return out

    cost = float(out.get("cost_amount") or 0)
    # PnL 只看已确认 lot vs 已确认成本: 从 current_value 里把 pending 剔出去再减 cost.
    # current_value 仍含 pending (资产总额视角); pnl 用 (mv - pending) - cost 算.
    pending_for_pnl = float(out.get("pending_amount") or 0) if t in ("FUND", "CRYPTO") else 0
    pnl = ((current_value or 0) - pending_for_pnl) - cost if current_value is not None else None
    pnl_pct = (pnl / cost * 100) if pnl is not None and cost > 0 else None

    out["quote"] = quote
    out["current_value"] = current_value
    out["pnl"] = round(pnl, 2) if pnl is not None else None
    out["pnl_pct"] = round(pnl_pct, 2) if pnl_pct is not None else None
    return out


@router.get("")
async def list_assets():
    """List all external assets with current values enriched."""
    rows = await list_external_assets()
    enriched = await asyncio.gather(*[_enrich(r) for r in rows])

    # Summary
    # NOTE: total_pnl 必须等于 Σ a["pnl"] (= 每个资产 (current_value - pending) - cost).
    # 不能简单 total_value - total_cost — current_value 含 pending 但 cost 不含,
    # 会让顶部 SummaryStrip 比持仓表多算 Σ pending_amount.
    total_cost = sum(float(a.get("cost_amount") or 0) for a in enriched)
    total_value = sum((a.get("current_value") or 0) for a in enriched)
    total_pnl = sum((a.get("pnl") or 0) for a in enriched)

    # By type
    by_type: dict[str, dict] = {}
    for a in enriched:
        t = a["asset_type"]
        if t not in by_type:
            by_type[t] = {"cost": 0.0, "value": 0.0, "pnl": 0.0, "count": 0}
        by_type[t]["cost"] += float(a.get("cost_amount") or 0)
        by_type[t]["value"] += a.get("current_value") or 0
        by_type[t]["pnl"] += a.get("pnl") or 0
        by_type[t]["count"] += 1
    for v in by_type.values():
        v["pnl"] = round(v["pnl"], 2)
        v["cost"] = round(v["cost"], 2)
        v["value"] = round(v["value"], 2)

    return {
        "assets": enriched,
        "summary": {
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
            "by_type": by_type,
        },
    }


@router.get("/realized")
async def assets_realized():
    """所有外部资产的累计已实现盈亏 (从 ledger 计算).

    BOT 跳过 (走 OKX, 不在本地 ledger).
    """
    rows = await list_external_assets()
    items: list[dict] = []
    total = 0.0
    for a in rows:
        t = a.get("asset_type") or ""
        if t == "BOT":
            continue
        actions = await list_external_actions(a["id"])
        if not actions:
            continue
        state = compute_external_state(actions, t)
        rp = float(state.get("realized_pnl") or 0)
        total += rp
        items.append({
            "asset_id": a["id"],
            "asset_type": t,
            "code": a.get("code") or "",
            "name": a.get("name") or "",
            "realized_pnl": rp,
            "income_realized": float(state.get("income_realized") or 0),
            "still_holding": (state.get("cost_amount") or 0) > 0,
        })
    items.sort(key=lambda x: x["realized_pnl"])
    return {"items": items, "total_realized_pnl": round(total, 2), "count": len(items)}


@router.post("")
async def create_asset(data: AssetCreate):
    if data.asset_type not in {"FUND", "CRYPTO", "BOT", "WEALTH", "CASH"}:
        raise HTTPException(400, "asset_type must be FUND / CRYPTO / BOT / WEALTH / CASH")

    # FUND/CRYPTO 且只填了金额没填份额 → 走 pending 模式 (T+1 未确认).
    # cost_amount 暂记 0, 实际金额放 pending_amount; seed action 写 status=pending.
    has_shares = data.shares is not None and float(data.shares) > 0
    is_pending_fund = (
        data.asset_type in ("FUND", "CRYPTO")
        and data.cost_amount is not None and data.cost_amount > 0
        and not has_shares
    )
    effective_cost = 0.0 if is_pending_fund else data.cost_amount
    effective_pending = (
        float(data.cost_amount) if is_pending_fund
        else (data.pending_amount if data.pending_amount is not None else None)
    )

    # 自动合并: FUND/CRYPTO 且 code 命中已有持仓 (同类型 + 同 code + 同券商) → 追加流水, 不新建重复。
    # 券商不同 = 不同账户, 不合并; 已清仓(shares=0)但有历史的也合并 (等于复活续上同一条流水)。
    merge_target = None
    if data.asset_type in ("FUND", "CRYPTO") and data.code:
        for ex in (await list_external_assets()):
            if (ex.get("asset_type") == data.asset_type
                    and (ex.get("code") or "") == data.code
                    and (ex.get("broker") or "") == (data.broker or "")):
                merge_target = ex
                break

    if merge_target:
        aid = merge_target["id"]
    else:
        aid = await add_external_asset(
            asset_type=data.asset_type,
            code=data.code,
            name=data.name,
            platform=data.platform or "",
            cost_amount=effective_cost,
            shares=data.shares,
            manual_value=data.manual_value,
            note=data.note or "",
            okx_algo_id=data.okx_algo_id,
            okx_bot_type=data.okx_bot_type,
            annual_yield_rate=data.annual_yield_rate,
            start_date=data.start_date,
            pending_amount=effective_pending,
            purchase_fee_rate=data.purchase_fee_rate,
            broker=data.broker,
        )
    # 写购买 action 进 ledger (BOT 不入账, 走 OKX 同步); 合并时标 add-lot, 新建标 initial
    if data.asset_type != "BOT" and data.cost_amount and data.cost_amount > 0:
        seed_type = "BUY" if data.asset_type in ("FUND", "CRYPTO") else "DEPOSIT"
        seed_fee = float(data.fee or 0)
        # cost_amount 已含 fee; 单价按净额 (cost - fee) / 份额, 保证 份额×单价+fee == amount,
        # 这样流水里手续费单独可见, 编辑也不会把它算丢。
        net = max(0.0, float(data.cost_amount) - seed_fee)
        unit_price = None
        if has_shares and data.asset_type in ("FUND", "CRYPTO"):
            unit_price = round(net / float(data.shares), 6)
        _tag = "add-lot" if merge_target else "initial"
        # start_date 缺失时兜底今天: 否则成交流水 trade_date 为空, 复盘/agent 会漏看这笔买入
        from datetime import date as _date
        seed_date = data.start_date or _date.today().isoformat()
        await add_external_action(
            aid, seed_type,
            amount=float(data.cost_amount),
            shares=float(data.shares) if has_shares else None,
            unit_price=unit_price,
            trade_date=seed_date,
            note=(f"{_tag} (pending: 份额待确认)" if is_pending_fund else _tag),
            status="pending" if is_pending_fund else "confirmed",
            fee=seed_fee if seed_fee > 0 else None,
            trade_time=(data.trade_time or None),
        )

    # 合并: 追加流水后按全部 action 重算已有持仓的 cost/shares (新建已由 add_external_asset 设好)
    if merge_target:
        acts = await list_external_actions(aid)
        state = compute_external_state(acts, data.asset_type)
        upd = {"cost_amount": state["cost_amount"]}
        if data.asset_type in ("FUND", "CRYPTO"):
            upd["shares"] = state["shares"]
        # 已有没设申购费率而这次填了 → 补上
        if merge_target.get("purchase_fee_rate") is None and data.purchase_fee_rate is not None:
            upd["purchase_fee_rate"] = data.purchase_fee_rate
        await update_external_asset(aid, **upd)

    # 顺便创建定投计划 (FUND/CRYPTO only)
    dca_created_id = None
    if data.dca and data.asset_type in ("FUND", "CRYPTO"):
        from services.dca import initial_next_due
        from database import add_dca_schedule
        d = data.dca
        if d.mode not in ("amount", "shares"):
            raise HTTPException(400, "dca.mode 必须是 amount 或 shares")
        if d.frequency not in ("daily_trading", "weekly", "monthly"):
            raise HTTPException(400, "dca.frequency 必须是 daily_trading / weekly / monthly")
        if d.value <= 0:
            raise HTTPException(400, "dca.value 必须 > 0")
        if d.frequency == "monthly":
            if d.day_of_month is None or not (1 <= int(d.day_of_month) <= 31):
                raise HTTPException(400, "monthly 频率必须 day_of_month (1-31)")
        elif d.frequency == "weekly":
            if d.day_of_week is None or not (1 <= int(d.day_of_week) <= 7):
                raise HTTPException(400, "weekly 频率必须 day_of_week (1-7)")
        next_due = initial_next_due(d.frequency, d.day_of_month, d.day_of_week)
        dca_created_id = await add_dca_schedule(
            asset_id=aid, mode=d.mode, value=d.value, frequency=d.frequency,
            day_of_month=d.day_of_month, day_of_week=d.day_of_week,
            next_due=next_due, note=d.note or "",
        )
    return {
        "message": "merged" if merge_target else "added", "id": aid,
        "merged": bool(merge_target),
        "pending": is_pending_fund,
        "dca_id": dca_created_id,
    }


class MergeFrom(BaseModel):
    source_id: int


@router.post("/{asset_id}/merge-from")
async def merge_assets(asset_id: int, data: MergeFrom):
    """把 source_id 的流水并入 asset_id, 重算, 删掉空壳 source。只允许同类型+同代码。"""
    target = await get_external_asset(asset_id)
    source = await get_external_asset(data.source_id)
    if not target or not source:
        raise HTTPException(404, "asset not found")
    if asset_id == data.source_id:
        raise HTTPException(400, "不能合并到自己")
    if target.get("asset_type") != source.get("asset_type") or (target.get("code") or "") != (source.get("code") or ""):
        raise HTTPException(400, "只能合并同类型+同代码的资产")

    moved = await reassign_external_actions(data.source_id, asset_id)
    acts = await list_external_actions(asset_id)
    state = compute_external_state(acts, target["asset_type"])
    upd: dict = {"cost_amount": state["cost_amount"]}
    if target["asset_type"] in ("FUND", "CRYPTO"):
        upd["shares"] = state["shares"]
    if target.get("purchase_fee_rate") is None and source.get("purchase_fee_rate") is not None:
        upd["purchase_fee_rate"] = source.get("purchase_fee_rate")
    await update_external_asset(asset_id, **upd)
    await delete_external_asset(data.source_id)
    return {
        "merged_into": asset_id, "deleted": data.source_id, "moved_actions": moved,
        "shares": state["shares"], "cost_amount": state["cost_amount"],
    }


@router.put("/{asset_id}")
async def modify_asset(asset_id: int, data: AssetUpdate):
    # Use exclude_unset so the frontend can explicitly clear a column by sending
    # null (e.g. unlock manual_value, drop pending_amount). Fields the client
    # didn't include stay untouched.
    payload = data.model_dump(exclude_unset=True)

    # FUND/CRYPTO 的 shares/cost_amount 由 ledger 推算 → 直接改 row 不会生效
    # (会被 _enrich 的 ledger overlay 覆盖). 把变更转成一条 ADD / REDEEM 调整 action
    # 写进流水, 让 ledger 跟用户的编辑保持一致.
    asset = await get_external_asset(asset_id)
    if asset and asset.get("asset_type") in ("FUND", "CRYPTO") and (
        "shares" in payload or "cost_amount" in payload
    ):
        actions = await list_external_actions(asset_id)
        state = compute_external_state(actions, asset["asset_type"])
        old_shares = float(state.get("shares") or 0)
        old_cost = float(state.get("cost_amount") or 0)
        new_shares = payload.get("shares")
        new_cost = payload.get("cost_amount")
        new_shares = float(new_shares) if new_shares is not None else old_shares
        new_cost = float(new_cost) if new_cost is not None else old_cost
        d_shares = new_shares - old_shares
        d_cost = new_cost - old_cost
        from datetime import date as _date
        today = _date.today().isoformat()
        if d_shares > 1e-6:
            unit = (max(d_cost, 0) / d_shares) if d_shares > 0 else None
            await add_external_action(
                asset_id, "ADD",
                amount=max(d_cost, 0), shares=d_shares, unit_price=unit,
                trade_date=today, note="adjust (from edit)",
            )
        elif d_shares < -1e-6:
            amt = max(abs(d_cost), 0)
            unit = (amt / abs(d_shares)) if d_shares != 0 else None
            await add_external_action(
                asset_id, "REDEEM",
                amount=amt, shares=abs(d_shares), unit_price=unit,
                trade_date=today, note="adjust (from edit)",
            )
        elif abs(d_cost) > 0.5:
            # 份额没变只动了成本 (>0.5 元才算真改, 否则是单价4位小数的舍入噪声):
            # 用 INTEREST(+) 或 一个 0 share 的成本调整 (-)
            if d_cost > 0:
                await add_external_action(
                    asset_id, "INTEREST",
                    amount=d_cost, trade_date=today, note="adjust (cost up)",
                )
            else:
                # 成本减少不太常见, 记一笔同名调整避免污染 INTEREST
                await add_external_action(
                    asset_id, "DIVIDEND",
                    amount=d_cost, trade_date=today, note="adjust (cost down)",
                )
        # 重新算 ledger 状态, 同步到 row 缓存
        actions = await list_external_actions(asset_id)
        state = compute_external_state(actions, asset["asset_type"])
        payload["shares"] = state["shares"]
        payload["cost_amount"] = state["cost_amount"]

    if payload:
        await update_external_asset(asset_id, **payload)
    return {"message": "updated"}


@router.post("/{asset_id}/add-lot")
async def add_lot(asset_id: int, data: LotAdd):
    """Add a follow-on purchase, recomputing aggregate cost/shares/start_date/yield.

    No transaction history is recorded — the asset row is mutated in place. If
    you need lot-level history, use `note` field or future ledger.
    """
    from datetime import date as _date, datetime as _dt, timedelta as _td

    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    t = asset["asset_type"]
    if t == "BOT":
        raise HTTPException(400, "BOT 资产不支持加仓 (走 OKX 同步)")
    if data.principal is None or data.principal <= 0:
        raise HTTPException(400, "principal 必须为正数")

    old_cost = float(asset.get("cost_amount") or 0)
    old_shares = float(asset.get("shares") or 0) if asset.get("shares") is not None else None
    new_cost = round(old_cost + data.principal, 4)
    payload: dict = {"cost_amount": new_cost}

    if t in ("FUND", "CRYPTO"):
        # Two modes:
        #   1) Confirmed: shares 或 unit_price 已知 → 累加 cost_amount + shares
        #   2) Pending:   只有 principal → 进 pending_amount, cost/shares 不动
        #      (份额结算后用户手动调 pending → 0 + 加 shares + 加 cost)
        has_unit = data.unit_price is not None and data.unit_price > 0
        if data.shares is not None or has_unit:
            inc_shares = float(data.shares) if data.shares is not None else round(data.principal / float(data.unit_price), 6)
            if old_shares is None:
                old_shares = 0.0
            payload["shares"] = round(old_shares + inc_shares, 6)
            payload["manual_value"] = None  # 让实时净值重新算
        else:
            # Pending 模式：只增 pending_amount，cost_amount/shares 不变
            old_pending = float(asset.get("pending_amount") or 0)
            payload = {"pending_amount": round(old_pending + data.principal, 4)}

    elif t in ("WEALTH", "CASH"):
        # Weighted-average start_date based on principal × days_held
        today = _date.today()
        try:
            old_start_str = asset.get("start_date") or str(asset.get("created_at", ""))[:10]
            old_start = _dt.strptime(old_start_str, "%Y-%m-%d").date()
        except Exception:
            old_start = today
        days_old = max(0, (today - old_start).days)

        if data.lot_start_date:
            try:
                lot_start = _dt.strptime(data.lot_start_date, "%Y-%m-%d").date()
            except Exception:
                lot_start = today
        else:
            lot_start = today
        days_lot = max(0, (today - lot_start).days)

        weighted_days = (old_cost * days_old + data.principal * days_lot) / new_cost if new_cost > 0 else 0
        new_start = today - _td(days=int(round(weighted_days)))
        payload["start_date"] = new_start.strftime("%Y-%m-%d")

        # Blend annual yield if a new one is given
        old_yield = asset.get("annual_yield_rate")
        if data.lot_yield_rate is not None:
            base_yield = float(old_yield) if old_yield is not None else float(data.lot_yield_rate)
            blended = (old_cost * base_yield + data.principal * float(data.lot_yield_rate)) / new_cost
            payload["annual_yield_rate"] = round(blended, 5)
        # If user wants WEALTH manual_value cleared (since principal changed),
        # they can edit later. We leave it alone.

    await update_external_asset(asset_id, **payload)

    # 写流水. OTC 基金 pending 模式 (只填本金) 也写一条 status=pending,
    # T+1 净值出来后用户在流水里"确认"补 shares/unit_price.
    action_type = "ADD" if t in ("FUND", "CRYPTO") else "DEPOSIT"
    seed_unit_price = None
    seed_shares = None
    status = "confirmed"
    fee = float(data.fee or 0)
    # principal 已含 fee; 算份额/反推单价都用净额 (principal - fee)
    net = max(0.0, float(data.principal) - fee)
    if t in ("FUND", "CRYPTO"):
        if data.shares is not None:
            seed_shares = float(data.shares)
            if data.unit_price is not None and data.unit_price > 0:
                seed_unit_price = float(data.unit_price)
            elif seed_shares and seed_shares > 0:
                seed_unit_price = round(net / seed_shares, 6)
        elif data.unit_price is not None and data.unit_price > 0:
            seed_unit_price = float(data.unit_price)
            seed_shares = round(net / seed_unit_price, 6)
        else:
            # OTC pending: 只填本金, 后续确认补 shares
            status = "pending"
    from datetime import date as _date
    await add_external_action(
        asset_id, action_type,
        amount=float(data.principal),
        shares=seed_shares,
        unit_price=seed_unit_price,
        trade_date=data.lot_start_date or _date.today().isoformat(),
        note="add-lot",
        status=status,
        fee=fee if fee > 0 else None,
        trade_time=(data.trade_time or None),
    )
    return {"message": "lot added", "new_state": payload, "status": status}


class LotReduce(BaseModel):
    """Redeem / withdraw from an existing asset.

    OTC 基金 (T+1): 仅 shares 必填, amount 可省 (会进 pending).
    ETF/CRYPTO 即时: amount 必填 + shares 或 unit_price 二选一.
    WEALTH/CASH: amount 必填; interest_part 可选 (拆出利息部分进 realized_pnl).
    """
    amount: Optional[float] = 0                # 赎回金额 CNY; 0/省略 + 是 OTC 基金 时进 pending
    shares: Optional[float] = None             # FUND/CRYPTO 赎回份额
    unit_price: Optional[float] = None         # FUND/CRYPTO 当时净值
    trade_date: Optional[str] = None           # YYYY-MM-DD
    trade_time: Optional[str] = None           # 成交时刻 HH:MM (可选, 供分时图打点)
    note: Optional[str] = ""
    interest_part: Optional[float] = None      # WEALTH/CASH: 本笔到账中的利息部分 (CNY)


@router.post("/{asset_id}/reduce-lot")
async def reduce_lot(asset_id: int, data: LotReduce):
    """记一笔赎回 / 减仓. 写流水 + 同步当前缓存的 cost_amount/shares.

    OTC 基金 (场外, 非 ETF): 仅传 shares 时进 pending 状态, 等 T+1 净值出来后再
    PUT confirm 补 amount/unit_price; ledger 暂不计入.
    ETF/CRYPTO: 必须 amount > 0 + 能定 shares, 直接 confirmed.
    WEALTH/CASH: amount 即可, 直接 confirmed.
    """
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    t = asset["asset_type"]
    if t == "BOT":
        raise HTTPException(400, "BOT 资产由 OKX 同步, 不支持手动减仓")

    is_otc_fund = (t == "FUND" and not _is_etf_code(asset.get("code") or ""))
    action_type = "REDEEM" if t in ("FUND", "CRYPTO") else "WITHDRAW"

    shares_in = data.shares
    unit_price = data.unit_price
    amount_in = data.amount

    status = "confirmed"

    if t in ("FUND", "CRYPTO"):
        if is_otc_fund and (amount_in is None or amount_in <= 0):
            # OTC 基金 T+1 模式: 必须有 shares (用户卖出份额已知)
            if not shares_in or shares_in <= 0:
                raise HTTPException(400, "OTC 基金赎回必须填份额")
            status = "pending"
            amount_in = 0  # 占位, 确认时填
        else:
            # ETF / CRYPTO 立即结算: amount 必须正, shares 或 unit_price 至少一个
            if amount_in is None or amount_in <= 0:
                raise HTTPException(400, "赎回金额必须为正数")
            if shares_in is None and unit_price and unit_price > 0:
                shares_in = round(amount_in / float(unit_price), 6)
            elif unit_price is None and shares_in and shares_in > 0:
                unit_price = round(amount_in / float(shares_in), 6)
            if shares_in is None or shares_in <= 0:
                raise HTTPException(400, "ETF/CRYPTO 赎回必须能定 shares (传 shares 或 unit_price)")
    else:
        # WEALTH / CASH
        if amount_in is None or amount_in <= 0:
            raise HTTPException(400, "赎回金额必须为正数")

    await add_external_action(
        asset_id, action_type,
        amount=float(amount_in or 0),
        shares=float(shares_in) if shares_in is not None else None,
        unit_price=float(unit_price) if unit_price is not None else None,
        trade_date=data.trade_date,
        note=data.note or "",
        status=status,
        interest_part=(
            float(data.interest_part)
            if (t in ("WEALTH", "CASH") and data.interest_part is not None)
            else None
        ),
        trade_time=(data.trade_time or None),
    )

    # 只有 confirmed 才同步缓存
    if status == "confirmed":
        actions = await list_external_actions(asset_id)
        state = compute_external_state(actions, t)
        sync: dict = {"cost_amount": state["cost_amount"]}
        if t in ("FUND", "CRYPTO"):
            sync["shares"] = state["shares"]
        await update_external_asset(asset_id, **sync)
        return {
            "message": "reduced",
            "status": status,
            "realized_pnl_total": state["realized_pnl"],
            "remaining_cost": state["cost_amount"],
            "remaining_shares": state["shares"] if t in ("FUND", "CRYPTO") else None,
        }
    return {"message": "pending — 等 T+1 净值出来后请确认", "status": status}


class ConfirmAction(BaseModel):
    amount: float
    shares: Optional[float] = None
    unit_price: Optional[float] = None
    fee: Optional[float] = None     # 手续费 (CNY), 含在 amount 里


@router.put("/{asset_id}/actions/{action_id}/confirm")
async def confirm_action(asset_id: int, action_id: int, data: ConfirmAction):
    """补全 pending 流水缺的 amount/unit_price (OTC 基金 T+1 净值出来后)."""
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "amount 必须为正数")

    actions = await list_external_actions(asset_id)
    target = next((a for a in actions if a["id"] == action_id), None)
    if not target:
        raise HTTPException(404, "action not found")
    if (target.get("status") or "confirmed") != "pending":
        raise HTTPException(400, "该流水已经确认过")

    shares = data.shares if data.shares is not None else target.get("shares")
    unit_price = data.unit_price
    # 手续费: 显式传了就用显式值; 没传则对场外基金申购按申购费率外扣自动算,
    # 跟「一键确认到期定投」(settle-pending) 口径一致 → 净额=金额/(1+费率)。
    if data.fee is not None:
        fee = float(data.fee)
    else:
        rate = float(asset.get("purchase_fee_rate") or 0)
        is_buy = (target.get("action_type") or "").upper() in ("ADD", "BUY")
        fee = round(float(data.amount) - float(data.amount) / (1 + rate), 2) \
            if (asset.get("asset_type") == "FUND" and rate > 0 and is_buy) else 0.0
    # 净额 = amount - fee (实际买到份额对应的钱), 反推 unit_price 用净额
    net_for_shares = float(data.amount) - fee
    if shares and shares > 0 and (unit_price is None or unit_price <= 0):
        unit_price = round(net_for_shares / float(shares), 6) if net_for_shares > 0 else 0
    elif unit_price and unit_price > 0 and (shares is None or shares <= 0):
        shares = round(net_for_shares / float(unit_price), 6) if net_for_shares > 0 else 0

    from database import update_external_action
    await update_external_action(
        action_id,
        amount=float(data.amount),
        shares=float(shares) if shares is not None else None,
        unit_price=float(unit_price) if unit_price is not None else None,
        fee=fee,
        status="confirmed",
    )

    # 同步缓存
    actions = await list_external_actions(asset_id)
    state = compute_external_state(actions, asset["asset_type"])
    sync: dict = {"cost_amount": state["cost_amount"]}
    if asset["asset_type"] in ("FUND", "CRYPTO"):
        sync["shares"] = state["shares"]
    await update_external_asset(asset_id, **sync)
    return {"message": "confirmed", "state": state}


@router.post("/settle-pending")
async def settle_pending_dca():
    """一键确认所有到期定投: 按每条 pending 的 trade_date 拉当日已确认净值, 自动结算份额。
    净值未公布的 (海外休市 / T+1 未出) 跳过, 仍保持 pending, 等下次再点。
    仅处理场外基金 (FUND) 的申购 pending (ADD/BUY); 加密等没历史净值的不动。
    """
    from services.external_assets import get_fund_nav_on_date
    from database import update_external_action

    assets = await list_external_assets()
    settled = 0
    skipped = 0
    details: list[dict] = []
    for asset in assets:
        if asset.get("asset_type") != "FUND":
            continue
        code = asset.get("code")
        if _is_onchain_etf(code):
            continue  # 场内 ETF 按市价成交, 不走 T+1 净值结算
        actions = await list_external_actions(asset["id"])
        pend = [a for a in actions
                if (a.get("status") or "confirmed") == "pending"
                and (a.get("action_type") or "").upper() in ("ADD", "BUY")]
        if not pend:
            continue
        changed = False
        for a in pend:
            td = (a.get("trade_date") or "")[:10]
            if not td:
                skipped += 1
                continue
            navinfo = await get_fund_nav_on_date(code, td)
            if not navinfo or not navinfo.get("nav"):
                skipped += 1  # 当日净值未公布 → 留 pending
                continue
            nav = float(navinfo["nav"])
            amount = float(a.get("amount") or 0)
            known_shares = float(a.get("shares") or 0)
            # 申购费内扣: 净申购额 = 申购金额 / (1+费率), 申购费 = 金额 - 净额, 份额 = 净额/净值。
            # C 类/无申购费 → rate=0 → 净额=金额, fee=0 (跟原来一致)。
            rate = float(asset.get("purchase_fee_rate") or 0)
            if amount > 0:                       # 金额定投
                net = amount / (1 + rate) if rate > 0 else amount
                fee = round(amount - net, 2)
                shares = round(net / nav, 6) if nav > 0 else 0
                final_amount = amount
            elif known_shares > 0:               # 份额定投: 份额×净值=净额, 反加申购费
                shares = known_shares
                net = shares * nav
                fee = round(net * rate, 2)
                final_amount = round(net + fee, 2)
            else:
                skipped += 1
                continue
            await update_external_action(
                a["id"], amount=final_amount, shares=shares,
                unit_price=round(nav, 6), fee=fee, status="confirmed",
            )
            settled += 1
            changed = True
            details.append({"asset": asset.get("name"), "date": td,
                            "nav": round(nav, 4), "shares": shares, "amount": final_amount, "fee": fee})
        if changed:
            acts2 = await list_external_actions(asset["id"])
            state = compute_external_state(acts2, asset["asset_type"])
            await update_external_asset(asset["id"], cost_amount=state["cost_amount"], shares=state["shares"])
    return {"settled": settled, "skipped": skipped, "details": details}


@router.post("/recompute-dca")
async def recompute_dca_fees():
    """费率改了之后, 重算已确认的定投流水 (note 以 'DCA' 开头).
    只动设置了 purchase_fee_rate 的基金 — 这些是用户刚补上申购费的, 之前可能按 fee=0 结算过。
    按每条 trade_date 重新拉当日净值, 用当前费率内扣重算 shares + fee。
    净值拉不到的跳过 (不动)。C 类 / 没设费率的基金完全不碰, 避免覆盖手动确认的值。
    """
    from services.external_assets import get_fund_nav_on_date
    from database import update_external_action

    assets = await list_external_assets()
    recomputed = 0
    skipped = 0
    details: list[dict] = []
    for asset in assets:
        if asset.get("asset_type") != "FUND":
            continue
        # rate=None (C 类/没设费率) 当 0 处理: DCA 份额仍按当日净值重算 (amount/nav)。
        # 只动 note 以 'DCA' 开头的自动定投行, 手动 add-lot / initial 不碰, 不会误覆盖。
        rate = float(asset.get("purchase_fee_rate") or 0)
        code = asset.get("code")
        if _is_onchain_etf(code):
            continue  # 场内 ETF 走市价/佣金, 不按净值+申购费重算
        actions = await list_external_actions(asset["id"])
        dca_rows = [a for a in actions
                    if (a.get("status") or "confirmed") == "confirmed"
                    and (a.get("action_type") or "").upper() in ("ADD", "BUY")
                    and str(a.get("note") or "").startswith("DCA")]
        if not dca_rows:
            continue
        changed = False
        for a in dca_rows:
            td = (a.get("trade_date") or "")[:10]
            amount = float(a.get("amount") or 0)
            if not td or amount <= 0:
                skipped += 1
                continue
            navinfo = await get_fund_nav_on_date(code, td)
            if not navinfo or not navinfo.get("nav"):
                skipped += 1
                continue
            nav = float(navinfo["nav"])
            net = amount / (1 + rate) if rate > 0 else amount
            fee = round(amount - net, 2)
            shares = round(net / nav, 6) if nav > 0 else 0
            # 没变就不写 (幂等)
            if abs(float(a.get("shares") or 0) - shares) < 1e-6 and abs(float(a.get("fee") or 0) - fee) < 0.005:
                continue
            await update_external_action(a["id"], shares=shares, unit_price=round(nav, 6), fee=fee)
            recomputed += 1
            changed = True
            details.append({"asset": asset.get("name"), "date": td, "nav": round(nav, 4),
                            "shares": shares, "fee": fee})
        if changed:
            acts2 = await list_external_actions(asset["id"])
            state = compute_external_state(acts2, asset["asset_type"])
            await update_external_asset(asset["id"], cost_amount=state["cost_amount"], shares=state["shares"])
    return {"recomputed": recomputed, "skipped": skipped, "details": details}


@router.post("/refresh-names")
async def refresh_fund_names():
    """按天天基金官方全称回填所有基金的名字 (场内 ETF 行情接口给的是简称, 会对不上)。
    名字拉不到的跳过; 一致的不动。"""
    from services.external_assets import _fetch_fund_name_sync
    assets = await list_external_assets()
    updated = 0
    details: list[dict] = []
    seen: dict[str, str] = {}
    for asset in assets:
        if asset.get("asset_type") != "FUND":
            continue
        code = asset.get("code") or ""
        full = seen.get(code)
        if full is None:
            try:
                full = await asyncio.to_thread(_fetch_fund_name_sync, code)
            except Exception:
                full = ""
            seen[code] = full or ""
        if full and full != asset.get("name"):
            await update_external_asset(asset["id"], name=full)
            details.append({"code": code, "old": asset.get("name"), "new": full})
            updated += 1
    return {"updated": updated, "details": details}


class ActionPatch(BaseModel):
    amount: Optional[float] = None
    shares: Optional[float] = None
    unit_price: Optional[float] = None
    fee: Optional[float] = None
    trade_date: Optional[str] = None
    trade_time: Optional[str] = None   # 成交时刻 HH:MM; ""=清空, None=不动


@router.patch("/{asset_id}/actions/{action_id}")
async def patch_action(asset_id: int, action_id: int, data: ActionPatch):
    """修改流水字段 (amount/shares/unit_price/fee).
    - pending 状态: 只接受 amount (兼容老行为, 状态保持 pending)
    - confirmed 状态: 接受全部字段, 改后重算资产 cost_amount/shares 同步
    """
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    actions = await list_external_actions(asset_id)
    target = next((a for a in actions if a["id"] == action_id), None)
    if not target:
        raise HTTPException(404, "action not found")
    status = target.get("status") or "confirmed"

    payload: dict = {}
    if data.amount is not None:
        if data.amount <= 0:
            raise HTTPException(400, "amount 必须为正数")
        payload["amount"] = float(data.amount)
    if data.shares is not None:
        if data.shares < 0:
            raise HTTPException(400, "shares 不能为负")
        payload["shares"] = float(data.shares)
    if data.unit_price is not None:
        if data.unit_price < 0:
            raise HTTPException(400, "unit_price 不能为负")
        payload["unit_price"] = float(data.unit_price)
    if data.fee is not None:
        if data.fee < 0:
            raise HTTPException(400, "fee 不能为负")
        payload["fee"] = float(data.fee)
    if data.trade_date is not None:
        td = str(data.trade_date)[:10]
        # 简单校验 YYYY-MM-DD
        from datetime import datetime as _dt
        try:
            _dt.strptime(td, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "trade_date 格式应为 YYYY-MM-DD")
        payload["trade_date"] = td
    if data.trade_time is not None:
        payload["trade_time"] = data.trade_time or None   # ""→NULL
    if not payload:
        raise HTTPException(400, "没有可修改的字段")

    if status == "pending":
        # pending 只允许改 amount / trade_date / trade_time (安全, 不动 ledger 数字);
        # shares/unit_price 这些得先点 '确认' 入账, 防 ledger 状态混乱
        if set(payload.keys()) - {"amount", "trade_date", "trade_time"}:
            raise HTTPException(400, "pending 流水只能改 amount / 日期, 其他字段请先点 '确认' 入账")

    from database import update_external_action
    await update_external_action(action_id, **payload)

    # confirmed 改后同步资产缓存
    if status == "confirmed":
        actions = await list_external_actions(asset_id)
        state = compute_external_state(actions, asset["asset_type"])
        sync: dict = {"cost_amount": state["cost_amount"]}
        if asset["asset_type"] in ("FUND", "CRYPTO"):
            sync["shares"] = state["shares"]
        await update_external_asset(asset_id, **sync)
    return {"message": "updated", **payload}


@router.get("/{asset_id}/actions")
async def list_actions(asset_id: int):
    """列流水 + 当前 ledger 状态."""
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    actions = await list_external_actions(asset_id)
    from database import resolve_action_time
    for a in actions:
        a["at_time"] = resolve_action_time(a)    # 成交时刻(供分时图打点)
    state = compute_external_state(actions, asset["asset_type"])
    return {"actions": actions, "state": state}


@router.delete("/{asset_id}/actions/{action_id}")
async def delete_action(asset_id: int, action_id: int):
    """删一条流水 (用于纠错). 之后会重新同步 cost_amount/shares."""
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    await delete_external_action(action_id)
    actions = await list_external_actions(asset_id)
    state = compute_external_state(actions, asset["asset_type"])
    sync: dict = {"cost_amount": state["cost_amount"]}
    if asset["asset_type"] in ("FUND", "CRYPTO"):
        sync["shares"] = state["shares"]
    await update_external_asset(asset_id, **sync)
    return {"message": "deleted", "state": state}


@router.delete("/{asset_id}")
async def remove_asset(asset_id: int):
    await delete_external_asset(asset_id)
    return {"message": "deleted"}


@router.get("/search/fund")
async def search_fund(keyword: str, limit: int = 5):
    """Proxy 天天基金 fund search — helps user find fund codes."""
    results = await search_fund_by_name(keyword, limit=limit)
    return {"results": results}


@router.get("/quote/fund/{code}")
async def quote_fund(code: str):
    q = await get_fund_quote(code)
    if not q:
        raise HTTPException(404, "基金代码无效或数据源不可达")
    return q


@router.get("/quote/crypto/{symbol}")
async def quote_crypto(symbol: str):
    """symbol like BTC-USDT, ETH-USDT."""
    q = await get_crypto_quote(symbol)
    if not q:
        raise HTTPException(404, "币种无效或数据源不可达")
    return q


# --- OKX integration ---

@router.get("/okx/status")
async def okx_status():
    """Check if OKX credentials are configured + live test."""
    if not okx_client.has_credentials():
        return {"configured": False}
    test = await okx_client.test_credentials()
    return {"configured": True, **test}


@router.post("/okx/credentials")
async def set_okx_credentials(data: OkxCredentials):
    """Save OKX API credentials to macOS Keychain. Read-only keys only — server
    never places trades. Validates by calling the trading-bot list endpoint."""
    ok, err = okx_client.save_credentials(data.api_key, data.secret_key, data.passphrase)
    if not ok:
        raise HTTPException(500, f"凭证保存失败：{err}")
    test = await okx_client.test_credentials()
    if not test.get("ok"):
        okx_client.clear_credentials()
        detail = "; ".join(test.get("errors") or []) or "未知"
        raise HTTPException(400, f"凭证校验失败: {detail}")
    return {"message": "saved", **test}


@router.delete("/okx/credentials")
async def remove_okx_credentials():
    ok = okx_client.clear_credentials()
    return {"message": "cleared" if ok else "nothing to clear"}


@router.get("/okx/debug/dca-raw")
async def okx_debug_dca_raw():
    """Debug: 返回 OKX DCA 多个 endpoint 的 raw 字段, 用来找"总预算"键名."""
    if not okx_client.has_credentials():
        raise HTTPException(400, "OKX 凭证未配置")
    import asyncio as _asyncio
    out = {}
    for ord_type in okx_client.DCA_ORD_TYPES:
        r = await _asyncio.to_thread(
            okx_client._authed_get, "/api/v5/tradingBot/dca/ongoing-list",
            {"algoOrdType": ord_type},
        )
        out[f"ongoing/{ord_type}"] = r
        # 拿到 algoId 后, 再试 sub-orders / details 看看
        if r and not r.get("error") and r.get("data"):
            for item in r["data"]:
                algo_id = item.get("algoId")
                if not algo_id: continue
                # 子订单 history
                sub = await _asyncio.to_thread(
                    okx_client._authed_get,
                    "/api/v5/tradingBot/dca/sub-orders",
                    {"algoOrdType": ord_type, "algoId": algo_id, "type": "live"},
                )
                out[f"sub-orders/{algo_id}"] = sub
                # stop-order-detail 看看
                det = await _asyncio.to_thread(
                    okx_client._authed_get,
                    "/api/v5/tradingBot/dca/stop-order-detail",
                    {"algoOrdType": ord_type, "algoId": algo_id},
                )
                out[f"stop-detail/{algo_id}"] = det
    return out


@router.get("/okx/bots")
async def list_okx_bots():
    """List user's OKX trading bots (grid family + DCA + signal). Requires credentials."""
    if not okx_client.has_credentials():
        raise HTTPException(400, "OKX 未配置凭证")
    grids = await okx_client.list_bots()
    dcas = await okx_client.list_dca_bots()
    signals = await okx_client.list_signal_bots()
    all_bots = (grids.get("bots") or []) + (dcas.get("bots") or []) + (signals.get("bots") or [])
    all_bots.sort(key=lambda b: (-int(b.get("active", False)), -(b.get("created_at_ms") or 0)))
    return {"bots": all_bots, "count": len(all_bots)}


@router.get("/okx/debug")
async def okx_debug():
    """Raw diagnostic: dump each endpoint's response so we can see what's returned."""
    if not okx_client.has_credentials():
        raise HTTPException(400, "OKX 未配置凭证")

    import asyncio as _asyncio
    probes = {
        "grid_pending_spot":       ("/api/v5/tradingBot/grid/orders-algo-pending",     {"algoOrdType": "grid"}),
        "grid_pending_contract":   ("/api/v5/tradingBot/grid/orders-algo-pending",     {"algoOrdType": "contract_grid"}),
        "grid_history_spot":       ("/api/v5/tradingBot/grid/orders-algo-history",     {"algoOrdType": "grid",         "limit": "20"}),
        "grid_history_contract":   ("/api/v5/tradingBot/grid/orders-algo-history",     {"algoOrdType": "contract_grid","limit": "20"}),
        "recurring_pending":       ("/api/v5/tradingBot/recurring/orders-algo-pending", None),
        "recurring_history":       ("/api/v5/tradingBot/recurring/orders-algo-history", {"limit": "20"}),
        "recurring_orders":        ("/api/v5/tradingBot/recurring/orders",              None),
        "recurring_orders_history":("/api/v5/tradingBot/recurring/orders-history",       {"limit": "20"}),
        "recurring_sub_orders":    ("/api/v5/tradingBot/recurring/sub-orders",          {"limit": "5"}),
        # Try other possible endpoints
        "chasing_pending":         ("/api/v5/tradingBot/chasing/orders-algo-pending",   None),
        "chasing_history":         ("/api/v5/tradingBot/chasing/orders-algo-history",   {"limit": "20"}),
        "public_preset_moon":      ("/api/v5/tradingBot/public/preset-moon-params",     None),
        # Try without type filter
        "grid_pending_notype":     ("/api/v5/tradingBot/grid/orders-algo-pending",      None),
        "grid_history_notype":     ("/api/v5/tradingBot/grid/orders-algo-history",      {"limit": "20"}),
        # Signal variants — list
        "signal_pending":          ("/api/v5/tradingBot/signal/orders-algo-pending",    {"algoOrdType": "contract"}),
        "signal_history":          ("/api/v5/tradingBot/signal/orders-algo-history",    {"algoOrdType": "contract", "limit": "20"}),
        # DCA 马丁格尔 - /dca/ongoing-list 需要 algoOrdType 必填
        "dca_ongoing_spot":        ("/api/v5/tradingBot/dca/ongoing-list",              {"algoOrdType": "spot_dca"}),
        "dca_ongoing_contract":    ("/api/v5/tradingBot/dca/ongoing-list",              {"algoOrdType": "contract_dca"}),
        "dca_history_spot":        ("/api/v5/tradingBot/dca/history-list",              {"algoOrdType": "spot_dca", "limit": "20"}),
        "dca_history_contract":    ("/api/v5/tradingBot/dca/history-list",              {"algoOrdType": "contract_dca", "limit": "20"}),
        # Any spot trading positions? (helps verify key scope)
        "account_balance":         ("/api/v5/account/balance",                          None),
    }

    out = {}
    for label, (path, params) in probes.items():
        r = await _asyncio.to_thread(okx_client._authed_get, path, params)
        if r is None:
            out[label] = {"_error": "no response"}
        elif r.get("error"):
            out[label] = {"_error": r["error"]}
        else:
            data = r.get("data") or []
            # Just return count + first item key set so we see what types exist
            out[label] = {
                "count": len(data),
                "sample_keys": list(data[0].keys()) if data else [],
                "sample": data[0] if data else None,
            }
    return out
