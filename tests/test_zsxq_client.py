"""知识星球只读接入。

真 CLI 要 OAuth 登录, 测试里用一个假 CLI(打印固定 JSON 的 shell 脚本)顶替, 只验我们这侧的
解析/去重/时间窗/降级, 不碰真账号。
"""
import json
import os
import stat
import tempfile
import pytest

from services import zsxq_client as z


def _fake_cli(mapping: dict, tmpdir: str) -> str:
    """按参数里出现的关键字选输出。mapping: {关键字: 要打印的 dict}"""
    path = os.path.join(tmpdir, "fake-zsxq")
    cases = "\n".join(
        f'''if [[ "$*" == *"{k}"* ]]; then cat <<'EOF'\n{json.dumps(v, ensure_ascii=False)}\nEOF\n  exit 0\nfi'''
        for k, v in mapping.items())
    with open(path, "w") as f:
        f.write("#!/bin/bash\n" + cases + "\necho '{\"ok\":false,\"error\":{\"type\":\"x\"}}'\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def cli(monkeypatch):
    d = tempfile.mkdtemp()

    def _install(mapping, groups=None):
        z.configure(_fake_cli(mapping, d), groups if groups is not None else [])
        return z

    yield _install
    z.configure("zsxq-cli", [])


def _topic(tid, text, t, title="", author="爱在冰川"):
    return {"topic_id": tid, "type": "talk", "title": title,
            "create_time": t, "likes_count": 3, "comments_count": 1,
            "talk": {"text": text, "owner": {"name": author}}}


def test_not_installed_degrades_quietly(monkeypatch):
    z.configure("/nonexistent/zsxq-cli", [{"group_id": "1", "name": "x"}])
    r = z._run(["group", "+list"])
    assert r["ok"] is False and r["error"]["type"] == "not_installed"
    assert z.list_topics("1") == []          # 不抛异常, 空结果


def test_auth_error_surfaced_in_health(cli):
    zc = cli({"group": {"ok": False, "error": {"type": "auth", "message": "not logged in",
                                               "hint": "run `zsxq-cli auth login`"}}})
    h = zc.health()
    assert h["installed"] is True and h["logged_in"] is False
    assert "not logged in" in h["error"] and "auth login" in h["hint"]


def test_list_groups_normalizes(cli):
    zc = cli({"+list": {"ok": True, "data": {"groups": [
        {"group_id": 123456789, "name": "打板复盘", "type": "pay"},
        {"name": "缺 id 的脏数据"},
    ]}}})
    r = zc.list_groups()
    assert r["ok"] and r["groups"] == [{"group_id": "123456789", "name": "打板复盘", "type": "pay"}]


def test_topics_dedupe_across_pages(cli):
    """翻页游标 next_end_time 含等于, 下一页会把上一页最后一条重复返回为首条 —— 必须去重,
    否则「今天几条」这类计数会虚高。"""
    from datetime import datetime, timedelta
    now = datetime.now().astimezone()
    t1 = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    t2 = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    t3 = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    page1 = {"ok": True, "data": {"topics": [_topic("1", "情绪退潮", t1), _topic("2", "承接一般", t2)],
                                 "has_more": True, "next_end_time": t2}}
    # 第二页首条就是第一页末条(id=2), 这是官方文档明确的边界重复
    page2 = {"ok": True, "data": {"topics": [_topic("2", "承接一般", t2), _topic("3", "昨日回顾", t3)],
                                 "has_more": False}}
    d = tempfile.mkdtemp()
    path = os.path.join(d, "fake")
    with open(path, "w") as f:
        f.write("#!/bin/bash\n"
                f'''if [[ "$*" == *"--end-time"* ]]; then cat <<'EOF'\n{json.dumps(page2, ensure_ascii=False)}\nEOF\n  exit 0\nfi\n'''
                f'''cat <<'EOF'\n{json.dumps(page1, ensure_ascii=False)}\nEOF\n''')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    z.configure(path, [{"group_id": "1", "name": "打板复盘"}])
    got = z.list_topics("1", "打板复盘", days=1)
    assert [t["topic_id"] for t in got] == ["1", "2", "3"]     # 2 只出现一次


def test_topics_drop_older_than_window(cli):
    from datetime import datetime, timedelta
    now = datetime.now().astimezone()
    fresh = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    old = (now - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
    zc = cli({"+topics": {"ok": True, "data": {"topics": [_topic("a", "今天", fresh),
                                                          _topic("b", "上周", old)]}}})
    got = zc.list_topics("1", "打板复盘", days=1)
    assert [t["topic_id"] for t in got] == ["a"]


def test_rich_text_markers_cleaned(cli):
    """zsxq 正文里混着 <e type="hashtag" title="%23复盘%23"/> 这类富文本标记, 直接喂模型会脏。"""
    raw = ('今天 <e type="hashtag" hid="1" title="%23打板复盘%23" /> 情绪退潮, '
           '<e type="mention" uid="9" title="@某人" />说注意接力。')
    zc = cli({"+topics": {"ok": True, "data": {"topics": [
        _topic("a", raw, __import__("datetime").datetime.now().astimezone()
               .strftime("%Y-%m-%dT%H:%M:%S.000+0800"))]}}})
    t = zc.list_topics("1", "打板复盘", days=1)[0]
    assert "<e" not in t["正文"] and "#打板复盘#" in t["正文"]
    assert t["作者"] == "爱在冰川"
    assert t["stance"] == "opinion"          # 观点标记必须在, agent 靠它打 [星球观点]


def test_search_returns_opinion_marked_topics(cli):
    zc = cli({"+search": {"ok": True, "data": {"topics": [
        _topic("s1", "中钨高新 这波是资源涨价逻辑", "2026-08-18T10:00:00.000+0800")]}}})
    got = zc.search_topics("中钨高新", "1", "打板复盘")
    assert len(got) == 1 and got[0]["stance"] == "opinion"
    assert got[0]["星球"] == "打板复盘"
    assert zc.search_topics("", "1") == []   # 空词不发请求


def test_enabled_only_when_groups_selected():
    z.configure("zsxq-cli", [])
    assert z.is_enabled() is False
    z.configure("zsxq-cli", [{"group_id": "1", "name": "x"}])
    assert z.is_enabled() is True
    z.configure("zsxq-cli", [])


def test_agent_hides_tools_when_not_configured():
    """没接入时这两个工具不该塞给模型, 否则它会去调然后拿一串 error。"""
    import services.stock_agent as sa
    z.configure("zsxq-cli", [])
    names = {t.get("name") for t in sa._active_tools()}
    assert "get_zsxq_digest" not in names and "search_zsxq" not in names
    z.configure("zsxq-cli", [{"group_id": "1", "name": "x"}])
    names = {t.get("name") for t in sa._active_tools()}
    assert "get_zsxq_digest" in names and "search_zsxq" in names
    z.configure("zsxq-cli", [])


def test_only_read_commands_are_used():
    """写操作(发帖/评论/加精/删除)一律不该出现在这个模块里 —— 账号级风险且对理财无用。"""
    import inspect
    src = inspect.getsource(z)
    for cmd in ("+create", "+reply", "+edit", "+set", "+answer", "note", "api raw"):
        assert f'"{cmd}"' not in src, f"只读模块里出现了写命令 {cmd}"


def test_error_envelope_on_stderr_is_parsed():
    """实测 CLI 把失败信封写到 stderr、stdout 留空。只读 stdout 会把「未登录」错认成
    「无输出」, 拿不到 type=auth 和 hint, 设置页就没法提示用户去登录。"""
    import os
    import stat
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "fake-stderr")
    with open(path, "w") as f:
        f.write('#!/bin/bash\n'
                'cat >&2 <<\'EOF\'\n'
                '{"ok":false,"error":{"type":"auth","message":"not logged in",'
                '"hint":"run `zsxq-cli auth login` to authenticate"}}\n'
                'EOF\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    z.configure(path, [])
    r = z._run(["group", "+list"])
    assert r["ok"] is False and r["error"]["type"] == "auth"
    assert "auth login" in r["error"]["hint"]
    h = z.health()
    assert h["installed"] is True and h["logged_in"] is False and "auth login" in h["hint"]
    z.configure("zsxq-cli", [])
