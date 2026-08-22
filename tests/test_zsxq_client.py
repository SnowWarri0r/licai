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


def _topic(tid, text, t, author="基业长青", **extra):
    """真实返回形态(实测 get_group_topics): 平铺结构, 正文在 content, 作者在顶层 owner,
    计数在 counts —— 不是嵌在 talk 里。假数据必须跟真的一致, 否则测试全绿而线上取不到。"""
    d = {"topic_id": tid, "type": "talk", "create_time": t, "title": "",
         "content": text, "owner": {"name": author, "alias": "", "user_id": "9"},
         "counts": {"likes": 3, "comments": 1, "readers": 10},
         "digested": False, "images": [], "files": [],
         "group": {"group_id": "1", "name": "打板复盘"}}
    d.update(extra)
    return d


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


def test_topics_scope_defaults_to_all(post):
    """默认 scope=all。实测「短评&信息」用 by_owner 只回三月份两条图片帖, scope=all 才是
    当天真有价值的研报摘要 —— 发帖人不一定被算成星主/合伙人, 所以默认别筛。"""
    calls = post({"get_group_topics": {"topics_brief": [_topic("a", "情绪退潮", _now())]}})
    z.list_topics("1", "打板复盘", days=1)
    assert calls[0]["args"]["scope"] == "all"
    calls.clear()
    z.list_topics("1", "打板复盘", days=1, owner_only=True)
    assert calls[0]["args"]["scope"] == "by_owner"


def test_topics_dedupe_across_pages(monkeypatch):
    """翻页游标是上一页末条的 create_time 且含等于, 下一页会把它重复返回为首条 ——
    必须按 topic_id 去重, 否则「今天几条」会虚高。"""
    t1, t2, t3 = _now(1), _now(2), _now(3)
    pages = [
        {"topics_brief": [_topic("1", "情绪退潮", t1), _topic("2", "承接一般", t2)],
         "next_end_time": t2},
        {"topics_brief": [_topic("2", "承接一般", t2), _topic("3", "再看一天", t3)]},
    ]
    seq = iter(pages)

    def _post(self, url, json=None, headers=None, timeout=None):
        return _Resp(_tool_ok(next(seq, {"topics_brief": []})))

    monkeypatch.setattr("requests.Session.post", _post)
    z.configure(URL, [{"group_id": "1", "name": "打板复盘"}])
    got = z.list_topics("1", "打板复盘", days=1)
    assert [t["topic_id"] for t in got] == ["1", "2", "3"]      # 2 只出现一次
    z.configure("", [])


def test_topics_drop_older_than_window(post):
    post({"get_group_topics": {"topics_brief": [_topic("a", "今天", _now(2)),
                                          _topic("b", "上周", _now(24 * 9))]}})
    assert [t["topic_id"] for t in z.list_topics("1", "打板复盘", days=1)] == ["a"]


def test_rich_text_markers_cleaned(post):
    """正文里混着 <e type="hashtag" title="%23复盘%23"/> 这类富文本标记, 直接喂模型会脏。"""
    raw = ('今天 <e type="hashtag" hid="1" title="%23打板复盘%23" /> 情绪退潮, '
           '<e type="mention" uid="9" title="@某人" />说注意接力。')
    post({"get_group_topics": {"topics_brief": [_topic("a", raw, _now())]}})
    t = z.list_topics("1", "打板复盘", days=1)[0]
    assert "<e" not in t["正文"] and "#打板复盘#" in t["正文"]
    assert t["作者"] == "基业长青"
    assert t["stance"] == "opinion"      # 观点标记必须在, agent 靠它打 [星球观点]


def test_search_marks_opinion(post):
    post({"search_topics": {"topics_brief": [_topic("s1", "中钨高新 是资源涨价逻辑", _now())]}})
    got = z.search_topics("中钨高新", "1", "打板复盘")
    assert len(got) == 1 and got[0]["stance"] == "opinion" and got[0]["星球"] == "打板复盘"
    assert z.search_topics("", "1") == []          # 空词不发请求


def test_topic_detail_can_include_comments(post):
    """博主常在评论里补充/修正正文的判断, 所以详情可连评论一起取。"""
    calls = post({
        "get_topic_info": {"topic": _topic("t9", "正文判断", _now())},
        "get_topic_comments": {"comments": [
            {"text": "补充: 尾盘又炸了", "owner": {"name": "基业长青"}}]},
    })
    d = z.topic_detail("t9", "打板复盘", with_comments=True)
    assert d["评论摘录"] == [{"作者": "基业长青", "内容": "补充: 尾盘又炸了"}]
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


def test_utf8_body_not_decoded_as_latin1(monkeypatch):
    """SSE 的 Content-Type 不带 charset, requests 的 r.text 会按 ISO-8859-1 解 —— 中文星球名
    直接变「æ°´」这种乱码(「水」的 UTF-8 三字节被当 latin-1)。必须自己按 UTF-8 解字节。"""
    payload = {"groups": [{"group": {"group_id": 1, "name": "水调歌头 Plus"}}]}
    frame = ("event: message\ndata: " + json.dumps(
        {"jsonrpc": "2.0", "id": 2,
         "result": {"content": [{"type": "text",
                                 "text": json.dumps(payload, ensure_ascii=False)}]}},
        ensure_ascii=False) + "\n\n")

    class _Bytes:
        content = frame.encode("utf-8")
        status_code = 200
        # requests 在没有 charset 的 text/* 上就是这么干的, 复现它
        text = frame.encode("utf-8").decode("latin-1")

    monkeypatch.setattr("requests.Session.post", lambda *a, **k: _Bytes())
    r = mcp_http.call_tool(URL, "get_user_groups", {"user_id": "1"})
    assert r["ok"] and r["data"]["groups"][0]["group"]["name"] == "水调歌头 Plus"
    assert "æ" not in json.dumps(r["data"], ensure_ascii=False)


def test_saving_groups_must_not_wipe_endpoint():
    """configure(groups=...) 不能顺手清掉 URL —— url 默认值写成 "" 时会被当成「清空端点」,
    实测「保存星球选择」那一下就把 api_key 抹了, 接入直接失效。"""
    z.configure(URL, [])
    z.configure(groups=[{"group_id": "1", "name": "x"}])
    assert z.endpoint_label() == "mcp.zsxq.com/topic/mcp"
    assert z.is_enabled() is True
    # 反向: 只改端点不该动已选星球
    z.configure(url="https://mcp.zsxq.com/topic/mcp?api_key=OTHER")
    assert [g["group_id"] for g in z.configured_groups()] == ["1"]
    z.configure("", [])


def test_health_count_key_does_not_collide_with_group_list():
    """路由里曾把 health() 盲展开进响应, 里面同名的计数字段把 groups 列表覆盖成整数,
    前端 picked 变数字、.find 一调就白屏。计数字段单独命名。"""
    z.configure(URL, [{"group_id": "1", "name": "x"}])
    h = z.health()
    assert "groups" not in h and h.get("group_count") == 1
    z.configure("", [])


def test_real_payload_fields_are_read(post):
    """正文/作者/计数分别在 content / owner.name / counts —— 这三处曾按 talk.text 那套猜错,
    线上拿到 0 条正文。"""
    post({"get_group_topics": {"topics_brief": [
        dict(_topic("a", "华虹宏力基本面无实际利空", _now()),
             counts={"likes": 7, "comments": 2}, digested=True)]}})
    t = z.list_topics("1", "打板复盘", days=1)[0]
    assert t["正文"] == "华虹宏力基本面无实际利空"
    assert t["作者"] == "基业长青"
    assert (t["点赞"], t["评论"]) == (7, 2)
    assert t["精华"] is True


def test_attachment_only_topic_reports_no_body(post):
    """纯文件/图片帖的 content 是「文件」这种占位串。别让模型把占位串当正文分析。"""
    post({"get_group_topics": {"topics_brief": [
        dict(_topic("a", "「文件」", _now()), files=[{"name": "研报.pdf"}] * 5)]}})
    t = z.list_topics("1", "打板复盘", days=1)[0]
    assert t["正文"] == "" and t["附件"] == "5个文件"


def test_hashtag_title_is_url_decoded(post):
    """标签 title 是整段 URL 编码的; 只替 %23 会在正文里留下 %E3%80%90 这种串(实测搜索结果里
    就这么糊着)。"""
    raw = '正文 <e type="hashtag" hid="1" title="%23%E7%AE%97%E5%8A%9B%E7%A7%9F%E8%B5%81%23" /> 收尾'
    post({"get_group_topics": {"topics_brief": [_topic("a", raw, _now())]}})
    t = z.list_topics("1", "打板复盘", days=1)[0]
    assert "#算力租赁#" in t["正文"] and "%" not in t["正文"]
