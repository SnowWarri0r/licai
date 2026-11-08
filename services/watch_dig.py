"""盯盘员深挖: 发现出来之后, 判断值不值得查原因, 值得就派一个后台子 agent 去查, 查到补充才挂到那条发现下面。

两级过滤:
  快路径(程序): 检测器在 DIGGABLE 里、当天自动深挖未满 DAILY_CAP、这条还没考虑过。
  慢路径(模型): 一次调用把这批候选交给轻量模型(llm_client 的 fast 别名), 逐条判定查不查、一句话理由。
深挖: agent_tasks.spawn 起一个不挂会话的子 agent(独立上下文、只读数据工具), 回答第一行写
  【有补充】或【无补充】。有补充 → dig_status=done, 正文进 dig_result, 页面在那条发现下展示;
  无补充 → silent, 页面不显示任何东西(查不出就不出声)。
用户也可以在页面上对单条发现点「查原因」(dig_now), 跳过两级过滤与每日上限。
服务重启时 queued/running 的深挖标成 interrupted, 不自动重跑。
"""
from __future__ import annotations

import asyncio
import json
import re

from database import get_db
from services import agent_tasks

DIGGABLE = ("risk_hits", "fund_est_miss", "fund_drop", "unlock_soon", "tail_pull", "sealed_close")
DAILY_CAP = 4
_WAIT_S = 15 * 60

_HINTS = {
    "risk_hits": "逐项说明各排雷标记为什么命中: 查近 20 日走势与量能(get_trend)、近 10 天龙虎榜席位、近期公告"
                 "(定增、减持、问询、业绩预告), 判断命中来自一次性事件还是持续的趋势。",
    "fund_est_miss": "说明估值与公布净值差在哪里: 用 get_etf_xray(基金代码) 看最新季报前十大与行业分布, "
                     "对照当日相关指数和前十大个股的实际涨跌, 指出偏差来自前十大以外的持仓、季报后的调仓、汇率还是 QDII 时差。",
    "fund_drop": "说明这段下跌来自哪部分: 用 get_etf_xray(基金代码) 看持仓结构, 对照同期跟踪市场的指数、主要持仓、"
                 "汇率与相关新闻, 区分市场整体、行业、个别重仓股三种来源。",
    "unlock_soon": "查这次解禁的股东类型与持股成本、股东近期减持或承诺公告(get_shareholders、get_announcements), "
                   "以及解禁规模相对日均成交额的大小。",
    "tail_pull": "查当日公告与新闻、所属板块当日尾盘是否同步拉升、资金流, 说明尾盘拉升的触发因素。",
    "sealed_close": "查当日公告与新闻、所属概念与板块联动、资金流, 收盘后的龙虎榜已公布时一并查看, 说明封板的触发因素。",
}

_JUDGE_SYS = ("你是盯盘员的调度员, 只做一件事: 判断每条提醒是否值得派研究员去查原因。用户只想看到查得出东西的补充。\n"
              "值得查: 提醒本身没交代原因, 而原因能用行情、公告、新闻、龙虎榜、基金持仓等数据查清, 且查清后会改变用户对这只标的的理解。\n"
              "不必查: 提醒文字已说明原因; 内容是固定日程或机械事实; 同一标的今天已查过。\n"
              '只输出 JSON: {"items": [{"id": 提醒id, "dig": true 或 false, "why": "15 字以内的理由"}]}')


def brief_for(f: dict) -> str:
    ev = f.get("evidence") or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev or "{}")
        except ValueError:
            ev = {}
    who = f"{f.get('name') or ''}({f['code']})" if f.get("code") else "提醒里列出的基金"
    return (f"盯盘员 {f.get('date') or ''} 记下一条提醒, 请查清它背后的原因。\n"
            f"【提醒】{f['title']}\n{f.get('body') or ''}\n【标的】{who}\n"
            f"【证据】{json.dumps(ev, ensure_ascii=False)[:600]}\n"
            f"【查法】{_HINTS.get(f['detector'], '查相关行情、公告与新闻, 说明这条提醒的成因。')}\n"
            "【交回格式】第一行只写【有补充】或【无补充】。查到了提醒文字之外的具体成因或新事实写【有补充】, "
            "接着用 300 字以内说明, 结论在前, 每个数字注明来源与日期; 工具没给出提醒之外的信息、或成因定不下来写【无补充】, "
            "接着一句话说明查了什么。")


def parse_result(text: str) -> tuple[str, str]:
    """子 agent 的回答 → (dig_status, 展示正文)。"""
    t = (text or "").strip()
    if not t:
        return "failed", ""
    first, _, rest = t.partition("\n")
    if "无补充" in first:
        return "silent", rest.strip()
    if "有补充" in first:
        return "done", rest.strip() or t
    return "done", t                        # 没按格式写: 有内容就当补充展示


def parse_judgement(text: str) -> dict[int, tuple[bool, str]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        items = json.loads(m.group(0)).get("items") or []
    except ValueError:
        return {}
    out = {}
    for it in items:
        try:
            out[int(it["id"])] = (bool(it.get("dig")), str(it.get("why") or "")[:40])
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def _set(fid: int, **cols) -> None:
    db = await get_db()
    try:
        await db.execute(f"UPDATE watch_finding SET {', '.join(k + ' = ?' for k in cols)} WHERE id = ?", (*cols.values(), fid))
        await db.commit()
    finally:
        await db.close()


async def _rows(where: str, args: tuple) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(f"SELECT * FROM watch_finding WHERE {where}", args)
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


def _judge_sync(cands: list[dict], dug_today: list[dict]) -> dict[int, tuple[bool, str]]:
    from services import llm_client
    lines = [f"[id={f['id']}] {f['title']}\n{f.get('body') or ''}" for f in cands]
    done = "、".join(sorted({f.get("name") or f.get("title") for f in dug_today})) or "无"
    prompt = "今天已查过的标的: " + done + "\n\n待判定的提醒:\n\n" + "\n\n".join(lines)
    return parse_judgement(llm_client.call_claude(prompt, _JUDGE_SYS, "fast", 800))


async def consider(findings: list[dict]) -> list[int]:
    """watcher 新记下的发现 → 两级过滤 → 派深挖。返回派出的发现 id。"""
    keys = [f["key"] for f in findings if f.get("detector") in DIGGABLE]
    if not keys:
        return []
    rows = await _rows(f"key IN ({','.join('?' * len(keys))}) AND dig_status = '' AND dismissed = 0", tuple(keys))
    if not rows:
        return []
    today = rows[0]["date"]
    dug = await _rows("date = ? AND dig_status NOT IN ('', 'skipped') AND dig_reason <> '用户要求'", (today,))
    room = DAILY_CAP - len(dug)
    if room <= 0:
        for r in rows:
            await _set(r["id"], dig_status="skipped", dig_reason=f"今天已自动查过 {DAILY_CAP} 条")
        return []
    try:
        verdict = await asyncio.to_thread(_judge_sync, rows, dug)
    except Exception as e:  # noqa: BLE001
        print(f"[watch-dig] 判定失败: {e}")
        return []                           # 保持 '' : 下次有新发现时一起再判
    out = []
    for r in rows:
        dig, why = verdict.get(r["id"], (False, "未判定"))
        if dig and len(out) < room:
            await start(r, why)
            out.append(r["id"])
        else:
            await _set(r["id"], dig_status="skipped", dig_reason=why if not dig else "超出每日上限")
    return out


async def start(row: dict, reason: str) -> int:
    tid = await agent_tasks.spawn(("查原因 " + (row.get("name") or row["detector"]))[:20], brief_for(row), True)
    await _set(row["id"], dig_status="queued", dig_reason=reason, dig_task_id=tid)
    asyncio.create_task(_collect(row["id"], tid))
    return tid


async def _collect(fid: int, tid: int) -> None:
    await agent_tasks.wait([tid], _WAIT_S)
    v = await agent_tasks.view(tid) or {}
    if v.get("status") in ("queued", "running"):
        agent_tasks.cancel(tid)
        await _set(fid, dig_status="failed", dig_result="")
        return
    status, text = parse_result(v.get("result") or "") if v.get("status") == "done" else ("failed", "")
    await _set(fid, dig_status=status, dig_result=text)


async def dig_now(fid: int) -> dict:
    """页面上点「查原因」: 跳过判定与每日上限。正在查或已查过的不重复派。"""
    rows = await _rows("id = ?", (fid,))
    if not rows:
        return {"error": "没有这条发现"}
    r = rows[0]
    if r["detector"] not in DIGGABLE:
        return {"error": "这类提醒不需要查原因"}
    if r["dig_status"] in ("queued", "running", "done", "silent"):
        return {"status": r["dig_status"]}
    await start(r, "用户要求")
    return {"status": "queued"}


async def interrupt_stale() -> int:
    db = await get_db()
    try:
        cur = await db.execute("UPDATE watch_finding SET dig_status = 'interrupted' WHERE dig_status IN ('queued', 'running')")
        await db.commit()
        return cur.rowcount or 0
    finally:
        await db.close()
