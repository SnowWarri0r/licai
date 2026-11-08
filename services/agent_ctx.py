"""agent 执行上下文: 当前在跑的是哪一轮(run)、处在第几层(主 agent / 子 agent)。

用 ContextVar 而不是函数参数: 问答 agent、多视角深挖、子任务都走同一条 ask_stock_stream,
上下文跟着 asyncio task 走(create_task / gather 会复制当前上下文), 不用改各条链的签名。

run: {"run_id", "session_id", "take_inbox": () -> [{id, text}], "spawned": int} | None
     take_inbox 取走这一轮收件箱里用户执行期间补充的消息; spawned 记这一轮派出的子任务数。
depth: 0 = 用户直接对话的主 agent; 1 = 主 agent 派出的子 agent。
untrusted: 这个子 agent 的任务说明里带着第三方原文(公告正文等)。这时只给公开数据工具:
     不能联网/读网页(没有往外发请求的通道), 也不能读用户自己的持仓、成交与记录(没有可带出的数据)。
"""
from __future__ import annotations

from contextvars import ContextVar

run: ContextVar[dict | None] = ContextVar("agent_run", default=None)
depth: ContextVar[int] = ContextVar("agent_depth", default=0)
untrusted: ContextVar[bool] = ContextVar("agent_untrusted", default=False)


def take_inbox() -> list[dict]:
    r = run.get()
    fn = (r or {}).get("take_inbox")
    return fn() if fn else []
