"""知识星球(zsxq)只读接入 —— 给 agent 补一层「人在怎么说」的观点面。

现有情绪面全是指标(涨停/炸板/赚钱效应/连板高度), 缺文本面: 盘该怎么定性、谁在讲什么逻辑。
付费社群内容在墙内, web_search 抓不到。这里通过官方 MCP 端点(https://mcp.zsxq.com/topic/mcp,
api_key 拼在 URL 上)只读取回来, 作为**观点**输入。

为什么走 MCP 而不是官方 CLI(npm i -g zsxq-cli):
  · 不用装全局 npm 包、不依赖系统 Keychain, 别人 self-host 填个 URL 就能跑
  · 接口面更全 —— get_group_topics 带 scope=by_owner(只要星主及合伙人的帖, 不含成员闲聊),
    还有按标签拉(get_hashtag_topics)、追某个作者(get_user_footprints)、评论区(get_topic_comments),
    这些 CLI 都没有。信噪比差别很大: 一个复盘星球里成员闲聊能占九成
  · 错误是结构化的, 不用兼容「错误信封写 stderr」这类坑

三条设计约束(与本项目红线一致):
  1. 只读。远端 21 个工具里有 create_topic/create_topic_comment/set_topic_digested/call_zsxq_api
     这些写口, 一个都不碰(对理财无用, 且是账号级写操作)。白名单在 _READ_TOOLS。
  2. 原文不落库。本模块只在内存里返回, 由调用方送进 LLM 提炼; 落 SQLite 的只该是结构化
     结论, 避免把付费内容镜像成本地资料库。
  3. 观点归观点。返回一律带 stance="opinion", agent 侧按 [星球观点] 单独一档标注,
     不作为数字依据、不转成买卖结论。

URL 里带 api_key, 所以: 不打印、不返回给前端(一律脱敏成 host)、存 DB 不进 config.py。
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

from services import mcp_http

_URL = ""               # https://mcp.zsxq.com/topic/mcp?api_key=... (含凭证, 别外泄)
_GROUPS: list = []      # 纳入财经流水线的星球: [{"group_id": "...", "name": "...", "owner_only": bool}]

# 只读白名单 —— 不在这张表里的远端工具一律不调
_READ_TOOLS = frozenset({
    "get_self_info", "get_user_groups", "get_group_topics", "get_topic_info",
    "get_topic_comments", "get_group_hashtags", "get_hashtag_topics", "get_user_footprints",
    "search_topics", "search_groups",
})


def configure(url: str | None = None, groups: list | None = None) -> None:
    """两个参数都用 None 当「本次不改」的哨兵。

    别把 url 的默认值写成 "" —— configure(groups=[...]) 会把默认空串当成「清空端点」,
    保存星球选择的那一下就把 api_key 抹了(实测踩过: 存完 groups 后 configured 变 False)。
    """
    global _URL, _GROUPS
    if url is not None:
        _URL = (url or "").strip()
    if groups is not None:
        _GROUPS = [g for g in groups if g.get("group_id")]


def configured_groups() -> list:
    return list(_GROUPS)


def is_enabled() -> bool:
    return bool(_URL and _GROUPS)


def endpoint_label() -> str:
    """给前端/日志看的脱敏标识: 只留 host+path, 绝不带 api_key。"""
    if not _URL:
        return ""
    try:
        u = urlparse(_URL)
        return f"{u.hostname or ''}{u.path or ''}"
    except ValueError:
        return "已配置"


def _call(name: str, args: dict | None = None) -> dict:
    if name not in _READ_TOOLS:
        return {"ok": False, "error": {"type": "forbidden", "message": f"{name} 不在只读白名单"}}
    if not _URL:
        return {"ok": False, "error": {"type": "not_configured",
                                       "message": "未配置知识星球 MCP 端点",
                                       "hint": "设置 → 知识星球 填入带 api_key 的 URL"}}
    return mcp_http.call_tool(_URL, name, args or {})


def _dig(obj, *keys):
    """从可能多层嵌套的返回里捞第一个命中的键(远端各工具层级不统一, 别写死路径)。"""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
        for v in obj.values():
            got = _dig(v, *keys)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig(v, *keys)
            if got is not None:
                return got
    return None


def self_info() -> dict:
    r = _call("get_self_info")
    if not r.get("ok"):
        return r
    uid = _dig(r["data"], "user_id", "uid", "id")
    name = _dig(r["data"], "name", "nickname") or ""
    return {"ok": True, "user_id": str(uid) if uid is not None else "", "name": name}


def list_groups(limit: int = 50) -> dict:
    """列出该账号加入的全部星球(选哪些进流水线由用户在设置里勾)。

    get_user_groups 要 user_id, 先用 get_self_info 拿。
    """
    me = self_info()
    if not me.get("ok"):
        return me
    if not me.get("user_id"):
        return {"ok": False, "error": {"type": "parse", "message": "取不到 user_id"}}
    r = _call("get_user_groups", {"user_id": me["user_id"], "limit": int(limit), "scope": "all"})
    if not r.get("ok"):
        return r
    arr = _dig(r["data"], "groups") or (r["data"] if isinstance(r["data"], list) else [])
    out = []
    for g in arr if isinstance(arr, list) else []:
        if not isinstance(g, dict):
            continue
        inner = g.get("group") if isinstance(g.get("group"), dict) else g
        gid = inner.get("group_id") or inner.get("id")
        if gid is None:
            continue
        out.append({"group_id": str(gid), "name": inner.get("name") or str(gid),
                    "type": inner.get("type") or ""})
    return {"ok": True, "groups": out, "me": me.get("name") or ""}


# 实测真实返回(get_group_topics / get_topic_info 同一套形态):
#   列表键是 topics_brief(不是 topics); 每项是**平铺**结构:
#     content=正文(实测 brief 里就是全文, 与 get_topic_info 的 content 逐条等长, 无需再拉详情)
#     owner={name,alias,user_id}=作者(不在 talk 里)  counts={likes,comments,readers,...}
#     digested=是否精华  images[]/files[]=附件  group={group_id,name}
#   纯文件/图片帖的 content 是「文件」「图片」这种占位串, 没有可读正文。
# 老式 talk/question/answer 嵌套形态留作兜底(万一端点换形状)。
_TEXT_KEYS = ("talk", "question", "answer", "solution")
_TAG_RE = re.compile(r"<[^>]+>")
_HASHTAG_RE = re.compile(r"<e[^>]*type=\"hashtag\"[^>]*title=\"([^\"]*)\"[^>]*/?>")
_PLACEHOLDER = ("「文件」", "「图片」", "「视频」", "「语音」")


def _clean(text: str) -> str:
    """去掉 zsxq 正文里的 <e .../> 富文本标记, 话题标签还原成 #名字。

    标签的 title 是**整段 URL 编码**的(实测 title="%23%E3%80%90...%23"), 只把 %23 换成 #
    会留下一串 %E3%80%90 糊在正文里 —— 要整段 unquote。
    """
    if not text:
        return ""
    s = _HASHTAG_RE.sub(lambda m: unquote(m.group(1)), text)
    s = _TAG_RE.sub("", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _norm_topic(t: dict, group_name: str = "") -> dict | None:
    if not isinstance(t, dict):
        return None
    tid = t.get("topic_id") or t.get("id")
    if tid is None:
        return None
    body = _clean(t.get("content") or "")
    owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
    author = owner.get("name") or owner.get("alias") or ""
    if not body:                     # 兜底: 老式嵌套形态
        for k in _TEXT_KEYS:
            blk = t.get(k)
            if isinstance(blk, dict):
                body = _clean(blk.get("text") or "")
                author = author or ((blk.get("owner") or {}) or {}).get("name") or ""
                if body:
                    break
    counts = t.get("counts") if isinstance(t.get("counts"), dict) else {}
    n_img, n_file = len(t.get("images") or []), len(t.get("files") or [])
    out = {
        "topic_id": str(tid),
        "星球": group_name or ((t.get("group") or {}) or {}).get("name") or "",
        "作者": author,
        "类型": t.get("type") or "",
        "标题": t.get("title") or "",
        "正文": body,
        "时间": str(t.get("create_time") or "")[:19].replace("T", " "),
        "点赞": counts.get("likes", t.get("likes_count")),
        "评论": counts.get("comments", t.get("comments_count")),
        "stance": "opinion",          # 观点, 不是事实 —— agent 靠它打 [星球观点]
    }
    if t.get("digested"):
        out["精华"] = True
    # 纯附件帖: 说清「没有可读正文」, 别让模型把占位串当内容分析
    if (n_img or n_file) and (not body or body in _PLACEHOLDER):
        out["附件"] = f"{n_file}个文件" if n_file else f"{n_img}张图"
        out["正文"] = ""
    return out


def _parse_time(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).astimezone()
        except (ValueError, TypeError):
            continue
    return None


def list_topics(group_id: str, group_name: str = "", days: int = 1,
                max_items: int = 40, owner_only: bool = False) -> list:
    """取某星球最近 days 天的主题。

    owner_only=True 走 scope=by_owner(星主及合伙人)。**默认关**: 实测「短评&信息」这个星球
    by_owner 只回三月份的两条图片帖, 而 scope=all 才是当天真正有价值的研报摘要 —— 发帖人
    不一定被算成"星主/合伙人"。所以默认全量, 要收窄再逐个星球开。

    翻页游标是上一页最后一条的 create_time 且**含等于**(官方文档写明), 下一页会把上一页
    最后一条重复返回为首条 —— 必须按 topic_id 去重, 否则「今天几条」会虚高。
    """
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=max(1, int(days)))
    seen, out, end_time = set(), [], ""
    for _ in range(6):                      # 单页最多 30, 6 页够覆盖一周高频星球
        args = {"group_id": str(group_id), "limit": min(30, max(1, max_items)),
                "scope": "by_owner" if owner_only else "all"}
        if end_time:
            args["end_time"] = end_time
        r = _call("get_group_topics", args)
        if not r.get("ok"):
            break
        page = _dig(r["data"], "topics_brief", "topics") or (r["data"] if isinstance(r["data"], list) else [])
        if not isinstance(page, list) or not page:
            break
        stale = False
        for t in page:
            n = _norm_topic(t, group_name)
            if not n or n["topic_id"] in seen:
                continue
            seen.add(n["topic_id"])
            ts = _parse_time(n["时间"])
            if ts and ts < cutoff:
                stale = True
                continue
            out.append(n)
        nxt = _dig(r["data"], "next_end_time") or (page[-1].get("create_time") if isinstance(page[-1], dict) else "")
        if stale or not nxt or len(out) >= max_items:
            break
        end_time = str(nxt)
    return out[:max_items]


def search_topics(query: str, group_id: str = "", group_name: str = "") -> list:
    """星球内全文搜索(服务端 RAG, 语义匹配)。

    官方文档提醒: 无翻页、结果数量由服务端定、会漏召也会误召, 所以这里只当线索,
    相关性由调用方(和模型)自己判, 不当权威检索。
    """
    if not query:
        return []
    r = _call("search_topics", {"group_id": str(group_id), "query": query})
    if not r.get("ok"):
        return []
    arr = _dig(r["data"], "topics_brief", "topics") or (r["data"] if isinstance(r["data"], list) else [])
    return [n for n in (_norm_topic(t, group_name) for t in (arr if isinstance(arr, list) else []))
            if n]


def topic_detail(topic_id: str, group_name: str = "", with_comments: bool = False) -> dict | None:
    """主题详情。with_comments=True 连评论一起取 —— 博主常在评论里补充/修正正文的判断。"""
    r = _call("get_topic_info", {"topic_id": str(topic_id)})
    if not r.get("ok"):
        return None
    t = _dig(r["data"], "topic") or r["data"]
    n = _norm_topic(t if isinstance(t, dict) else {}, group_name)
    if n and with_comments:
        c = _call("get_topic_comments", {"topic_id": str(topic_id), "limit": 20})
        arr = _dig(c.get("data"), "comments") if c.get("ok") else None
        n["评论摘录"] = [
            {"作者": ((x.get("owner") or {}) or {}).get("name") or "",
             "内容": _clean(x.get("text") or "")}
            for x in (arr or []) if isinstance(x, dict)
        ][:20]
    return n


def health() -> dict:
    """设置页/启动日志用: 端点通不通、凭证有效否、选了几个星球。不返回任何含 key 的串。"""
    if not _URL:
        return {"configured": False, "ok": False, "group_count": len(_GROUPS),
                "error": "未配置 MCP 端点"}
    me = self_info()
    if me.get("ok"):
        return {"configured": True, "ok": True, "group_count": len(_GROUPS),
                "endpoint": endpoint_label(), "account": me.get("name") or ""}
    err = me.get("error") or {}
    return {"configured": True, "ok": False, "group_count": len(_GROUPS),
            "endpoint": endpoint_label(),
            "error": err.get("message") or "", "hint": err.get("hint") or ""}
