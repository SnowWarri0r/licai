"""盯盘员: 发现按 key 去重, 同检测器的新状态取代旧的; 现金对上了不产出, 对不上给出差额。"""
import asyncio

import pytest

import database
from services import watcher as w


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("config.config.db_path", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())


def test_drift_finding_tolerance_and_text():
    assert w.drift_finding(1000.4, 1000.0, "现金", "2026-01-05") is None
    f = w.drift_finding(1500.0, 1000.0, "现金", "2026-01-05")
    assert f["key"] == "books_drift::2026-01-05::1500.00" and "+500.00" in f["body"]


def test_record_dedups_and_new_state_supersedes(db):
    d = "2026-01-05"
    f1 = w.drift_finding(1500.0, 1000.0, "现金", d)
    assert len(asyncio.run(w.record([f1], d))) == 1
    assert asyncio.run(w.record([f1], d)) == []                       # 同 key 只记一次
    f2 = w.drift_finding(1800.0, 1000.0, "现金", d)
    asyncio.run(w.resolve("books_drift", d, keep_key=f2["key"]))
    asyncio.run(w.record([f2], d))
    items = asyncio.run(w.list_findings(d))["items"]
    assert [x["key"] for x in items] == [f2["key"]]                    # 旧状态收起, 只剩新的
    assert asyncio.run(w.dismiss(items[0]["id"]))
    assert asyncio.run(w.list_findings(d))["items"] == []


def test_broker_cash_reads_number_and_fails_quietly():
    assert asyncio.run(w._broker_cash("echo 12,345.67")) == 12345.67
    assert asyncio.run(w._broker_cash("false")) is None                # 客户端没开: 非零退出
    assert asyncio.run(w._broker_cash("echo 没有")) is None


def test_cash_cmd_never_read_from_db(db, monkeypatch):
    """要执行的命令只认本机 config.py; 数据库(导入备份可写)里的同名键不执行。"""
    monkeypatch.setattr("config.config.watch_cash_cmd", "", raising=False)
    asyncio.run(database.set_config("watch_cash_cmd", "touch /nonexistent/x"))
    asyncio.run(database.set_config("watch_cash_asset_id", "1"))
    ran = []
    monkeypatch.setattr(w, "_broker_cash", lambda cmd: ran.append(cmd))
    assert asyncio.run(w.check_books_drift()) is None and ran == []
