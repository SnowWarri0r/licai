"""美股解析: 全大写 ticker / US. 前缀先按美股验, 免得被 A 股拼音首字母抢走; 中英文名走美股搜索。"""
import asyncio
from unittest import mock

import services.market_data as md
import services.stock_agent as sa


class _R:
    text = 'v_hint="us~ter.oq~\\u6cf0\\u745e\\u8fbe~trd~GP^us~tceff.ps~terra clean energy corp~*~GP^us~terc.am~corgi ter 2x~*~QZ"'


def test_search_us_parses_and_drops_pink_and_non_stock():
    md._us_search_cache.clear()
    with mock.patch.object(md._requests, "get", return_value=_R()):
        assert md.search_us_stocks("泰瑞达") == [{"code": "US.TER", "name": "泰瑞达"}]


def test_uppercase_ticker_resolves_to_us_before_pinyin(monkeypatch):
    async def hit(t):
        return {"code": f"US.{t}", "name": t, "market": "US", "in_holdings": False} if t in ("TER", "SYM") else None

    async def none():
        return []
    monkeypatch.setattr(sa, "_us_ticker_hit", hit)
    monkeypatch.setattr(sa, "_active_holdings", none)
    monkeypatch.setattr("database.get_all_holdings", none)
    monkeypatch.setattr("database.list_external_assets", none)
    assert asyncio.run(sa._tool_resolve_stock("TER"))["code"] == "US.TER"
    assert asyncio.run(sa._tool_resolve_stock("US.SYM"))["code"] == "US.SYM"
