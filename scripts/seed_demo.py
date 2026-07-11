"""理财助手 · 演示数据填充脚本.

适用场景:
  1. 第一次 clone 想看效果, 不想录入自己数据 → `python scripts/seed_demo.py --use`
  2. 截图发 README / 演示视频, 又不想暴露真实持仓 → `python scripts/seed_demo.py --use`
  3. 看完 demo 想恢复自己的数据 → `python scripts/seed_demo.py --restore`

数据特点:
  - 6 大类全覆盖: A股/基金/理财/现金/加密 (机器人需 OKX 凭证, 跳过)
  - 跨行业避免单押 (消费/汽车/银行/科技等)
  - 一些浮盈一些浮亏, 演示警告条不同样式
  - 真实股票/基金代码, 实时行情接口能正常拉到

⚠ 演示数据明显是虚构的 (持仓金额规整, 起投日整数), 别误解为投顾建议.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "portfolio.db"
DEMO_PATH = ROOT / "portfolio.demo.db"
BACKUP_PATH = ROOT / "portfolio.real.db.bak"


# ---- 演示数据定义 ----

DEMO_HOLDINGS = [
    # (code, name, shares, cost_price, buy_days_ago)
    # 建仓日期分散在过去三个月, 让净值曲线/持有天数/事件日历都有真实素材
    ("600519", "贵州茅台",  1,   1620.00, 95),  # 消费 — 白酒龙头, 1 股
    ("002594", "比亚迪",   200,  220.00, 80),   # 汽车 — 新能源
    ("600036", "招商银行",  500, 38.00,  65),    # 金融 — 银行
    ("300750", "宁德时代",  100, 240.00, 50),   # 电气新能源
]

DEMO_ASSETS = [
    # FUND (5 只, 含场内宽基/行业主题/商品 + 场外 + QDII; 真实公开代码方便实时行情可拉)
    # buy_days_ago: 初始建仓日, 让份额流水有历史(净值曲线用)
    {
        "asset_type": "FUND", "code": "510300", "name": "沪深300ETF",
        "platform": "证券账户", "cost_amount": 2100, "shares": 500, "buy_days_ago": 90,
    },
    {
        "asset_type": "FUND", "code": "512480", "name": "半导体ETF",
        "platform": "证券账户", "cost_amount": 3200, "shares": 3000, "buy_days_ago": 70,
    },
    {
        "asset_type": "FUND", "code": "518880", "name": "黄金ETF",
        "platform": "证券账户", "cost_amount": 4500, "shares": 1000, "buy_days_ago": 55,
    },
    {
        "asset_type": "FUND", "code": "012922",
        "name": "易方达全球成长精选混合(QDII)人民币C",
        "platform": "公募平台", "cost_amount": 2500, "shares": 1000, "buy_days_ago": 45,
    },
    {
        "asset_type": "FUND", "code": "008702", "name": "华夏黄金ETF联接C",
        "platform": "公募平台", "cost_amount": 6000, "shares": 3000, "buy_days_ago": 40,
    },
    # WEALTH (3 只, 名称泛化)
    {
        "asset_type": "WEALTH", "code": "DEMO001",
        "name": "稳健型理财 1 号 (演示)",
        "platform": "银行 A", "cost_amount": 5000,
        "annual_yield_rate": 0.025,
        "start_date": (date.today() - timedelta(days=60)).strftime("%Y-%m-%d"),
    },
    {
        "asset_type": "WEALTH", "code": "DEMO002",
        "name": "日开型理财 (演示)",
        "platform": "银行 B", "cost_amount": 10000,
        "annual_yield_rate": 0.022,
        "start_date": (date.today() - timedelta(days=30)).strftime("%Y-%m-%d"),
    },
    {
        "asset_type": "WEALTH", "code": "DEMO003",
        "name": "30 天滚动型 (演示)",
        "platform": "银行 C", "cost_amount": 8000,
        "annual_yield_rate": 0.020,
        "start_date": (date.today() - timedelta(days=15)).strftime("%Y-%m-%d"),
    },
    # CASH (3 只, 货币基金/活期, 名称泛化)
    {
        "asset_type": "CASH", "code": "mmf-a", "name": "货币基金 A",
        "platform": "支付平台 A", "cost_amount": 5000, "manual_value": 5000,
        "annual_yield_rate": 0.015,
    },
    {
        "asset_type": "CASH", "code": "mmf-b", "name": "货币基金 B",
        "platform": "支付平台 B", "cost_amount": 2000, "manual_value": 2000,
        "annual_yield_rate": 0.014,
    },
    {
        "asset_type": "CASH", "code": "mmf-c", "name": "活期账户",
        "platform": "银行 A", "cost_amount": 3000, "manual_value": 3000,
        "annual_yield_rate": 0.016,
    },
    # CRYPTO (1 只, 通用)
    {
        "asset_type": "CRYPTO", "code": "BTC-USDT", "name": "比特币",
        "platform": "加密交易所", "cost_amount": 18000, "shares": 0.05, "buy_days_ago": 85,
    },
]


# ---- 主流程 ----

async def seed_into(db_path: Path):
    """直接调内部 helpers, 不走 HTTP. 这样脚本能在服务器没启动时也跑."""
    # 临时把 DB_PATH 改到目标
    os.environ.pop("DB_PATH", None)
    from config import config as _config
    original_db = _config.db_path
    _config.db_path = str(db_path)
    try:
        from database import (
            init_db, add_holding, add_position_action,
            add_external_asset,
        )
        from services.position_ledger import compute_position_state
        from database import get_position_actions, update_holding

        await init_db()

        # 建仓价 = 建仓日真实收盘(拉不到才用表里的兜底价): 净值曲线上不出现"编造成本 vs
        # 真实行情"的假跳变, demo 的浮盈亏也更像真账户
        async def _close_on(code_, d_):
            try:
                from services.market_data import get_historical_data
                df = await get_historical_data(code_, days=160)
                rows = {str(r["日期"])[:10]: float(r["收盘"]) for _, r in df.iterrows()}
                ks = sorted(k for k in rows if k <= d_)
                return rows[ks[-1]] if ks else None
            except Exception:
                return None

        # A 股持仓 (走 holdings + position_actions, 触发综合成本计算)
        # 建仓日期回填到过去几个月: 净值曲线/持有天数/复盘统计才有真实素材
        for code, name, shares, cost, days_ago in DEMO_HOLDINGS:
            buy_date = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            cost = (await _close_on(code, buy_date)) or cost
            await add_holding(code, name, shares, cost)
            await add_position_action(code, "BUY", cost, shares,
                                      trade_date=buy_date, note="initial (demo)")
            actions = await get_position_actions(code, limit=500)
            state = compute_position_state(actions, stock_code=code)
            if state["shares"] > 0:
                await update_holding(code, shares=state["shares"], cost_price=state["cost_price"])

        # 外部资产(基金带日期回填的初始申购流水, 份额账本/净值曲线可用;
        # 申购价同样按建仓日真实净值/收盘取)
        from database import add_external_action
        from services.external_assets import _is_onchain_etf
        for a in DEMO_ASSETS:
            cost_amount, unit = a["cost_amount"], None
            if a.get("shares") and a.get("buy_days_ago"):
                d0 = (date.today() - timedelta(days=a["buy_days_ago"])).strftime("%Y-%m-%d")
                code = a["code"]
                if a["asset_type"] == "FUND":
                    if _is_onchain_etf(code):
                        unit = await _close_on(code, d0)
                    else:
                        try:
                            from services.portfolio_curve import _otc_nav_hist_sync
                            navs = _otc_nav_hist_sync(code)
                            ks = sorted(k for k in navs if k <= d0)
                            unit = navs[ks[-1]] if ks else None
                        except Exception:
                            unit = None
                if unit:
                    cost_amount = round(unit * a["shares"], 2)
            aid = await add_external_asset(
                asset_type=a["asset_type"],
                code=a["code"],
                name=a["name"],
                platform=a.get("platform", ""),
                cost_amount=cost_amount,
                shares=a.get("shares"),
                manual_value=a.get("manual_value"),
                note=a.get("note", ""),
                annual_yield_rate=a.get("annual_yield_rate"),
                start_date=a.get("start_date"),
            )
            if a.get("shares") and a.get("buy_days_ago"):
                d0 = (date.today() - timedelta(days=a["buy_days_ago"])).strftime("%Y-%m-%d")
                await add_external_action(
                    aid, "BUY", amount=cost_amount, shares=a["shares"],
                    unit_price=unit or round(cost_amount / a["shares"], 4),
                    trade_date=d0, note="initial (demo)")
        print(f"✓ 已写入 {len(DEMO_HOLDINGS)} 只 A股 + {len(DEMO_ASSETS)} 笔外部资产到 {db_path.name}")
    finally:
        _config.db_path = original_db


def cmd_create():
    """生成 portfolio.demo.db, 不动当前 DB."""
    if DEMO_PATH.exists():
        print(f"删除旧 {DEMO_PATH.name}")
        DEMO_PATH.unlink()
    asyncio.run(seed_into(DEMO_PATH))
    print(f"✓ Demo DB ready: {DEMO_PATH}")


def cmd_use():
    """备份当前 DB → 切换到 demo. 服务器需要重启才生效."""
    if DB_PATH.exists():
        if BACKUP_PATH.exists():
            print(f"⚠ 备份已存在 {BACKUP_PATH.name}, 跳过覆盖. 想恢复用 --restore.")
        else:
            shutil.copy2(DB_PATH, BACKUP_PATH)
            print(f"✓ 真实数据备份: {BACKUP_PATH.name}")
        DB_PATH.unlink()
    asyncio.run(seed_into(DB_PATH))
    print(f"✓ 演示数据已写入 portfolio.db. 重启服务器: lsof -ti:8888 | xargs kill -9; python run.py")


def cmd_restore():
    """演示完恢复真实数据."""
    if not BACKUP_PATH.exists():
        print(f"❌ 没有找到备份 {BACKUP_PATH}, 无法恢复.")
        return
    if DB_PATH.exists():
        DB_PATH.unlink()
    shutil.move(BACKUP_PATH, DB_PATH)
    print(f"✓ 真实数据已恢复. 重启服务器即可.")


def cmd_clean():
    """删 demo DB + 备份 (用于 cleanup)."""
    for p in (DEMO_PATH, BACKUP_PATH):
        if p.exists():
            p.unlink()
            print(f"✓ 删除 {p.name}")


def main():
    p = argparse.ArgumentParser(description="理财助手 · 演示数据填充")
    p.add_argument("--use", action="store_true",
                   help="备份当前 portfolio.db, 写入 demo 数据 (用于截图/演示)")
    p.add_argument("--restore", action="store_true",
                   help="恢复 --use 之前备份的真实数据")
    p.add_argument("--clean", action="store_true",
                   help="删除 portfolio.demo.db + portfolio.real.db.bak")
    p.add_argument("--peek", action="store_true",
                   help="(默认) 生成 portfolio.demo.db, 不动当前 DB")
    args = p.parse_args()

    if args.use:
        cmd_use()
    elif args.restore:
        cmd_restore()
    elif args.clean:
        cmd_clean()
    else:
        cmd_create()


if __name__ == "__main__":
    main()
