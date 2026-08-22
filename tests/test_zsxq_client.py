"""知识星球只读接入(走官方 MCP HTTP 端点)。

真端点要 api_key, 测试里假造 requests.post 的响应, 只验我们这侧: SSE 解析、只读白名单、
翻页去重、时间窗、富文本清洗、观点标记、脱敏。不碰真账号也不发真请求。
"""
import json
import pytest

from services import mcp_http
from services import zsxq_client as z

URL = "https://mcp.zsxq.com/topic/mcp?api_key=TESTKEY"


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


def _sse(result: dict) -> str:
    """端点实测回的是 SSE 帧, 工具数据在 result.content[].text 里且是 JSON 字符串。"""
    body = {"jsonrpc": "2.0", "id": 2, "result": result}
    return "event: message\ndata: " + json.dumps(body, ensure_ascii=False) + "\n\n"


def _tool_ok(payload) -> str:
    return _sse({"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]})


@pytest.fixture
def post(monkeypatch):
    """按 (tool_name → payload) 假造响应, 并记录实际发出的调用。"""
    calls = []

    def _install(mapping):
        def _post(self, url, json=None, headers=None, timeout=None):
            name = ((json or {}).get("params") or {}).get("name") or (json or {}).get("method")
            calls.append({"url": url, "name": name,
                          "args": ((json or {}).get("params") or {}).get("arguments") or {}})
            v = mapping.get(name)
            if v is None:
                return _Resp(_sse({"content": [{"type": "text",
                                                "text": '{"error":"no stub","success":false}'}],
                                   "isError": True}))
            return _Resp(v if isinstance(v, str) else _tool_ok(v))
        monkeypatch.setattr("requests.Session.post", _post)
        z.configure(URL, [{"group_id": "1", "name": "打板复盘"}])
        return calls

    yield _install
    z.configure("", [])


def _topic(tid, text, t, author="爱在冰川"):
    return {"topic_id": tid, "type": "talk", "create_time": t,
            "likes_count": 3, "comments_count": 1,
            "talk": {"text": text, "owner": {"name": author}}}


def _now(hours_ago=1):
    from datetime import datetime, timedelta
    return (datetime.now().astimezone() - timedelta(hours=hours_ago)) \
        .strftime("%Y-%m-%dT%H:%M:%S.000+0800")


# ── 传输层 ───────────────────────────────────────────────
def test_sse_and_plain_json_both_parse():
    assert mcp_http._parse_body("event: message\ndata: {\"result\":{\"a\":1}}")["result"] == {"a": 1}
    assert mcp_http._parse_body('{"result":{"a":2}}')["result"] == {"a": 2}
    assert mcp_http._parse_body("") is None
    assert mcp_http._parse_body("<html>502</html>") is None


def test_tool_error_surfaced(monkeypatch):
    """实测无凭证时: isError=true 且正文是 {"error":"Authentication failed","success":false}。"""
    body = _sse({"content": [{"type": "text",
                              "text": '{"error":"Authentication failed","success":false}'}],
                 "isError": True})
    monkeypatch.setattr("requests.Session.post", lambda *a, **k: _Resp(body))
    r = mcp_http.call_tool(URL, "get_self_info", {})
    assert r["ok"] is False and "Authentication failed" in r["error"]["message"]


def test_success_false_without_iserror_still_fails(monkeypatch):
    body = _sse({"content": [{"type": "text", "text": '{"success":false,"error":"quota"}'}]})
    monkeypatch.setattr("requests.Session.post", lambda *a, **k: _Resp(body))
    assert mcp_http.call_tool(URL, "get_self_info", {})["ok"] is False


def test_rpc_level_error(monkeypatch):
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"Method not found"}}'
    monkeypatch.setattr("requests.Session.post", lambda *a, **k: _Resp(body))
    r = mcp_http.call_tool(URL, "get_self_info", {})
    assert r["ok"] is False and r["error"]["type"] == "rpc"


# ── 只读约束 ─────────────────────────────────────────────
def test_write_tools_are_refused(post):
    """远端 21 个工具里有 create_topic / create_topic_comment / set_topic_digested /
    call_zsxq_api 这些写口。白名单必须挡住, 且不发请求。"""
    calls = post({})
    for bad in ("create_topic", "create_topic_comment", "set_topic_digested",
                "set_topic_tags", "create_note", "call_zsxq_api"):
        r = z._call(bad, {})
        assert r["ok"] is False and r["error"]["type"] == "forbidden", bad
    assert not calls


def test_no_call_without_url():
    z.configure("", [{"group_id": "1", "name": "x"}])
    assert z.is_enabled() is False
    r = z._call("get_self_info")
    assert r["ok"] is False and r["error"]["type"] == "not_configured"


# ── 取数 ─────────────────────────────────────────────────
def test_list_groups_goes_through_self_info(post):
    calls = post({
        "get_self_info": {"user_id": 42, "name": "我"},
        "get_user_groups": {"groups": [
            {"group": {"group_id": 123456789, "name": "打板复盘", "type": "pay"}},
            {"group": {"name": "缺 id 的脏数据"}},
        ]},
    })
    r = z.list_groups()
    assert r["ok"] and r["groups"] == [{"group_id": "123456789", "name": "打板复盘", "type": "pay"}]
    assert [c["name"] for c in calls] == ["get_self_info", "get_user_groups"]
    assert calls[1]["args"]["user_id"] == "42"


def test_topics_default_to_owner_only(post):
    """复盘星球里成员闲聊占九成, 默认只要星主的帖(scope=by_owner)。"""
    calls = post({"get_group_topics": {"topics": [_topic("a", "情绪退潮", _now())]}})
    z.list_topics("1", "打板复盘", days=1)
    assert calls[0]["args"]["scope"] == "by_owner"
    calls.clear()
    z.list_topics("1", "打板复盘", days=1, owner_only=False)
    assert calls[0]["args"]["scope"] == "all"


def test_topics_dedupe_across_pages(monkeypatch):
    """翻页游标是上一页末条的 create_time 且含等于, 下一页会把它重复返回为首条 ——
    必须按 topic_id 去重, 否则「今天几条」会虚高。"""
    t1, t2, t3 = _now(1), _now(2), _now(3)
    pages = [
        {"topics": [_topic("1", "情绪退潮", t1), _topic("2", "承接一般", t2)],
         "next_end_time": t2},
        {"topics": [_topic("2", "承接一般", t2), _topic("3", "再看一天", t3)]},
    ]
    seq = iter(pages)

    def _post(self, url, json=None, headers=None, timeout=None):
        return _Resp(_tool_ok(next(seq, {"topics": []})))

    monkeypatch.setattr("requests.Session.post", _post)
    z.configure(URL, [{"group_id": "1", "name": "打板复盘"}])
    got = z.list_topics("1", "打板复盘", days=1)
    assert [t["topic_id"] for t in got] == ["1", "2", "3"]      # 2 只出现一次
    z.configure("", [])


def test_topics_drop_older_than_window(post):
    post({"get_group_topics": {"topics": [_topic("a", "今天", _now(2)),
                                          _topic("b", "上周", _now(24 * 9))]}})
    assert [t["topic_id"] for t in z.list_topics("1", "打板复盘", days=1)] == ["a"]


def test_rich_text_markers_cleaned(post):
    """正文里混着 <e type="hashtag" title="%23复盘%23"/> 这类富文本标记, 直接喂模型会脏。"""
    raw = ('今天 <e type="hashtag" hid="1" title="%23打板复盘%23" /> 情绪退潮, '
           '<e type="mention" uid="9" title="@某人" />说注意接力。')
    post({"get_group_topics": {"topics": [_topic("a", raw, _now())]}})
    t = z.list_topics("1", "打板复盘", days=1)[0]
    assert "<e" not in t["正文"] and "#打板复盘#" in t["正文"]
    assert t["作者"] == "爱在冰川"
    assert t["stance"] == "opinion"      # 观点标记必须在, agent 靠它打 [星球观点]


def test_search_marks_opinion(post):
    post({"search_topics": {"topics": [_topic("s1", "中钨高新 是资源涨价逻辑", _now())]}})
    got = z.search_topics("中钨高新", "1", "打板复盘")
    assert len(got) == 1 and got[0]["stance"] == "opinion" and got[0]["星球"] == "打板复盘"
    assert z.search_topics("", "1") == []          # 空词不发请求


def test_topic_detail_can_include_comments(post):
    """博主常在评论里补充/修正正文的判断, 所以详情可连评论一起取。"""
    calls = post({
        "get_topic_info": {"topic": _topic("t9", "正文判断", _now())},
        "get_topic_comments": {"comments": [
            {"text": "补充: 尾盘又炸了", "owner": {"name": "爱在冰川"}}]},
    })
    d = z.topic_detail("t9", "打板复盘", with_comments=True)
    assert d["评论摘录"] == [{"作者": "爱在冰川", "内容": "补充: 尾盘又炸了"}]
    assert [c["name"] for c in calls] == ["get_topic_info", "get_topic_comments"]


# ── 凭证脱敏 ─────────────────────────────────────────────
def test_api_key_never_leaks_in_labels():
    z.configure(URL, [{"group_id": "1", "name": "x"}])
    label = z.endpoint_label()
    assert "TESTKEY" not in label and "api_key" not in label
    assert label == "mcp.zsxq.com/topic/mcp"
    h = z.health.__doc__ or ""
    assert "不返回任何含 key 的串" in h
    z.configure("", [])


def test_health_reports_unconfigured():
    z.configure("", [])
    h = z.health()
    assert h["configured"] is False and h["ok"] is False


def test_agent_hides_tools_when_not_configured():
    """没接入时这两个工具不该塞给模型, 否则它会去调然后拿一串 error。"""
    import services.stock_agent as sa
    z.configure("", [])
    names = {t.get("name") for t in sa._active_tools()}
    assert "get_zsxq_digest" not in names and "search_zsxq" not in names
    z.configure(URL, [{"group_id": "1", "name": "x"}])
    names = {t.get("name") for t in sa._active_tools()}
    assert "get_zsxq_digest" in names and "search_zsxq" in names
    z.configure("", [])
