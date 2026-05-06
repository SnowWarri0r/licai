"""External assets (funds / crypto / bots) REST endpoints."""
from __future__ import annotations
import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_external_assets, add_external_asset, update_external_asset, delete_external_asset,
    get_external_asset,
    list_external_actions, add_external_action, delete_external_action,
)
from services.external_assets import (
    get_fund_quote, get_crypto_quote, search_fund_by_name,
)
from services.external_ledger import compute_external_state
from services import okx_client

router = APIRouter(prefix="/api/assets", tags=["external-assets"])


def _is_etf_code(code: str) -> bool:
    """场内 ETF: 上交所 5xxxxx / 深交所 159xxx / 科创 588xxx (6 位).
    场外基金 (含 ETF 联接): 0xxxxx / 1xxxxx (除 159) / 9xxxxx 等."""
    c = (code or "").strip()
    if len(c) != 6 or not c.isdigit():
        return False
    return c.startswith("5") or c.startswith("159") or c.startswith("588")


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
    pending_amount: Optional[float] = None     # FUND/CRYPTO 待确认金额 (份额未结算)


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
    lot_start_date: Optional[str] = None    # WEALTH 加投起投日 (default: today)
    lot_yield_rate: Optional[float] = None  # WEALTH 加投年化 (default: keep existing)


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
    if t != "BOT":
        try:
            actions = await list_external_actions(asset["id"])
            if actions:
                pending_count = sum(1 for a in actions if (a.get("status") or "confirmed") == "pending")
                ledger_state = compute_external_state(actions, t)
                if ledger_state["cost_amount"] >= 0:
                    out["cost_amount"] = ledger_state["cost_amount"]
                if t in ("FUND", "CRYPTO"):
                    out["shares"] = ledger_state["shares"]
                realized_pnl = ledger_state["realized_pnl"]
        except Exception as e:
            print(f"[assets] ledger enrich failed for asset#{asset.get('id')}: {e}")
    out["realized_pnl"] = round(realized_pnl, 2)
    out["pending_actions_count"] = pending_count

    if t == "FUND":
        quote = await get_fund_quote(asset["code"])
        pending = float(asset.get("pending_amount") or 0)
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
        # 附加代理标的行情 (用底层市场实时数据预判基金当日走势)
        try:
            from services.fund_proxy import get_fund_proxy
            proxy = await get_fund_proxy(asset["code"])
            if proxy and quote is not None:
                quote["proxy_change_pct"] = proxy["weighted_change_pct"]
                quote["proxy_label"] = proxy["label"]
                quote["proxy_details"] = proxy["proxies"]
        except Exception as e:
            print(f"[fund-proxy] {asset['code']} failed: {e}")
    elif t == "CRYPTO":
        quote = await get_crypto_quote(asset["code"])
        pending = float(asset.get("pending_amount") or 0)
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

        if manual is not None:
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
    # else BOT: uses manual_value

    cost = float(out.get("cost_amount") or 0)
    pnl = (current_value or 0) - cost if current_value is not None else None
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
    total_cost = sum(float(a.get("cost_amount") or 0) for a in enriched)
    total_value = sum((a.get("current_value") or 0) for a in enriched)
    total_pnl = total_value - total_cost

    # By type
    by_type: dict[str, dict] = {}
    for a in enriched:
        t = a["asset_type"]
        if t not in by_type:
            by_type[t] = {"cost": 0.0, "value": 0.0, "count": 0}
        by_type[t]["cost"] += float(a.get("cost_amount") or 0)
        by_type[t]["value"] += a.get("current_value") or 0
        by_type[t]["count"] += 1
    for v in by_type.values():
        v["pnl"] = round(v["value"] - v["cost"], 2)
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
    aid = await add_external_asset(
        asset_type=data.asset_type,
        code=data.code,
        name=data.name,
        platform=data.platform or "",
        cost_amount=data.cost_amount,
        shares=data.shares,
        manual_value=data.manual_value,
        note=data.note or "",
        okx_algo_id=data.okx_algo_id,
        okx_bot_type=data.okx_bot_type,
        annual_yield_rate=data.annual_yield_rate,
        start_date=data.start_date,
        pending_amount=data.pending_amount,
    )
    # 写一条初始 action 进 ledger (BOT 不入账, 走 OKX 同步)
    if data.asset_type != "BOT" and data.cost_amount and data.cost_amount > 0:
        seed_type = "BUY" if data.asset_type in ("FUND", "CRYPTO") else "DEPOSIT"
        unit_price = None
        if data.asset_type in ("FUND", "CRYPTO") and data.shares and float(data.shares) > 0:
            unit_price = round(float(data.cost_amount) / float(data.shares), 6)
        await add_external_action(
            aid, seed_type,
            amount=float(data.cost_amount),
            shares=float(data.shares) if data.shares else None,
            unit_price=unit_price,
            trade_date=data.start_date,
            note="initial",
        )
    return {"message": "added", "id": aid}


@router.put("/{asset_id}")
async def modify_asset(asset_id: int, data: AssetUpdate):
    # Use exclude_unset so the frontend can explicitly clear a column by sending
    # null (e.g. unlock manual_value, drop pending_amount). Fields the client
    # didn't include stay untouched.
    payload = data.model_dump(exclude_unset=True)
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
    if t in ("FUND", "CRYPTO"):
        if data.shares is not None:
            seed_shares = float(data.shares)
            if data.unit_price is not None and data.unit_price > 0:
                seed_unit_price = float(data.unit_price)
            elif seed_shares and seed_shares > 0:
                seed_unit_price = round(data.principal / seed_shares, 6)
        elif data.unit_price is not None and data.unit_price > 0:
            seed_unit_price = float(data.unit_price)
            seed_shares = round(data.principal / seed_unit_price, 6)
        else:
            # OTC pending: 只填本金, 后续确认补 shares
            status = "pending"
    await add_external_action(
        asset_id, action_type,
        amount=float(data.principal),
        shares=seed_shares,
        unit_price=seed_unit_price,
        trade_date=data.lot_start_date,
        note="add-lot",
        status=status,
    )
    return {"message": "lot added", "new_state": payload, "status": status}


class LotReduce(BaseModel):
    """Redeem / withdraw from an existing asset.

    OTC 基金 (T+1): 仅 shares 必填, amount 可省 (会进 pending).
    ETF/CRYPTO 即时: amount 必填 + shares 或 unit_price 二选一.
    WEALTH/CASH: amount 必填.
    """
    amount: Optional[float] = 0                # 赎回金额 CNY; 0/省略 + 是 OTC 基金 时进 pending
    shares: Optional[float] = None             # FUND/CRYPTO 赎回份额
    unit_price: Optional[float] = None         # FUND/CRYPTO 当时净值
    trade_date: Optional[str] = None           # YYYY-MM-DD
    note: Optional[str] = ""


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
    if shares and shares > 0 and (unit_price is None or unit_price <= 0):
        unit_price = round(float(data.amount) / float(shares), 6)
    elif unit_price and unit_price > 0 and (shares is None or shares <= 0):
        shares = round(float(data.amount) / float(unit_price), 6)

    from database import update_external_action
    await update_external_action(
        action_id,
        amount=float(data.amount),
        shares=float(shares) if shares is not None else None,
        unit_price=float(unit_price) if unit_price is not None else None,
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


@router.get("/{asset_id}/actions")
async def list_actions(asset_id: int):
    """列流水 + 当前 ledger 状态."""
    asset = await get_external_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    actions = await list_external_actions(asset_id)
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
