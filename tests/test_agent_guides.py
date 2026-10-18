"""系统提示拆成核心 + 技能包后: 规则一条不丢, 核心不含只属于某类问题的规范, load_guide 可用。"""
import re
from services import agent_guides as G


def test_every_old_section_still_present():
    # 拆分前最后一版 prompt 的全部小节标题(2026-10-13); 拆分只调归属, 不删规则
    import subprocess
    old = subprocess.run(["git", "show", "e8c3884:services/stock_agent.py"],
                         capture_output=True, text=True).stdout
    if not old:
        return
    block = old[old.index("_SYSTEM = ("):]
    block = block[:block.index("\n)\n")]
    titles = set(re.findall(r"【([^】]+)】", block))
    assert len(titles) > 50
    missing = [t for t in titles if f"【{t}】" not in G.ALL_TEXT]
    assert missing == []


def test_core_has_triage_and_not_topic_rules():
    assert "按问题类型决定投入" in G.CORE
    assert "产业链全景" not in G.CORE and "广覆盖" not in G.CORE


def test_load_guide():
    out = G.load_guide(["stock", "holdings"])
    assert set(out["guides"]) == {"stock", "holdings"}
    assert "error" in G.load_guide(["nope"])
    assert G.LOAD_GUIDE_TOOL["input_schema"]["properties"]["topics"]["items"]["enum"] == list(G.GUIDES)
