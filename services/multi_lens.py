"""多视角对抗: 四层视角在**同一份事实**上各自成文, 再挑出彼此的冲突点。

**为什么不是"看多/看空"**: 那两个角色不告诉模型去查什么, 容易两边都在扯估值。视角要带
**检查项**才有执行力 —— 所以这里直接复用项目里已经落地的「生意质量四层框架」(商业模式 /
护城河+毛利ROE佐证+同行印证 / 盈利质量持续性 / 价格与价值分开)加红线清单, 不另发明一套。
方法论的价值就在这儿: 它规定了看什么, 而且四层天然不重叠。

**为什么不是让一个模型一次写完四层**: 那是现在单轮就在做的事。一个模型自己写"正反两面"
会把分歧平均成糊 —— 它没有动机跟自己吵。分开跑, 分歧才留得住。

**比 ai-berkshire 严一处: 四个视角共享同一份 fact pack。** 他们那四个后台 agent 各自
WebSearch, 取到的数据都不一样, 于是**分歧可能只是取数的分歧, 不是视角的分歧** —— 那种
"冲突"没有信息量。这里先取一份事实, 四层在同一份事实上吵, 冲突才归因得到视角本身。

**每层必须自报失效条件。** 只给结论的四份报告拼起来还是四段描述; 要求每层写清"这条在什么
条件下不成立", 综合那一步才有东西可碰 —— 输出的是分歧点与证伪条件, 而不是又一份综述。

**护栏没松**: 各层与综合都不出买卖/仓位/目标价/评分。可判定的事(量价、护城河有无佐证、
盈利质量、估值在历史区间的位置、红线命中)果断下结论; 未来价格与操作决策留给用户。
ai-berkshire 那条流水线的终点是"四维评分 + Checklist + 最终投资建议", 这一段刻意不搬。
"""
from __future__ import annotations
import asyncio
import json as _json

_MODEL_LENS = "claude-sonnet-5"     # 四层并行, 用均衡档
_MODEL_SYNTH = "claude-sonnet-5"
_MAX_TOK_LENS = 1800
_MAX_TOK_SYNTH = 2200

# fact pack: 名字 → (工具名, 参数)。四层共用这一份, 谁都不许再自己取数或凭记忆补。
def _plan(code: str) -> dict:
    return {
        "实时行情": ("get_quote", {"code": code}),
        "近60日裸K量价与结构": ("get_trend", {"code": code, "days": 60}),
        "公司画像与主营构成": ("get_company_profile", {"code": code}),
        "基本面与估值": ("get_fundamentals", {"code": code}),
        "同行对照": ("get_peers", {"code": code}),
        "客观红线": ("get_red_flags", {"code": code}),
        "所属概念题材": ("get_stock_concepts", {"code": code}),
        "筹码与解禁": ("get_shareholders", {"code": code}),
    }


_LENSES = (
    {"key": "生意", "name": "生意与护城河",
     "checklist": "①这门生意怎么赚钱: 用主营构成一句话说清商业模式与钱从谁手里赚; 绕到看不懂就直说"
                  "'复杂生意, 超出可简单理解的范围'。②护城河有没有、是哪一种: 定价权(毛利率高且多年稳)/"
                  "成本优势/品牌无形资产/转换成本/网络效应/牌照资源 —— 每条都要有数字或同行对照佐证, "
                  "**找不到佐证就写'未见明显护城河, 属同质化竞争'**, 不许用形容词凑。"},
    {"key": "数", "name": "盈利质量与估值位置",
     "checklist": "①ROE 的水平**与多年持续性**(一年高不算数); ②增长靠内生还是靠杠杆/并购堆出来; "
                  "③现金流与利润是否匹配; ④估值只说**位置**: PE/PB 放进该股自身历史区间与同行水平对照, "
                  "表述成'当前处于什么位置'。**不给内在价值、不给目标价、不给三情景推演。**"},
    {"key": "局", "name": "行业格局与同行",
     "checklist": "①它在同行里排第几、差距是多少(用同行对照的数字说); ②这个格局是在变好还是变差, "
                  "依据是什么; ③当下资金在追的题材跟它是什么关系(沾主线还是被动跟随); "
                  "④这门生意十年后最可能被什么改变。"},
    {"key": "雷", "name": "红线与筹码",
     "checklist": "①红线清单里命中了什么(带公告日期), 没命中的也说'已扫描未命中'; ②筹码面: 十大流通"
                  "股东在加还是减、北向变动、未来解禁抛压占流通多少; ③把这些风险**按可能性与影响排个序**, "
                  "别只罗列。这一层的职责是**找问题**, 不要替它辩护。"},
)

_LENS_SYSTEM = (
    "你是一位只负责单一层面的分析者, 层面是「{name}」。这一层之外的事一句都不要写 —— "
    "别人在写别的层, 你越界只会制造重复。\n"
    "你这一层的检查项: {checklist}\n\n"
    "【只能用给你的事实】下面的 fact pack 是全部可用材料。**严禁**用训练记忆补充任何数字、事件或时间。"
    "材料里没有的、或标了取数失败的, 一律写进「这一层缺什么」, 不许绕过去当作没发生。\n"
    "【必须自报失效条件】每条结论后面要跟一句「这条在什么条件下就不成立」—— 这是本次分析最重要的"
    "产出之一, 没有它, 四层拼起来只是四段描述。\n"
    "【客观但不含糊】可判定的事(量价事实、护城河有无佐证、盈利质量、估值在历史区间的位置、红线命中)"
    "果断下结论, 别用'可能/或许'和稀泥。只有两类保持开放: ①未来价格怎么走 ②该不该买卖。\n"
    "【硬护栏】不给买卖建议、不给仓位、不给目标价、不给评分。\n\n"
    "输出格式(Markdown, 不要寒暄):\n"
    "**结论**: 一到两句, 果断\n"
    "**支撑事实**: 逐条, 每条带上它来自 fact pack 的哪一项\n"
    "**失效条件**: 逐条, 「若 X 则上面第 N 条不成立」\n"
    "**这一层缺什么**: 缺的数据 / 取数失败的项; 没有就写「无」"
)

_SYNTH_SYSTEM = (
    "你拿到四份**互不相同层面**的分析, 它们看的是同一份事实。你的任务**不是**再写一份综述 —— "
    "综述没有价值, 四份原文已经在那儿了。你只做三件事:\n"
    "1. **挑冲突**: 四份之间哪里互相矛盾、哪里同一个事实被读出了不同含义。逐条摆出来, 指明是谁跟谁冲突。"
    "真的没有冲突就明说「四层没有实质冲突」, 不要为了凑数编分歧。\n"
    "2. **分开事实与假设**: 把支撑各层结论的东西分成两堆 —— 「可验证的事实(带来源)」与「未经验证的假设」。"
    "假设被当成事实用是最常见的错。\n"
    "3. **给每条假设标证伪条件**: 「要看到什么, 这条假设就算被推翻」。可观测、可等待, 别写成空话。\n\n"
    "最后补一段「**还缺什么**」: 四层各自报的缺口汇总, 以及这些缺口把结论的可靠程度限制到什么地步。\n\n"
    "【硬护栏】不给买卖建议、不给仓位、不给目标价、不给评分、不给综合结论式的'值不值得买'。"
    "可判定的事果断说, 未来价格与操作决策留给用户。\n"
    "输出用 Markdown, 四个小节: 分歧点 / 可验证的事实 / 未经验证的假设(附证伪条件) / 还缺什么"
)


async def _fact_pack(code: str) -> tuple[str, list[str]]:
    """取一份共享事实。返回 (喂给模型的文本, 失败项列表)。

    失败项要显式带进 prompt —— 不然模型看不出缺口, 会拿记忆把那块圆掉(与 stock_agent 里
    「取数失败必须自曝」同一条约束)。
    """
    from services.stock_agent import _EXECUTORS
    plan = _plan(code)
    keys = list(plan)
    outs = await asyncio.gather(
        *[_EXECUTORS[plan[k][0]](plan[k][1]) for k in keys], return_exceptions=True)
    parts, missing = [], []
    for k, out in zip(keys, outs):
        if isinstance(out, Exception):
            missing.append(f"{k}(异常: {type(out).__name__})")
            continue
        if isinstance(out, dict) and out.get("error"):
            missing.append(f"{k}({str(out['error'])[:60]})")
            continue
        try:
            body = _json.dumps(out, ensure_ascii=False, default=str)
        except Exception:
            body = str(out)
        parts.append(f"### {k}\n{body[:6000]}")
    if missing:
        parts.append("### 取数失败的项(不许用记忆补)\n" + "\n".join(f"- {m}" for m in missing))
    return "\n\n".join(parts), missing


def render(r: dict) -> str:
    """结果 → 给人看的 Markdown。综合放最前(那是新增的信息), 四层原文折叠在后面备查。"""
    if not r.get("可用"):
        return f"深挖没跑起来: {r.get('note') or '未知原因'}" + (
            f"\n\n缺口: {', '.join(r.get('缺口') or [])}" if r.get("缺口") else "")
    out = [f"## {r.get('名称') or ''}({r.get('代码')}) · 四层交叉", ""]
    if r.get("综合"):
        out += [r["综合"], ""]
    out += ["---", "", "### 四层原文", ""]
    for i, l in enumerate(r.get("各层") or []):
        if l.get("error"):
            out.append(f"- **{l['name']}**: 这一层没跑成({l['error']})")
            continue
        if not l.get("text"):
            continue
        out += [f"<details>\n<summary><b>第{i+1}份 · {l['name']}</b></summary>\n",
                l["text"], "\n</details>", ""]
    out += ["---", "", f"> {r.get('口径', '')}"]
    return "\n".join(out)


async def stream(code: str, name: str = "", question: str = ""):
    """事件流版本, 挂到 ask_runs 后台跑 —— 一轮实测 3 分半, 同步请求会被浏览器/代理掐断。

    产出的事件跟问答 agent 同一套(step / step_result / answer / done), 所以前端那套
    跟随、断线续拉、历史回看全部复用, 不必为它另写一条通道。
    """
    yield {"type": "step", "tool": "deep_dive", "label": "取共享事实",
           "arg": f"{name or ''}{code}"}
    pack, missing = await _fact_pack(code)
    for m in missing:
        yield {"type": "step_result", "tool": "deep_dive", "ok": False, "err": m}
    if not pack.strip():
        yield {"type": "answer", "text": "深挖没跑起来: fact pack 全部取数失败, 没有可分析的事实"}
        yield {"type": "done"}
        return
    for l in _LENSES:
        yield {"type": "step", "tool": "deep_dive", "label": f"视角·{l['name']}", "arg": "并行"}
    r = await _analyze_with_pack(code, name, question, pack, missing)
    for l in (r.get("各层") or []):
        if l.get("error"):
            yield {"type": "step_result", "tool": "deep_dive", "ok": False,
                   "err": f"{l['name']}: {l['error'][:60]}"}
    yield {"type": "step", "tool": "deep_dive", "label": "挑冲突与证伪条件", "arg": ""}
    yield {"type": "answer", "text": render(r)}
    yield {"type": "done"}


async def analyze(code: str, name: str = "", question: str = "") -> dict:
    """四层并行 + 综合。返回各层原文与综合, 不落库(调用方决定要不要存)。"""
    pack, missing = await _fact_pack(code)
    if not pack.strip():
        return {"可用": False, "note": "fact pack 全部取数失败, 没有可分析的事实",
                "缺口": missing}
    return await _analyze_with_pack(code, name, question, pack, missing)


async def _analyze_with_pack(code: str, name: str, question: str,
                             pack: str, missing: list) -> dict:
    """事实已经取好时的分析主体。抽出来是为了 stream() 不必再取一遍 —— fact pack 有八个
    工具调用, 取两遍既慢又可能拿到两份不一致的事实, 那就破了"四层共享同一份"这条不变式。"""
    from services import llm_client
    head = f"标的: {name or ''} {code}\n用户想知道的: {question or '这门生意的质地'}\n\n"

    async def _one(lens: dict) -> dict:
        sys = _LENS_SYSTEM.format(name=lens["name"], checklist=lens["checklist"])
        try:
            txt = await asyncio.to_thread(
                llm_client.call_claude, head + "## 可用事实(fact pack)\n" + pack,
                sys, _MODEL_LENS, _MAX_TOK_LENS)
        except Exception as e:
            return {"key": lens["key"], "name": lens["name"], "error": str(e)[:200]}
        return {"key": lens["key"], "name": lens["name"], "text": (txt or "").strip()}

    lenses = await asyncio.gather(*[_one(l) for l in _LENSES])
    ok = [l for l in lenses if l.get("text")]
    if not ok:
        return {"可用": False, "note": "四层全部调用失败", "各层": lenses, "缺口": missing}

    body = "\n\n".join(f"## 第{i+1}份 · {l['name']}\n{l['text']}" for i, l in enumerate(ok))
    try:
        synth = await asyncio.to_thread(
            llm_client.call_claude, head + body, _SYNTH_SYSTEM, _MODEL_SYNTH, _MAX_TOK_SYNTH)
    except Exception as e:
        synth = ""
        lenses.append({"key": "综合", "name": "综合", "error": str(e)[:200]})
    return {
        "可用": True, "代码": code, "名称": name,
        "各层": lenses, "综合": (synth or "").strip(),
        "缺口": missing,
        "口径": ("四层看的是**同一份 fact pack**(不是各自取数) —— 所以层与层之间的冲突归因于视角本身, "
                 "不是数据不一致。每层都被要求自报'这条在什么条件下不成立'。"
                 f"共 {len(ok)}/{len(_LENSES)} 层成功" + (f"; 取数缺 {len(missing)} 项" if missing else "") +
                 "。全部为客观信息与结构判断, 不含买卖/仓位/目标价/评分。"),
    }
