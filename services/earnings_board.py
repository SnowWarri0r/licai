"""业绩预告看板: 最新报告期(当前=中报)全市场业绩预告的预喜/预警榜。

数据 = 东财业绩预告表(复用蓄势扫描的 _forecast_map: 每股一条, 归母净利>净利>扣非)。
预喜 = 预增/略增/扭亏/续盈/减亏; 预警 = 预减/略减/首亏/续亏/增亏。
未披露 ≠ 业绩差(预告只对大幅变动强制)。纯客观数据陈列, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time

_cache: dict = {}
_TTL = 1800


async def earnings_board(top: int = 100) -> dict:
    c = _cache.get("board")
    if not c or time.time() - c[1] >= _TTL:
        from services.coiled_scanner import _forecast_map, _FC_POS, _FC_NEG
        from services import etf_xray
        fc, imap = await asyncio.gather(asyncio.to_thread(_forecast_map),
                                        asyncio.to_thread(etf_xray.industry_map))
        period = fc.get("_期") or "当期"
        # 在持关联(A股直持 + 场内ETF前十大成分)
        via: dict[str, str] = {}
        try:
            from services.event_calendar import _watch_list
            for w in await _watch_list():
                via[w["code"]] = w["via"]
        except Exception:
            pass
        rows = []
        for code, f in fc.items():
            if code.startswith("_"):
                continue
            nm, ind, cap = imap.get(code, (code, "", 0))
            rows.append({"code": code, "name": nm or code, "行业": ind,
                         "类型": f["类型"], "幅度%": f.get("幅度%"),
                         "披露日": f["日期"], "市值亿": cap,
                         "持仓关联": via.get(code, "")})
        pos = sorted([r for r in rows if r["类型"] in _FC_POS],
                     key=lambda r: -(r["幅度%"] if r["幅度%"] is not None else -1e9))
        neg = sorted([r for r in rows if r["类型"] in _FC_NEG],
                     key=lambda r: (r["幅度%"] if r["幅度%"] is not None else 1e9))
        _cache["board"] = ({"period": period, "pos": pos, "neg": neg,
                            "total": len(rows)}, time.time())
    b = _cache["board"][0]
    held_pos = [r for r in b["pos"] if r["持仓关联"]]
    held_neg = [r for r in b["neg"] if r["持仓关联"]]
    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "period": b["period"], "total": b["total"],
        "预喜": b["pos"][:top], "预警": b["neg"][:top],
        "n_预喜": len(b["pos"]), "n_预警": len(b["neg"]),
        "持仓关联预喜": held_pos[:20], "持仓关联预警": held_neg[:20],
        "note": f"最新报告期({b['period']})业绩预告, 全市场已披露 {b['total']} 家"
                f"(预喜 {len(b['pos'])} / 预警 {len(b['neg'])})。幅度=归母净利同比变动中值%。"
                "未披露 ≠ 业绩差(预告只对大幅变动强制); 正式财报以披露日公告为准。"
                "持仓关联=A股直持或经由在持场内ETF前十大成分。纯客观数据, 不构成任何买卖建议。",
    }
