"""用户画像记忆: 记下即生效、可覆盖、可删、封顶, 评测模式不落库。"""
import asyncio
import os
import tempfile

import pytest

from services import user_memory as um


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    monkeypatch.delenv("AGENT_MEMORY_READONLY", raising=False)
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


def test_remember_then_rendered_into_system(temp_db):
    r = asyncio.run(um.remember({"fact": "用户的场外基金扣款均为定投, 不属于主动操作"}))
    assert r["ok"]
    from services.stock_agent import _system
    assert f"[{r['id']}] 用户的场外基金扣款均为定投" in _system()


def test_replace_and_delete(temp_db):
    a = asyncio.run(um.remember({"fact": "用户偏好表格"}))
    b = asyncio.run(um.remember({"fact": "用户偏好分点列表", "replace_id": a["id"]}))
    assert [m["fact"] for m in um.active_sync()] == ["用户偏好分点列表"]
    asyncio.run(um.remember({"fact": "", "replace_id": b["id"]}))
    assert um.active_sync() == []
    assert "error" in asyncio.run(um.remember({"fact": "x", "replace_id": 999}))


def test_cap(temp_db, monkeypatch):
    monkeypatch.setattr(um, "MAX_ACTIVE", 2)
    asyncio.run(um.remember({"fact": "a"}))
    asyncio.run(um.remember({"fact": "b"}))
    assert "error" in asyncio.run(um.remember({"fact": "c"}))
    assert len(um.active_sync()) == 2


def test_readonly_in_eval(temp_db, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_READONLY", "1")
    assert asyncio.run(um.remember({"fact": "a"}))["ok"]
    assert um.active_sync() == []


def test_render_empty():
    assert um.render([]) == ""
