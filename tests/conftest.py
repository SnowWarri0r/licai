"""全测试共用: agent 入口一进来就会在后台预取资产看板(见 stock_agent._assets_snapshot),
用假 LLM 跑 loop 的测试不该因此去碰真实账本和行情源。"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _no_assets_prefetch(monkeypatch):
    import services.stock_agent as sa

    def stub(fresh=False):
        f = asyncio.get_running_loop().create_future()
        f.set_result({"assets": []})
        return f
    monkeypatch.setattr(sa, "_assets_snapshot", stub)
