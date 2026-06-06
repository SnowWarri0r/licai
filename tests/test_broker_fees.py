import asyncio
import os
import tempfile
import pytest
import config as cfg
import database as db


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # database.get_db() reads config.config.db_path (the singleton instance)
    monkeypatch.setattr("config.config.db_path", path)
    asyncio.run(db.init_db())
    yield path
    os.remove(path)


def test_seed_creates_zhaoshang_yinhe(fresh_db):
    rows = asyncio.run(db.list_brokers())
    names = {r["name"] for r in rows}
    assert "招商证券" in names and "银河证券" in names
    zs = next(r for r in rows if r["name"] == "招商证券")
    assert abs(zs["stock_rate"] - 0.0001854) < 1e-9
    assert zs["stock_min"] == 5 and zs["is_default"] == 1
    yh = next(r for r in rows if r["name"] == "银河证券")
    assert abs(yh["etf_rate"] - 0.00005) < 1e-9 and yh["etf_min"] == 0.1


def test_set_default_is_exclusive(fresh_db):
    rows = asyncio.run(db.list_brokers())
    yh = next(r for r in rows if r["name"] == "银河证券")
    asyncio.run(db.update_broker(yh["id"], is_default=1))
    rows2 = asyncio.run(db.list_brokers())
    defaults = [r for r in rows2 if r["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "银河证券"
