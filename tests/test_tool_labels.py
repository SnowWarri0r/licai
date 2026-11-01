"""每个 agent 工具都要有中文标签: 前端步骤条显示 _TOOL_CN 里的名字, 漏登记就会露出英文工具名。"""
import services.stock_agent as sa


def test_every_tool_has_cn_label():
    names = [t["name"] for t in sa._TOOLS if t.get("name")]
    assert [n for n in names if n not in sa._TOOL_CN] == []
