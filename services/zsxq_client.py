"""知识星球(zsxq)只读接入 —— 给 agent 补一层「人在怎么说」的观点面。

现有情绪面全是指标(涨停/炸板/赚钱效应/连板高度), 缺文本面: 盘该怎么定性、谁在讲什么逻辑。
付费社群内容在墙内, web_search 抓不到。这里通过官方 CLI(https://github.com/unnoo/zsxq-skill,
`npm i -g zsxq-cli` + `zsxq-cli auth login`)只读取回来, 作为**观点**输入。

三条设计约束(与本项目红线一致):
  1. 只读。只用 group +list / group +topics / topic +search / topic +detail 四个命令,
     发帖/评论/加精/删除一律不碰(它们对理财毫无用处, 且是账号级写操作)。
  2. 原文不落库。本模块只在内存里返回, 由调用方送进 LLM 提炼; 落 SQLite 的只该是结构化
     结论(情绪档位/关注方向), 避免把付费内容镜像成本地资料库。
  3. 观点归观点。返回值一律带 stance="opinion", agent 侧按 [星球观点] 单独一档标注,
     不作为数字依据、不转成买卖结论。

CLI 未安装 / 未登录 / 未选星球时全部静默降级(返回 None 或 error), 不影响其他功能。
"""
from __future__ import annotations
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

_CLI = "zsxq-cli"       # 可被 configure 覆盖(全局装的话就是 PATH 里这个名字)
_GROUPS: list = []      # 纳入财经流水线的星球: [{"group_id": "...", "name": "..."}]
_TIMEOUT = 25.0         # RAG 搜索偶尔慢, 给宽一点; CLI 自己也有超时


def configure(cli_path: str = "", groups: list | None = None) -> None:
    global _CLI, _GROUPS
    if cli_path:
        _CLI = cli_path
    if groups is not None:
        _GROUPS = [g for g in groups if g.get("group_id")]


def configured_groups() -> list:
    return list(_GROUPS)


def is_enabled() -> bool:
    return bool(_GROUPS)


def _run(args: list) -> dict:
    """跑一条只读命令, 统一成 {"ok": bool, "data"|"error"}。

    CLI 的 --json 输出是带 ok 字段的信封(未登录时实测:
    {"ok": false, "error": {"type": "auth", "message": "not logged in", ...}});
    成功时的层级各命令不同, 交给各自的取数函数按候选键找。
    """
    try:
        p = subprocess.run([_CLI, *args, "--json"], capture_output=True, text=True,
                           timeout=_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "error": {"type": "not_installed",
                                       "message": f"{_CLI} 未安装", "hint": "npm i -g zsxq-cli"}}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"type": "timeout", "message": f"{_CLI} 超时"}}
    # 实测: 失败时 CLI 把 {"ok":false,"error":{...}} 写到 **stderr**, stdout 是空的。
    # 只读 stdout 会把「未登录」错认成「无输出」, 拿不到 type=auth 与 hint, 设置页就没法
    # 提示用户去登录 —— 两条都试着解析。
    raw = (p.stdout or "").strip()
    err_raw = (p.stderr or "").strip()
    for candidate in (raw, err_raw):
        if not candidate:
            continue
        try:
            d = json.loads(candidate)
            break
        except json.JSONDecodeError:
            d = None
    else:
        return {"ok": False, "error": {"type": "empty", "message": err_raw[:200] or "无输出"}}
    if d is None:
        # 不带 --json 也能跑的命令偶尔混进表格输出, 视作失败而不是硬崩
        return {"ok": False, "error": {"type": "parse", "message": (raw or err_raw)[:200]}}
    if isinstance(d, dict) and d.get("ok") is False:
        return d
    if isinstance(d, dict) and "ok" in d:
        return {"ok": True, "data": d.get("data", d)}
    return {"ok": True, "data": d}      # 裸对象/数组也接受


def _dig(obj, *keys):
    """从可能多层嵌套的返回里捞第一个命中的键(CLI 各命令层级不统一, 别写死路径)。"""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
        for v in obj.values():
            got = _dig(v, *keys)
            if got is not None:
                return got
    return None


def list_groups(limit: int = 50) -> dict:
    """列出该账号加入/创建的全部星球(选哪些进流水线由用户在设置里勾)。"""
    r = _run(["group", "+list", "--limit", str(int(limit))])
    if not r.get("ok"):
        return r
    arr = _dig(r["data"], "groups") or (r["data"] if isinstance(r["data"], list) else [])
    out = []
    for g in arr if isinstance(arr, list) else []:
        if not isinstance(g, dict):
            continue
        gid = g.get("group_id") or g.get("id")
        if gid is None:
            continue
        out.append({"group_id": str(gid), "name": g.get("name") or str(gid),
                    "type": g.get("type") or ""})
    return {"ok": True, "groups": out}


# 主题正文散在 talk/question/answer/solution 几个容器里, 逐个试
_TEXT_KEYS = ("talk", "question", "answer", "solution")
_TAG_RE = re.compile(r"<[^>]+>")
_HASHTAG_RE = re.compile(r"<e[^>]*type=\"hashtag\"[^>]*title=\"([^\"]*)\"[^>]*/?>")


def _clean(text: str) -> str:
    """去掉 zsxq 正文里的 <e .../> 富文本标记, 话题标签还原成 #名字。"""
    if not text:
        return ""
    s = _HASHTAG_RE.sub(lambda m: m.group(1).replace("%23", "#"), text)
    s = _TAG_RE.sub("", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _norm_topic(t: dict, group_name: str = "") -> dict | None:
    if not isinstance(t, dict):
        return None
    tid = t.get("topic_id") or t.get("id")
    if tid is None:
        return None
    body, author = "", ""
    for k in _TEXT_KEYS:
        blk = t.get(k)
        if isinstance(blk, dict):
            body = _clean(blk.get("text") or "")
            author = ((blk.get("owner") or {}) or {}).get("name") or ""
            if body:
                break
    return {
        "topic_id": str(tid),
        "星球": group_name,
        "作者": author,
        "类型": t.get("type") or "",
        "标题": t.get("title") or "",
        "正文": body,
        "时间": str(t.get("create_time") or "")[:19].replace("T", " "),
        "点赞": t.get("likes_count"),
        "评论": t.get("comments_count"),
        "stance": "opinion",          # 观点, 不是事实 —— agent 按 [星球观点] 标注
    }


def _topics_page(group_id: str, limit: int, end_time: str = "") -> tuple[list, str]:
    args = ["group", "+topics", "--group-id", str(group_id), "--limit", str(int(limit))]
    if end_time:
        args += ["--end-time", end_time]
    r = _run(args)
    if not r.get("ok"):
        return [], ""
    data = r["data"]
    arr = _dig(data, "topics") or (data if isinstance(data, list) else [])
    nxt = _dig(data, "next_end_time") or ""
    return (arr if isinstance(arr, list) else []), (nxt if isinstance(nxt, str) else "")


def list_topics(group_id: str, group_name: str = "", days: int = 1,
                max_items: int = 40) -> list:
    """取某星球最近 days 天的主题。

    翻页游标 next_end_time **含等于**(官方 reference 明确写了), 下一页会把上一页最后一条
    重复返回为首条 —— 必须按 topic_id 去重, 否则累计计数会虚高。
    """
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=max(1, int(days)))
    seen, out, end_time = set(), [], ""
    for _ in range(6):                      # 单页最多 30, 6 页够覆盖一周高频星球
        page, nxt = _topics_page(group_id, min(30, max_items), end_time)
        if not page:
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
        if stale or not nxt or len(out) >= max_items:
            break
        end_time = nxt
    return out[:max_items]


def _parse_time(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).astimezone()
        except (ValueError, TypeError):
            continue
    return None


def search_topics(query: str, group_id: str = "", group_name: str = "") -> list:
    """星球内全文搜索(服务端 RAG, 语义匹配)。

    官方 reference 提醒: 无翻页、结果数量由服务端定、会漏召也会误召, 所以这里只当线索,
    相关性由调用方(和模型)自己判, 不当权威检索。
    """
    if not query:
        return []
    r = _run(["topic", "+search", "--group-id", str(group_id), "--query", query])
    if not r.get("ok"):
        return []
    arr = _dig(r["data"], "topics") or (r["data"] if isinstance(r["data"], list) else [])
    out = []
    for t in arr if isinstance(arr, list) else []:
        n = _norm_topic(t, group_name)
        if n:
            out.append(n)
    return out


def topic_detail(topic_id: str, group_name: str = "") -> dict | None:
    r = _run(["topic", "+detail", "--topic-id", str(topic_id)])
    if not r.get("ok"):
        return None
    t = _dig(r["data"], "topic") or r["data"]
    return _norm_topic(t if isinstance(t, dict) else {}, group_name)


def health() -> dict:
    """设置页/启动日志用: CLI 在不在、登录没登录、选了几个星球。不打印任何 token。"""
    r = _run(["group", "+list", "--limit", "1"])
    if r.get("ok"):
        return {"installed": True, "logged_in": True, "groups": len(_GROUPS)}
    err = (r.get("error") or {})
    return {"installed": err.get("type") != "not_installed",
            "logged_in": False, "groups": len(_GROUPS),
            "error": err.get("message") or "", "hint": err.get("hint") or ""}
