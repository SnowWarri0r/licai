"""榜单的概念标签: 清洗 + 聚成"今天钱堆在哪/什么在涨"。

榜单原来只有一级行业(「元件」「通信设备」), 看不出今天是 CPO 在涨还是 PCB 在涨 —— 而轮动
恰恰发生在概念这一层。东财的概念串直接用不了: 实测中天科技挂 47 个, 从「CPO概念/算力概念」
一路到「长江三角/贬值受益/创投」; 不清洗就聚合, 排最前的会是「融资融券」「深股通」这种
全市场都有的标签, 把真主线挤下去。
"""
from services.concept_tags import clean, parse, is_noise, group_rows


# ── 清洗 ────────────────────────────────────────────────

def test_market_state_and_index_tags_are_noise():
    """"融资融券/深股通/沪深300" 说的是这只票的交易资格和指数身份, 不是它做什么。"""
    for nm in ("融资融券", "深股通", "沪深300", "基金重仓", "转债标的", "预盈预增", "百元股"):
        assert is_noise(nm), nm


def test_index_membership_suffix_underscore():
    """东财给指数成分的标签一律带下划线(上证180_/HS300_) —— 拿后缀当判据, 枚举指数名必漏。"""
    assert is_noise("上证180_") and is_noise("央视50_")


def test_geography_and_fx_are_noise():
    """注册地(长江三角)和汇率(贬值受益)全行业通吃, 对"今天什么在轮动"零信息。"""
    for nm in ("长江三角", "西部大开发", "粤港澳", "贬值受益", "升值受益", "次新股"):
        assert is_noise(nm), nm


def test_style_suffixes_are_noise():
    for nm in ("中盘成长", "大盘价值", "科技风格", "昨日涨停", "连板"):
        assert is_noise(nm), nm


def test_real_concepts_survive():
    """供应链概念(华为/英伟达/小米)恰恰是 A 股轮动的主要形式, 不能当噪声删。"""
    keep = ["CPO概念", "光通信模块", "算力概念", "存储芯片", "MLCC", "PCB",
            "华为概念", "英伟达概念", "小米概念", "人形机器人", "可控核聚变"]
    assert clean(keep) == keep


def test_parse_and_dedupe_order():
    out = parse("CPO概念,融资融券,光通信模块,CPO概念,长江三角,算力概念")
    assert out == ["CPO概念", "光通信模块", "算力概念"]      # 去噪 + 去重保序


# ── 聚堆 ────────────────────────────────────────────────

def _r(code, name, pct, amt, concepts, ind="通信设备", limit_ratio=None):
    return {"code": code, "name": name, "pct": pct, "成交额亿": amt,
            "概念": concepts, "行业": ind, "涨停占比%": limit_ratio}


_ROWS = [
    _r("300308", "中际旭创", 1.79, 227.8, ["CPO概念", "光通信模块", "通信技术"]),
    _r("600487", "亨通光电", 9.98, 191.2, ["CPO概念", "光通信模块", "通信技术"], limit_ratio=99.8),
    _r("300502", "新易盛", 2.59, 148.7, ["CPO概念", "光通信模块", "通信技术"]),
    _r("688825", "长鑫科技", 5.38, 188.4, ["存储芯片", "国产芯片"], ind="半导体"),
    _r("603986", "兆易创新", 4.76, 165.4, ["存储芯片", "国产芯片"], ind="半导体"),
    _r("002236", "大华股份", -1.20, 30.0, ["安防"], ind="消费电子"),
]


def _g(groups, name):
    return next((g for g in groups if g["name"] == name), None)


def _gany(groups, *names):
    """同义标签会被并成一行, 主名是成交额最大的那个(同额时看谁先出现) —— 按别名也能找到它。"""
    for g in groups:
        if g["name"] in names or set(names) & set(g.get("aliases") or []):
            return g
    return None


def test_group_counts_money_and_average_move():
    g = _gany(group_rows(_ROWS, "概念"), "光通信模块")
    assert g["n"] == 3
    assert g["amt_yi"] == 567.7                      # 227.8+191.2+148.7
    assert g["avg_pct"] == 4.79                      # (1.79+9.98+2.59)/3
    assert g["limit_n"] == 1                         # 亨通光电封板


def test_group_tops_sorted_by_money():
    """"谁在领"按成交额排 —— 涨得最多的常是小票, 钱在哪才是主线。"""
    g = _gany(group_rows(_ROWS, "概念"), "光通信模块")
    assert [t["name"] for t in g["tops"]] == ["中际旭创", "亨通光电", "新易盛"]


def test_single_stock_tag_is_not_rotation():
    """只有一只票的标签是个股故事, 不是轮动 —— 不成堆。"""
    assert _g(group_rows(_ROWS, "概念"), "安防") is None


def test_groups_sorted_by_money_not_by_count():
    gs = group_rows(_ROWS, "概念")
    assert _gany(gs, "光通信模块") is gs[0]                    # 567.7亿 > 353.8亿
    names = [g["name"] for g in gs]
    assert "存储芯片" in names and names.index("存储芯片") > 0


def test_industry_grouping():
    g = _g(group_rows(_ROWS, "行业"), "半导体")
    assert g["n"] == 2 and g["amt_yi"] == 353.8
    assert _g(group_rows(_ROWS, "行业"), "消费电子") is None    # 只一只


# ── 同义并堆 ────────────────────────────────────────────

def test_synonym_tags_merge_into_one_line():
    """CPO概念/光通信模块/通信技术 在这几只票上是同一堆(Jaccard=1) —— 三行占掉半个榜,
    看着像三条主线其实是一条。并成一行, 别名留着。"""
    gs = group_rows(_ROWS, "概念")
    same = [g for g in gs if g["name"] in ("CPO概念", "光通信模块", "通信技术")]
    assert len(same) == 1
    assert set(same[0]["aliases"]) == {"CPO概念", "光通信模块", "通信技术"} - {same[0]["name"]}


def test_partially_overlapping_tags_stay_separate():
    """存储芯片(2家) vs 国产芯片(3家): 交2并3 = 0.67 < 0.75, 是两条线, 并了就把信息磨平。"""
    rows = _ROWS + [_r("688008", "澜起科技", 10.06, 144.6, ["国产芯片", "算力概念"], ind="半导体")]
    gs = group_rows(rows, "概念")
    assert _g(gs, "存储芯片") is not None and _g(gs, "国产芯片") is not None


def test_merged_group_keeps_the_widest_name():
    """主名留成交额最大的那个(覆盖最全), 其余进 aliases —— 数字不能因为并堆而变小。"""
    rows = [
        _r("A", "甲", 5.0, 100.0, ["宽标签", "窄标签"]),
        _r("B", "乙", 5.0, 100.0, ["宽标签", "窄标签"]),
        _r("C", "丙", 5.0, 100.0, ["宽标签", "窄标签"]),
        _r("D", "丁", 5.0, 100.0, ["宽标签"]),          # 3/4 = 0.75, 刚够并
    ]
    gs = group_rows(rows, "概念")
    assert len(gs) == 1
    assert gs[0]["name"] == "宽标签" and gs[0]["amt_yi"] == 400.0    # 主名的数字是它自己的, 没被并小
    assert gs[0]["aliases"] == ["窄标签"]


def test_codes_returned_for_click_to_filter():
    """界面点一个概念要把榜筛到那条线上, 靠这份 codes 精确匹配(别名也一起并进来了)。"""
    g = _gany(group_rows(_ROWS, "概念"), "光通信模块", "CPO概念")
    assert set(g["codes"]) == {"300308", "600487", "300502"}
