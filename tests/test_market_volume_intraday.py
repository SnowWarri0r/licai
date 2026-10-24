"""当日分时量能: 数据源偶发超时时的重试、退回上一份结果, 以及两市缺一边时不拿半个市场冒充两市。"""
import asyncio

import pytest

from services import market_volume as mv


def _day(scale_amt: float = 1.0) -> dict:
    """一个完整交易日(240 档)+ 今天 10 档的累计序列, 形如 _sina_min_sync 的返回。"""
    def series(n):
        out, t = [], 9 * 60 + 31
        for i in range(n):
            if t == 11 * 60 + 31:
                t = 13 * 60 + 1
            out.append((f"{t // 60:02d}:{t % 60:02d}", (i + 1) * 1e8, (i + 1) * 1e9 * scale_amt))
            t += 1
        return out
    return {"2026-10-22": series(240), "2026-10-23": series(10)}


@pytest.fixture(autouse=True)
def _clear_cache():
    mv._intraday_cache.clear()
    yield
    mv._intraday_cache.clear()


def test_retries_once_after_timeout(monkeypatch):
    calls = {"n": 0}

    def flaky(sym, datalen, scale):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")
        return _day()

    monkeypatch.setattr(mv, "_sina_min_sync", flaky)
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert len(out["points"]) == 10
    assert calls["n"] == 3          # 1 分钟线超时一次后重试成功 + 5 分钟线一次


def test_falls_back_to_last_good_result(monkeypatch):
    monkeypatch.setattr(mv, "_sina_min_sync", lambda sym, datalen, scale: _day())
    good = asyncio.run(mv.market_volume_intraday("创业"))
    sym, ts = mv._intraday_cache["创业"]
    mv._intraday_cache["创业"] = (sym, ts - 3600)         # 让缓存过期, 迫使重新拉

    def down(sym, datalen, scale):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(mv, "_sina_min_sync", down)
    out = asyncio.run(mv.market_volume_intraday("创业"))
    assert out["stale"] is True
    assert out["points"] == good["points"]


def test_two_markets_need_both_exchanges(monkeypatch):
    def only_sh(sym, datalen, scale):
        if sym == mv._SYM["深"]:
            raise TimeoutError("read timed out")
        return _day()

    monkeypatch.setattr(mv, "_sina_min_sync", only_sh)
    out = asyncio.run(mv.market_volume_intraday("两市"))
    assert out["points"] == []
    assert "两市" not in mv._intraday_cache


def test_two_markets_sum_both_exchanges(monkeypatch):
    monkeypatch.setattr(mv, "_sina_min_sync",
                        lambda sym, datalen, scale: _day(2.0 if sym == mv._SYM["深"] else 1.0))
    out = asyncio.run(mv.market_volume_intraday("两市"))
    assert out["actual"]["amt"] == round((10e9 + 20e9) / 1e8)
